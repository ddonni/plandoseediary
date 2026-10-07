"""API 테스트 — 진짜 Supabase 대신 메모리 위의 가짜 DB로 돌린다.
실행: python -m pytest -q
"""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
import index  # noqa: E402


CHILD_TABLES = ("tasks", "logs", "reviews", "plan_revisions", "task_completions")


class FakeDb:
    """Supabase 클래스와 같은 메서드를 가진 메모리 DB (외래키·연쇄삭제·주인 상속 트리거 흉내)."""

    def __init__(self):
        self.t = {"plans": [], "tasks": [], "logs": [], "reviews": [], "plan_revisions": [],
                  "task_completions": [], "request_keys": [],
                  "users": [], "sessions": [], "login_attempts": []}
        self.ids = itertools.count(1)
        self.clock = None          # 정해 두면 '지금'(created_at·updated_at)으로 쓴다 — 5일 기록 순서 시험용

    def _match(self, row, eq):
        return all(row.get(k) == v for k, v in eq.items())

    @staticmethod
    def _cond(v, cond):
        op, val = cond.split(".", 1)
        sv = str(v).lower() if isinstance(v, bool) else str(v)
        if op == "eq":
            return v is not None and sv == val
        if op == "is":
            return v is None
        if v is None:
            return False
        return {"lt": sv < val, "gt": sv > val, "gte": sv >= val, "lte": sv <= val}[op]

    def _where(self, row, params):
        return all(self._cond(row.get(k), c) for k, c in params.items() if k not in ("select", "limit", "order"))

    def select(self, table, order="id", **eq):
        return [dict(r) for r in self.t[table] if self._match(r, eq)]

    def select_where(self, table, params, order="id"):
        rows = [dict(r) for r in self.t[table] if self._where(r, params)]
        return rows[: int(params["limit"])] if "limit" in params else rows

    def _owner_of(self, plan_id):
        return next((p.get("user_id") for p in self.t["plans"] if p["id"] == plan_id), None)

    def insert(self, table, row):
        if table == "logs" and row["task_id"] is not None:
            task = next(t for t in self.t["tasks"] if t["id"] == row["task_id"])
            if task["plan_id"] != row["plan_id"]:
                raise index.ApiError(*index.PG_ERRORS["23503"])
        if table in ("users",) and any(u["email"] == row["email"] for u in self.t["users"]):
            raise index.ApiError(*index.PG_ERRORS["23505"])
        row = {"id": next(self.ids), **row}
        if table in CHILD_TABLES:                      # inherit_plan_owner 트리거 흉내
            row["user_id"] = self._owner_of(row["plan_id"])
        if table == "tasks":
            for k, v in (("due_date", None), ("priority", "medium"), ("tags", []),
                         ("status", "open"), ("done_at", None), ("deleted_at", None)):
                row.setdefault(k, v)
        if table == "plans":
            row.setdefault("from_review_id", None)
            row.setdefault("goal", None)
            row.setdefault("rule", None)
            row.setdefault("question", None)
            row.update(version=1, change_note=None,
                       updated_at=self.clock or f"2026-10-0{1 + row['id'] % 8}T00:00:00+00:00")
        if table == "logs":                            # created_at 기본값 흉내: 지금(=기록을 남긴 시각)
            row.setdefault("created_at", self.clock or row.get("ended_at") or "2026-10-02T00:00:00+00:00")
        self.t[table].append(row)
        if table == "tasks":
            self._completion_events(None, row)
        return dict(row)

    def delete_where(self, table, params):
        gone = [r["id"] for r in self.t[table] if self._where(r, params)]
        for i in gone:
            self.delete(table, id=i)
        return len(gone)

    def insert_ignore(self, table, row, on_conflict):
        if any(r[on_conflict] == row[on_conflict] for r in self.t[table]):
            return None
        return self.insert(table, dict(row))

    def _completion_events(self, old, new):
        """tasks_completion_events 트리거 흉내: 진행 중→완료로 '바뀔 때만' 완료 기록 1건"""
        active = [c for c in self.t["task_completions"]
                  if c["task_id"] == new["id"] and c["revoked_at"] is None]
        if new["status"] == "done" and (old is None or old["status"] != "done") and not active:
            self.t["task_completions"].append({"id": next(self.ids), "task_id": new["id"],
                                               "plan_id": new["plan_id"], "user_id": new.get("user_id"),
                                               "completed_at": "2026-10-02T03:00:00+00:00",
                                               "revoked_at": None})
        elif old is not None and old["status"] == "done" and new["status"] == "open":
            for c in active:
                c["revoked_at"] = "2026-10-02T04:00:00+00:00"

    def update(self, table, row, **eq):
        """plans_keep_history 트리거 흉내: 내용이 바뀌면 옛 값을 이력에 쌓고 버전+1"""
        out = []
        for r in self.t[table]:
            if not self._match(r, eq):
                continue
            new = {**r, **row}
            if table == "plans" and any(new.get(k) != r.get(k) for k in index.PLAN_FIELDS):
                self.t["plan_revisions"].append({
                    "id": next(self.ids), "plan_id": r["id"], "user_id": r.get("user_id"),
                    **{k: r.get(k) for k in (*index.PLAN_FIELDS, "version", "change_note")},
                    "valid_from": r["updated_at"], "replaced_at": "now"})
                new.update(version=r["version"] + 1,
                           updated_at=self.clock or f"2026-10-0{min(9, r['version'] + 2)}T05:00:00+00:00")
            elif table == "plans":
                new.update(version=r["version"], change_note=r["change_note"])
            old = dict(r)
            r.update(new)
            if table == "tasks":
                self._completion_events(old, r)
            out.append(dict(r))
        return out

    def upsert(self, table, row, on_conflict):
        for r in self.t[table]:
            if r[on_conflict] == row[on_conflict]:
                r.update(row)
                return dict(r)
        return self.insert(table, row)

    def delete(self, table, **eq):
        gone = [r for r in self.t[table] if self._match(r, eq)]
        self.t[table] = [r for r in self.t[table] if not self._match(r, eq)]
        for r in gone:  # on delete cascade
            if table == "plans":
                for child in CHILD_TABLES:
                    self.delete(child, plan_id=r["id"])
            if table == "tasks":
                self.delete("logs", task_id=r["id"])
                self.delete("task_completions", task_id=r["id"])
            if table == "users":
                self.delete("sessions", user_id=r["id"])
                self.delete("plans", user_id=r["id"])
        return len(gone)


PASSWORD = "correct-horse-battery"   # 테스트 전용 값


def new_client(email="a@example.com"):
    """가입·로그인까지 마친 테스트 클라이언트 (쿠키가 저장된다)."""
    cl = index.app.test_client()
    cl.environ_base["HTTP_X_REQUESTED_WITH"] = "pds"
    r = cl.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.json
    return cl


@pytest.fixture
def c():
    index.app.config["DB"] = FakeDb()
    index.app.config["TESTING"] = True
    yield new_client()
    index.app.config.pop("DB")


PLAN = {"title": "10월 1주", "start_date": "2026-10-01", "end_date": "2026-10-07",
        "priority": "high", "success_criteria": "카드 1~3 통과", "estimated_minutes": 600}


def make_plan(c, **kw):
    data = {**PLAN, **kw}
    r = c.post("/api/plans", json=data)
    assert r.status_code == 201, r.json
    return r.json["id"]


def make_task(c, pid, minutes, title="할 일"):
    r = c.post(f"/api/plans/{pid}/tasks", json={"title": title, "planned_minutes": minutes})
    assert r.status_code == 201, r.json
    return r.json["id"]


def times(minutes, start="2026-10-02T09:00:00+09:00"):
    from datetime import datetime, timedelta
    s = datetime.fromisoformat(start)
    return {"started_at": s.isoformat(), "ended_at": (s + timedelta(minutes=max(minutes, 1))).isoformat()}


def log(c, pid, minutes, status="done", task_id=None, note=None):
    if status == "skipped":   # 화면에서는 더 이상 만들지 않는 예전 상태 — DB에 직접 넣어 집계만 시험
        return index.app.config["DB"].insert("logs", {
            "plan_id": pid, "task_id": task_id, "done_date": "2026-10-02",
            "actual_minutes": minutes, "status": "skipped", "note": note, "blocker": None})["id"]
    body = {**times(minutes), "actual_minutes": minutes, "note": note}
    if task_id is None:
        r = c.post(f"/api/plans/{pid}/logs", json=body)
    else:
        r = c.post(f"/api/tasks/{task_id}/logs", json={**body, "complete": status == "done"})
    assert r.status_code == 201, r.json
    return r.json["id"]


def test_health(c):
    assert c.get("/api/health").json == {"ok": True, "db": "connected"}


def test_plan_validation(c):
    bad = [
        {},
        {**PLAN, "title": ""},
        {**PLAN, "start_date": "2026-13-01"},
        {**PLAN, "start_date": "2026-10-09"},
        {**PLAN, "title": "x" * 101},
        {**PLAN, "priority": "urgent"},
        {**PLAN, "success_criteria": "  "},
        {**PLAN, "estimated_minutes": 0},
        {k: v for k, v in PLAN.items() if k != "estimated_minutes"},
    ]
    for data in bad:
        r = c.post("/api/plans", json=data)
        assert r.status_code == 400 and "error" in r.json, data
    assert c.post("/api/plans", data="not json").status_code == 400


def test_task_and_log_validation(c):
    pid = make_plan(c)
    assert c.post(f"/api/plans/{pid}/tasks", json={"title": "a", "planned_minutes": 0}).status_code == 400
    assert c.post(f"/api/plans/{pid}/tasks", json={"title": "a", "planned_minutes": True}).status_code == 400
    assert c.post("/api/plans/999/tasks", json={"title": "a", "planned_minutes": 10}).status_code == 404
    # 계획에 없던 일은 메모가 필수
    r = c.post(f"/api/plans/{pid}/logs", json={**times(10), "actual_minutes": 10})
    assert r.status_code == 400


def test_log_cannot_point_to_other_plans_task(c):
    p1, p2 = make_plan(c), make_plan(c, title="다른 계획")
    t1 = make_task(c, p1, 30)
    r = c.post(f"/api/plans/{p2}/logs", json={"task_id": t1, **times(10), "note": "x"})
    assert r.status_code == 400   # 할 일 기록은 할 일 주소로만 — 계획은 할 일에서 정해짐


def test_verdicts():
    v = index.verdict
    L = lambda m, s="done": {"actual_minutes": m, "status": s}
    assert v(60, [], True) == "pending"
    assert v(60, [L(90)], True) == "over"
    assert v(60, [L(80, "partial")], False) == "over"    # 안 끝났는데 이미 초과
    assert v(60, [L(30)], True) == "under"
    assert v(60, [L(60)], True) == "on_track"
    assert v(60, [L(72)], True) == "on_track"            # 경계: 정확히 +20%
    assert v(60, [L(20, "partial")], False) == "in_progress"
    assert v(60, [L(0, "skipped")], False) == "skipped"
    assert v(60, [L(60)], False) == "in_progress"        # 시간은 맞아도 체크 안 했으면 진행 중


def test_full_cycle_and_drilldown_counts_match(c):
    pid = make_plan(c)
    t_over = make_task(c, pid, 60, "보고서")
    t_under = make_task(c, pid, 60, "운동")
    t_ok = make_task(c, pid, 30, "정리")
    t_skip = make_task(c, pid, 30, "독서")
    make_task(c, pid, 30, "미착수")
    log(c, pid, 50, "partial", t_over)
    log(c, pid, 50, "done", t_over)
    log(c, pid, 20, "done", t_under)
    log(c, pid, 30, "done", t_ok)
    log(c, pid, 0, "skipped", t_skip)
    log(c, pid, 45, "done", None, "갑자기 생긴 서류 작업")
    for tid in (t_over, t_under, t_ok):
        assert c.patch(f"/api/tasks/{tid}", json={"status": "done"}).status_code == 200

    d = c.get(f"/api/plans/{pid}").json
    s = d["summary"]
    assert (s["tasks"], s["done"], s["over"], s["under"], s["on_track"],
            s["skipped"], s["pending"], s["unplanned"]) == (5, 3, 1, 1, 1, 1, 1, 1)
    assert s["planned"] == 210 and s["actual"] == 195 and s["unplanned_minutes"] == 45

    # 집계 숫자 == 눌렀을 때 나오는 근거 개수
    task_kinds = ["tasks", "done", "over", "under", "on_track", "skipped", "pending", "in_progress"]
    for kind in task_kinds:
        r = c.get(f"/api/records?kind={kind}&plan_id={pid}").json
        assert len(r["tasks"]) == s[kind], kind
    r = c.get(f"/api/records?kind=unplanned&plan_id={pid}").json
    assert len(r["logs"]) == s["unplanned"] and r["logs"][0]["note"] == "갑자기 생긴 서류 작업"
    r = c.get(f"/api/records?kind=actual&plan_id={pid}").json
    assert sum(l["actual_minutes"] for l in r["logs"]) == s["actual"]
    r = c.get(f"/api/records?kind=planned&plan_id={pid}").json
    assert sum(t["planned_minutes"] for t in r["tasks"]) == s["planned"]
    r = c.get(f"/api/records?kind=over&plan_id={pid}").json
    assert [t["title"] for t in r["tasks"]] == ["보고서"] and len(r["logs"]) == 2

    assert c.get("/api/records?kind=nope").status_code == 400
    assert c.get("/api/records?kind=over&plan_id=abc").status_code == 400


def test_review_feeds_next_plan(c):
    p1 = make_plan(c)
    r = c.put(f"/api/plans/{p1}/review", json={"miss_pattern": "underestimate",
                                                "lesson": "글쓰기는 1.5배로 잡기"})
    assert r.status_code == 200
    rid = r.json["id"]
    # 다시 쓰면 덮어쓰기 (계획당 하나)
    r = c.put(f"/api/plans/{p1}/review", json={"miss_pattern": "unplanned", "lesson": "여유 30분 남기기"})
    assert r.json["id"] == rid and r.json["lesson"] == "여유 30분 남기기"
    assert c.put(f"/api/plans/{p1}/review", json={"miss_pattern": "x", "lesson": "a"}).status_code == 400

    p2 = make_plan(c, title="10월 2주", start_date="2026-10-08", end_date="2026-10-14", from_review_id=rid)
    d = c.get(f"/api/plans/{p2}").json
    assert d["source_review"]["lesson"] == "여유 30분 남기기"
    assert d["source_review"]["plan_title"] == "10월 1주"
    assert c.post("/api/plans", json={**PLAN, "from_review_id": 999}).status_code == 404

    s = c.get("/api/stats").json
    assert s["review_patterns"]["unplanned"] == 1
    r = c.get("/api/records?kind=review:unplanned").json
    assert len(r["reviews"]) == 1


def test_delete_cascades_and_404(c):
    pid = make_plan(c)
    tid = make_task(c, pid, 30)
    log(c, pid, 30, "done", tid)
    assert c.delete(f"/api/tasks/{tid}").status_code == 200   # 휴지통으로
    assert c.get(f"/api/plans/{pid}").json["logs"] == []
    assert c.delete(f"/api/plans/{pid}").status_code == 204
    assert c.get(f"/api/plans/{pid}").status_code == 404
    assert c.delete("/api/logs/12345").status_code == 404
    assert c.get("/api/unknown").status_code == 404


def test_missing_env_message_does_not_leak(monkeypatch):
    index.app.config.pop("DB", None)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    r = index.app.test_client().get("/api/health")
    assert r.status_code == 500
    assert "SUPABASE_URL" in r.json["error"] and "SUPABASE_SECRET_KEY" in r.json["error"]
    index.app.config.pop("DB", None)


def test_supabase_headers_and_error_mapping(monkeypatch):
    new = index.Supabase("https://x.supabase.co/", "sb_secret_abc")
    assert new.headers == {"apikey": "sb_secret_abc", "Content-Type": "application/json"}
    old = index.Supabase("https://x.supabase.co", "eyJhbGciOi")
    assert old.headers["Authorization"] == "Bearer eyJhbGciOi"
    assert new._filters({"task_id": None, "id": 3}) == {"task_id": "is.null", "id": "eq.3"}

    class Resp:
        status_code, content, text = 400, b"x", "x"
        def json(self):
            return {"code": "23514", "message": "secret detail"}

    monkeypatch.setattr(index.requests, "request", lambda *a, **k: Resp())
    with index.app.app_context(), pytest.raises(index.ApiError) as e:
        new.insert("plans", {})
    assert e.value.status == 400 and "secret" not in e.value.message


# ---------------- 카드 1: 계획 세우기 ----------------
def test_card1_plan_fields_saved(c):
    pid = make_plan(c)
    p = c.get(f"/api/plans/{pid}").json["plan"]
    assert (p["start_date"], p["end_date"]) == ("2026-10-01", "2026-10-07")   # T06-C04 기간
    assert p["priority"] == "high"                                            # T06-C05 우선순위
    assert p["success_criteria"] == "카드 1~3 통과"                            # T06-C06 성공 기준
    assert p["estimated_minutes"] == 600                                      # T06-C07 예상 시간
    assert p["version"] == 1


def test_card1_edit_keeps_original(c):                                       # T06-C08
    pid = make_plan(c)
    r = c.patch(f"/api/plans/{pid}", json={"estimated_minutes": 720, "change_note": "API가 더 걸림"})
    assert r.status_code == 200 and r.json["changed"] is True
    assert r.json["plan"]["id"] == pid and r.json["plan"]["version"] == 2
    c.patch(f"/api/plans/{pid}", json={"end_date": "2026-10-09", "priority": "medium",
                                       "change_note": "마감 연장"})
    v = c.get(f"/api/plans/{pid}/revisions").json["versions"]
    assert [x["version"] for x in v] == [1, 2, 3]
    assert v[0]["estimated_minutes"] == 600 and v[0]["end_date"] == "2026-10-07"  # 처음 계획 그대로
    assert v[1]["change_note"] == "API가 더 걸림" and v[1]["estimated_minutes"] == 720
    assert v[2]["priority"] == "medium" and v[2]["replaced_at"] is None          # 현재
    assert c.get(f"/api/plans/{pid}").json["plan"]["id"] == pid


def test_card1_edit_validation(c):
    pid = make_plan(c)
    # 변화 없으면 이력도 안 쌓이고 사유도 필요 없음
    r = c.patch(f"/api/plans/{pid}", json={"title": "10월 1주"})
    assert r.status_code == 200 and r.json["changed"] is False
    assert len(c.get(f"/api/plans/{pid}/revisions").json["versions"]) == 1
    assert c.patch(f"/api/plans/{pid}", json={"title": "새 제목"}).status_code == 400      # 사유 없음
    assert c.patch(f"/api/plans/{pid}", json={"end_date": "2026-09-01", "change_note": "x"}).status_code == 400
    assert c.patch(f"/api/plans/{pid}", json={}).status_code == 400
    assert c.patch(f"/api/plans/{pid}", json={"version": 9}).status_code == 400            # 버전은 못 바꿈
    assert c.patch("/api/plans/999", json={"title": "x", "change_note": "x"}).status_code == 404


def test_supabase_url_normalized_and_404_message(monkeypatch):
    for url in ("https://x.supabase.co", "https://x.supabase.co/", "https://x.supabase.co/rest/v1/",
                " https://x.supabase.co/rest/v1 "):
        assert index.Supabase(url, " sb_secret_k\n").base == "https://x.supabase.co/rest/v1/"
    assert index.Supabase("https://x.supabase.co", " sb_secret_k\n").headers["apikey"] == "sb_secret_k"

    class Resp:
        status_code, content, text = 404, b"x", "x"
        def json(self):
            return {"code": "PGRST205", "message": "Could not find the table"}

    monkeypatch.setattr(index.requests, "request", lambda *a, **k: Resp())
    with index.app.app_context(), pytest.raises(index.ApiError) as e:
        index.Supabase("https://x.supabase.co", "k").select("plans")
    assert e.value.status == 502 and "'plans' 표가 없습니다" in e.value.message and "PGRST205" in e.value.message


# ---------------- 카드 2: 할 일 다루기 ----------------
def add(c, pid, **kw):
    data = {"title": "할 일", "planned_minutes": 60, **kw}
    r = c.post(f"/api/plans/{pid}/tasks", json=data)
    assert r.status_code == 201, r.json
    return r.json


def listing(c, pid, **params):
    r = c.get(f"/api/plans/{pid}/tasks", query_string=params)
    assert r.status_code == 200, r.json
    return r.json


def test_card2_create_with_fields(c):                                         # C09, C14~C17
    pid = make_plan(c)
    t = add(c, pid, title="API 만들기", due_date="2026-10-02", priority="high",
            tags=["백엔드", " API ", "백엔드", "#Python"], planned_minutes=120)
    assert (t["title"], t["due_date"], t["priority"], t["planned_minutes"]) == \
           ("API 만들기", "2026-10-02", "high", 120)
    assert t["tags"] == ["백엔드", "API", "Python"]        # 공백·중복·# 정리
    assert t["status"] == "open"
    t2 = add(c, pid, tags="화면, 디자인")                     # 쉼표 글자도 받음
    assert t2["tags"] == ["화면", "디자인"] and t2["priority"] == "medium" and t2["due_date"] is None


def test_card2_validation(c):
    pid = make_plan(c)
    for bad in ({"title": ""}, {"priority": "urgent"}, {"due_date": "10/02"},
                {"tags": ["x" * 21]}, {"tags": [str(i) for i in range(11)]}, {"tags": [1]},
                {"planned_minutes": 0}):
        r = c.post(f"/api/plans/{pid}/tasks", json={"title": "a", "planned_minutes": 30, **bad})
        assert r.status_code == 400, bad
    tid = add(c, pid)["id"]
    assert c.patch(f"/api/tasks/{tid}", json={"status": "finished"}).status_code == 400
    assert c.patch(f"/api/tasks/{tid}", json={}).status_code == 400
    assert c.patch("/api/tasks/9999", json={"title": "x"}).status_code == 404


def test_card2_edit_done_undo_delete(c):                                     # C10~C13
    pid = make_plan(c)
    tid = add(c, pid, title="초안")["id"]
    r = c.patch(f"/api/tasks/{tid}", json={"title": "고친 제목", "tags": ["수정됨"],
                                           "due_date": "2026-10-03", "priority": "low"})
    assert r.json["title"] == "고친 제목" and r.json["tags"] == ["수정됨"] and r.json["priority"] == "low"
    r = c.patch(f"/api/tasks/{tid}", json={"due_date": None})              # 마감일 지우기
    assert r.json["due_date"] is None
    r = c.patch(f"/api/tasks/{tid}", json={"status": "done"})
    assert r.json["status"] == "done" and r.json["done_at"]
    first_done = r.json["done_at"]
    r = c.patch(f"/api/tasks/{tid}", json={"status": "done"})              # 다시 눌러도 시각 유지
    assert r.json["done_at"] == first_done
    r = c.patch(f"/api/tasks/{tid}", json={"status": "open"})
    assert r.json["status"] == "open" and r.json["done_at"] is None
    assert c.delete(f"/api/tasks/{tid}").status_code == 200   # 휴지통으로
    assert listing(c, pid)["total"] == 0


def seed(c, pid):
    rows = [
        dict(title="DB 설계", due_date="2026-10-01", priority="high", tags=["백엔드"], planned_minutes=60),
        dict(title="API 만들기", due_date="2026-10-02", priority="high", tags=["백엔드", "API"], planned_minutes=120),
        dict(title="화면 만들기", due_date="2026-10-02", priority="medium", tags=["화면"], planned_minutes=120),
        dict(title="배포", due_date="2026-10-02", priority="high", tags=["배포"], planned_minutes=30),
        dict(title="README", priority="low", tags=["문서"], planned_minutes=30),
        dict(title="api 테스트", due_date="2026-10-03", priority="medium", tags=["백엔드"], planned_minutes=60),
    ]
    ids = [add(c, pid, **r)["id"] for r in rows]
    c.patch(f"/api/tasks/{ids[0]}", json={"status": "done"})
    return ids


def titles(res):
    return [t["title"] for t in res["tasks"]]


def test_card2_search(c):                                                     # C18
    pid = make_plan(c)
    seed(c, pid)
    assert titles(listing(c, pid, q="api")) == ["API 만들기", "api 테스트"]   # 대소문자 무시
    assert set(titles(listing(c, pid, q="백엔드"))) == {"DB 설계", "API 만들기", "api 테스트"}  # 태그도 찾음
    r = listing(c, pid, q="없는말")
    assert r["shown"] == 0 and r["total"] == 6


def test_card2_filters(c, monkeypatch):                                      # C19
    pid = make_plan(c)
    seed(c, pid)
    assert titles(listing(c, pid, status="done")) == ["DB 설계"]
    assert len(listing(c, pid, status="open")["tasks"]) == 5
    assert set(titles(listing(c, pid, priority="high"))) == {"DB 설계", "API 만들기", "배포"}
    assert set(titles(listing(c, pid, tag="백엔드", status="open"))) == {"API 만들기", "api 테스트"}
    assert titles(listing(c, pid, due="none")) == ["README"]

    class FixedDT(index.datetime):                                            # '오늘' = 10월 2일 (한국)
        @classmethod
        def now(cls, tz=None):
            return index.datetime(2026, 10, 2, 9, 0, tzinfo=index.KST).astimezone(tz)
    monkeypatch.setattr(index, "datetime", FixedDT)
    assert titles(listing(c, pid, due="today")) == ["API 만들기", "배포", "화면 만들기"]
    assert listing(c, pid, due="overdue")["shown"] == 0                      # 10/1 건은 완료라 제외
    c.patch(f"/api/tasks/{listing(c, pid, q='DB')['tasks'][0]['id']}", json={"status": "open"})
    assert titles(listing(c, pid, due="overdue")) == ["DB 설계"]
    assert "README" not in titles(listing(c, pid, due="week"))
    assert listing(c, pid)["tags"] == ["API", "문서", "배포", "백엔드", "화면"]

    for bad in ({"status": "x"}, {"priority": "x"}, {"due": "x"}, {"sort": "x"}):
        assert c.get(f"/api/plans/{pid}/tasks", query_string=bad).status_code == 400


def test_card2_sort_matches_stated_rule_and_is_stable(c):                    # C20
    pid = make_plan(c)
    seed(c, pid)
    r = listing(c, pid, sort="due")
    # 마감일 → 같으면 우선순위 → 같으면 먼저 만든 순, 마감일 없음은 맨 뒤
    assert titles(r) == ["DB 설계", "API 만들기", "배포", "화면 만들기", "api 테스트", "README"]
    assert r["sort"]["steps"][0].startswith("마감일") and r["sort"]["label"] == "마감일 빠른 순"
    assert titles(listing(c, pid, sort="priority")) == \
        ["DB 설계", "API 만들기", "배포", "화면 만들기", "api 테스트", "README"]
    assert titles(listing(c, pid, sort="minutes"))[:2] == ["API 만들기", "화면 만들기"]
    assert titles(listing(c, pid, sort="title")) == \
        ["API 만들기", "api 테스트", "DB 설계", "README", "배포", "화면 만들기"]
    assert titles(listing(c, pid, sort="recent"))[0] == "api 테스트"
    # 같은 요청은 몇 번을 해도 같은 순서
    for sort in index.SORTS:
        first = titles(listing(c, pid, sort=sort))
        assert all(titles(listing(c, pid, sort=sort)) == first for _ in range(5)), sort


# ---------------- 카드 3: 실제로 한 일 적기 ----------------
import uuid


def test_card3_log_fields_saved_and_attached(c):                              # C23~C26
    pid = make_plan(c)
    tid = make_task(c, pid, 60, "API 만들기")
    r = c.post(f"/api/tasks/{tid}/logs", json={
        "started_at": "2026-10-01T14:00:00+09:00", "ended_at": "2026-10-01T15:30:00+09:00",
        "actual_minutes": 75, "blocker": "Supabase 키 위치를 못 찾음"})
    assert r.status_code == 201, r.json
    log = r.json
    assert log["started_at"].startswith("2026-10-01T14:00:00+09:00")          # C23
    assert log["ended_at"].startswith("2026-10-01T15:30:00+09:00")            # C24
    assert log["actual_minutes"] == 75                                       # C25
    assert log["blocker"] == "Supabase 키 위치를 못 찾음"                       # C26
    assert log["task_id"] == tid and log["plan_id"] == pid and log["done_date"] == "2026-10-01"
    # 실제 시간을 안 적으면 시작~끝 사이 시간
    r = c.post(f"/api/tasks/{tid}/logs", json={"started_at": "2026-10-01T23:30:00+09:00",
                                                "ended_at": "2026-10-02T00:10:00+09:00"})
    assert r.json["actual_minutes"] == 40 and r.json["done_date"] == "2026-10-02"


def test_card3_log_validation(c):
    pid = make_plan(c)
    tid = make_task(c, pid, 60)
    bad = [
        {},
        {"started_at": "2026-10-01T14:00:00", "ended_at": "2026-10-01T15:00:00"},           # 시간대 없음
        {"started_at": "2026-10-01T15:00:00+09:00", "ended_at": "2026-10-01T14:00:00+09:00"},  # 거꾸로
        {"started_at": "2026-10-01T10:00:00+09:00", "ended_at": "2026-10-02T11:00:00+09:00"},  # 24시간 초과
        {"started_at": "2026-10-01T14:00:00+09:00", "ended_at": "2026-10-01T15:00:00+09:00",
         "actual_minutes": 90},                                                                 # 사이보다 김
        {**times(30), "blocker": "x" * 501},
        {**times(30), "complete": "yes"},
    ]
    for d in bad:
        assert c.post(f"/api/tasks/{tid}/logs", json=d).status_code == 400, d
    assert c.post("/api/tasks/999/logs", json=times(10)).status_code == 404


def test_card3_log_does_not_overwrite_plan(c):                                # C27
    pid = make_plan(c)
    tid = make_task(c, pid, 60, "API")
    before_plan = c.get(f"/api/plans/{pid}").json["plan"]
    before_task = next(t for t in c.get(f"/api/plans/{pid}").json["tasks"] if t["id"] == tid)
    c.post(f"/api/tasks/{tid}/logs", json={**times(150, "2026-10-01T09:00:00+09:00"),
                                            "blocker": "예상보다 복잡", "complete": True})
    after = c.get(f"/api/plans/{pid}").json
    after_task = next(t for t in after["tasks"] if t["id"] == tid)
    assert after["plan"] == before_plan                                       # 계획 그대로 (버전도 그대로)
    assert after_task["planned_minutes"] == before_task["planned_minutes"] == 60  # 예상 시간 그대로
    assert after_task["actual_minutes"] == 150 and after_task["verdict"] == "over"
    assert len(c.get(f"/api/plans/{pid}/revisions").json["versions"]) == 1


def completions_of(c, pid):
    return c.get(f"/api/plans/{pid}").json["summary"]["completions"]


def test_card3_double_complete_same_key(c):                                   # C21, C22
    pid = make_plan(c)
    tid = make_task(c, pid, 60)
    before = completions_of(c, pid)
    key = str(uuid.uuid4())
    r1 = c.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers={"Idempotency-Key": key})
    r2 = c.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers={"Idempotency-Key": key})
    assert r1.status_code == r2.status_code == 200 and r1.json == r2.json
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert completions_of(c, pid) == before + 1


def test_card3_double_complete_different_keys_db_guard(c):                    # 키가 달라도 DB가 막음
    pid = make_plan(c)
    tid = make_task(c, pid, 60)
    for _ in range(3):
        c.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert completions_of(c, pid) == 1
    # 되돌리면 취소, 다시 완료하면 다시 1 (취소된 기록은 집계에서 빠짐)
    c.patch(f"/api/tasks/{tid}", json={"status": "open"})
    assert completions_of(c, pid) == 0
    c.patch(f"/api/tasks/{tid}", json={"status": "done"})
    assert completions_of(c, pid) == 1


def test_card3_double_save_log_with_complete(c):                              # 기록 저장 연타
    pid = make_plan(c)
    tid = make_task(c, pid, 60)
    key = str(uuid.uuid4())
    body = {**times(50), "complete": True, "blocker": None}
    r1 = c.post(f"/api/tasks/{tid}/logs", json=body, headers={"Idempotency-Key": key})
    r2 = c.post(f"/api/tasks/{tid}/logs", json=body, headers={"Idempotency-Key": key})
    assert r1.status_code == r2.status_code == 201 and r1.json["id"] == r2.json["id"]
    s = c.get(f"/api/plans/{pid}").json["summary"]
    assert s["logs"] == 1 and s["completions"] == 1
    # 이미 완료된 할 일에 '완료로 저장' 기록을 하나 더 → 기록은 늘지만 완료 수는 그대로
    c.post(f"/api/tasks/{tid}/logs", json={**body, **times(10, "2026-10-02T12:00:00+09:00")})
    s = c.get(f"/api/plans/{pid}").json["summary"]
    assert s["logs"] == 2 and s["completions"] == 1


def test_card3_idempotency_key_rules(c):
    pid = make_plan(c)
    tid = make_task(c, pid, 60)
    assert c.patch(f"/api/tasks/{tid}", json={"status": "done"},
                   headers={"Idempotency-Key": "not-a-uuid"}).status_code == 400
    key = str(uuid.uuid4())
    c.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers={"Idempotency-Key": key})
    r = c.post(f"/api/tasks/{tid}/logs", json=times(10), headers={"Idempotency-Key": key})
    assert r.status_code == 422                                # 다른 요청에 같은 키
    # 실패한 요청의 키는 풀려서, 고쳐서 다시 보낼 수 있다
    key2 = str(uuid.uuid4())
    assert c.post(f"/api/tasks/{tid}/logs", json={}, headers={"Idempotency-Key": key2}).status_code == 400
    assert c.post(f"/api/tasks/{tid}/logs", json=times(10), headers={"Idempotency-Key": key2}).status_code == 201


def test_card3_blocked_drilldown(c):
    pid = make_plan(c)
    tid = make_task(c, pid, 60)
    c.post(f"/api/tasks/{tid}/logs", json={**times(30), "blocker": "환경변수 오타"})
    c.post(f"/api/tasks/{tid}/logs", json=times(20, "2026-10-02T13:00:00+09:00"))
    s = c.get(f"/api/plans/{pid}").json["summary"]
    assert s["blocked"] == 1
    r = c.get(f"/api/records?kind=blocked&plan_id={pid}").json
    assert [l["blocker"] for l in r["logs"]] == ["환경변수 오타"]


# ---------------- 카드 4: 돌아보기, 그리고 다음 계획으로 ----------------
from datetime import date as _date


def review(c, pid, kind=None):
    return c.get(f"/api/plans/{pid}/review" + (f"?kind={kind}" if kind else "")).json


def seed_review(c, monkeypatch):
    monkeypatch.setattr(index, "today_kst", lambda: _date(2026, 10, 2))   # 서울 기준 오늘 = 10/2
    pid = make_plan(c)
    mk = lambda title, due, minutes: c.post(f"/api/plans/{pid}/tasks", json={
        "title": title, "due_date": due, "planned_minutes": minutes}).json["id"]
    a = mk("완료+막힘+초과", "2026-09-30", 60)     # 마감 지났지만 완료 → 지연 아님
    b = mk("지연+막힘", "2026-10-01", 30)          # 미완료 + 마감 지남 → 지연
    d = mk("오늘 마감", "2026-10-02", 45)          # 오늘 마감은 지연 아님
    e = mk("마감 없음", None, 20)                  # 기록 없음
    c.post(f"/api/tasks/{a}/logs", json={"started_at": "2026-10-01T09:00:00+09:00", "ended_at": "2026-10-01T10:00:00+09:00",
                                          "blocker": "키 위치 헷갈림"})
    c.post(f"/api/tasks/{a}/logs", json={"started_at": "2026-10-01T11:00:00+09:00", "ended_at": "2026-10-01T11:30:00+09:00",
                                          "complete": True, "blocker": "  "})        # 공백 막힘은 막힘 아님
    c.post(f"/api/tasks/{b}/logs", json={"started_at": "2026-10-01T13:00:00+09:00", "ended_at": "2026-10-01T13:20:00+09:00",
                                          "blocker": "배포 502"})
    c.post(f"/api/tasks/{d}/logs", json={"started_at": "2026-10-01T14:00:00+09:00", "ended_at": "2026-10-01T14:10:00+09:00"})
    c.post(f"/api/plans/{pid}/logs", json={"started_at": "2026-10-01T15:00:00+09:00", "ended_at": "2026-10-01T15:40:00+09:00",
                                            "note": "계획에 없던 회의"})                 # 할 일에 안 붙은 기록
    return pid, (a, b, d, e)


def test_card4_counts(c, monkeypatch):                                         # C28~C32
    pid, (a, b, d, e) = seed_review(c, monkeypatch)
    s = review(c, pid)["summary"]
    assert s["tasks"] == 4                                                     # C28
    assert s["done"] == 1                                                      # C29
    assert s["delayed"] == 1                                                   # C30 (완료한 a는 제외, 오늘 마감 d 제외)
    assert s["blocked"] == 2                                                   # C31 (할 일 수: a, b — 기록 수 아님)
    assert s["planned"] == 60 + 30 + 45 + 20                                   # C32
    assert s["actual"] == 60 + 30 + 20 + 10                                    # 할 일 기록만 (계획에 없던 40분 제외)
    assert s["diff"] == s["actual"] - s["planned"]
    # 지운 할 일은 빠진다
    c.delete(f"/api/tasks/{e}")
    s = review(c, pid)["summary"]
    assert s["tasks"] == 3 and s["planned"] == 135 and s["diff"] == 120 - 135


def test_card4_empty_plan_is_zero(c, monkeypatch):
    monkeypatch.setattr(index, "today_kst", lambda: _date(2026, 10, 2))
    pid = make_plan(c)
    assert review(c, pid)["summary"] == {"tasks": 0, "done": 0, "delayed": 0, "blocked": 0,
                                         "planned": 0, "actual": 0, "diff": 0}


def test_card4_complete_then_not_delayed(c, monkeypatch):                      # 완료는 지연으로 안 셈
    pid, (a, b, d, e) = seed_review(c, monkeypatch)
    c.patch(f"/api/tasks/{b}", json={"status": "done"})
    s = review(c, pid)["summary"]
    assert s["delayed"] == 0 and s["done"] == 2


def test_card4_drilldown_matches_numbers(c, monkeypatch):                      # C83
    pid, (a, b, d, e) = seed_review(c, monkeypatch)
    s = review(c, pid)["summary"]
    for kind in ("tasks", "done", "delayed", "blocked"):
        r = review(c, pid, kind)
        assert len(r["records"]) == s[kind], kind
        assert r["label"]
    assert [x["id"] for x in review(c, pid, "delayed")["records"]] == [b]
    assert {x["id"] for x in review(c, pid, "blocked")["records"]} == {a, b}
    r = review(c, pid, "blocked")["records"]
    assert all(any(l["blocker"] for l in x["logs"]) for x in r)                # 근거 기록(막힌 이유)이 함께 옴
    assert sum(x["planned_minutes"] for x in review(c, pid, "planned")["records"]) == s["planned"]
    assert sum(x["actual_minutes"] for x in review(c, pid, "actual")["records"]) == s["actual"]
    diff = review(c, pid, "diff")["records"]
    assert sum(x["diff_minutes"] for x in diff) == s["diff"]
    assert [abs(x["diff_minutes"]) for x in diff] == sorted((abs(x["diff_minutes"]) for x in diff), reverse=True)
    assert c.get(f"/api/plans/{pid}/review?kind=nope").status_code == 400


def test_card4_lesson_carries_to_next_plan(c, monkeypatch):                    # C33
    pid, _ = seed_review(c, monkeypatch)
    r = c.put(f"/api/plans/{pid}/review", json={"miss_pattern": "underestimate",
                                                 "lesson": "배포 관련 할 일은 예상 시간을 1.5배로 잡는다"})
    assert r.status_code == 200
    rid = r.json["id"]
    assert c.put(f"/api/plans/{pid}/review", json={"miss_pattern": "skipped", "lesson": "두 줄\n안 됨"}).status_code == 400
    info = c.get(f"/api/reviews/{rid}").json
    assert info["lesson"].startswith("배포 관련") and info["plan_title"] == "10월 1주"
    nxt = make_plan(c, title="10월 2주", start_date="2026-10-08", end_date="2026-10-14", from_review_id=rid)
    after = review(c, nxt)
    assert after["source_review"]["lesson"] == "배포 관련 할 일은 예상 시간을 1.5배로 잡는다"
    assert after["source_review"]["plan_title"] == "10월 1주"
    assert review(c, pid)["next_plans"][0]["id"] == nxt                        # 지난 돌아보기에서도 이어짐이 보임
    periods = c.get("/api/reviews").json["periods"]
    row = next(p for p in periods if p["plan_id"] == nxt)
    assert row["from_review_id"] == rid


def test_card4_suggested_pattern(c, monkeypatch):
    pid, _ = seed_review(c, monkeypatch)
    assert review(c, pid)["suggested_pattern"] in index.PATTERNS


# ---------------- 휴지통 (지운 할 일 30일 되돌리기) ----------------
def test_trash_soft_delete_and_restore(c, monkeypatch):
    monkeypatch.setattr(index, "today_kst", lambda: _date(2026, 10, 2))
    pid = make_plan(c)
    keep = make_task(c, pid, 60, "남길 것")
    tid = make_task(c, pid, 30, "지울 것")
    c.post(f"/api/tasks/{tid}/logs", json={**times(20), "blocker": "막힘", "complete": True})
    before = review(c, pid)["summary"]
    assert before["tasks"] == 2 and before["done"] == 1 and before["blocked"] == 1 and before["actual"] == 20

    r = c.delete(f"/api/tasks/{tid}")
    assert r.status_code == 200 and r.json["deleted_at"]
    # 화면·집계에서 빠짐 (C28: 지우지 않은 할 일만)
    assert [t["id"] for t in c.get(f"/api/plans/{pid}/tasks").json["tasks"]] == [keep]
    s = review(c, pid)["summary"]
    assert (s["tasks"], s["done"], s["blocked"], s["actual"], s["planned"]) == (1, 0, 0, 0, 60)
    assert c.get(f"/api/plans/{pid}").json["summary"]["completions"] == 0
    assert c.get(f"/api/plans/{pid}/tasks").json["logs"] == []
    # 휴지통에 있음 + 남은 날
    tr = c.get(f"/api/plans/{pid}/trash").json
    assert [t["id"] for t in tr["tasks"]] == [tid] and tr["tasks"][0]["days_left"] == 30 and tr["keep_days"] == 30
    # 휴지통의 할 일은 고치거나 기록할 수 없음
    assert c.patch(f"/api/tasks/{tid}", json={"title": "x"}).status_code == 404
    assert c.post(f"/api/tasks/{tid}/logs", json=times(10)).status_code == 404
    assert c.delete(f"/api/tasks/{tid}").status_code == 404           # 두 번 지우기 X
    # 되돌리면 기록·완료까지 그대로 돌아옴
    assert c.post(f"/api/tasks/{tid}/restore").json["deleted_at"] is None
    assert review(c, pid)["summary"] == before
    assert c.get(f"/api/plans/{pid}").json["summary"]["completions"] == 1
    assert c.post(f"/api/tasks/{tid}/restore").status_code == 404       # 살아 있으면 되돌릴 것 없음


def test_trash_permanent_delete(c):
    pid = make_plan(c)
    tid = make_task(c, pid, 30)
    c.post(f"/api/tasks/{tid}/logs", json=times(10))
    assert c.delete(f"/api/tasks/{tid}?permanent=1").status_code == 404    # 휴지통을 거쳐야 영구 삭제
    c.delete(f"/api/tasks/{tid}")
    assert c.delete(f"/api/tasks/{tid}?permanent=1").status_code == 204
    assert c.get(f"/api/plans/{pid}/trash").json["tasks"] == []
    assert index.app.config["DB"].t["logs"] == []                           # 딸린 기록도 실제로 지워짐
    assert c.post(f"/api/tasks/{tid}/restore").status_code == 404


def test_trash_purges_after_30_days(c):
    pid = make_plan(c)
    old, new = make_task(c, pid, 30, "오래된"), make_task(c, pid, 30, "최근")
    fake = index.app.config["DB"]
    for t in fake.t["tasks"]:
        if t["id"] == old:
            t["deleted_at"] = "2020-01-01T00:00:00+00:00"                  # 30일 훨씬 전
    c.delete(f"/api/tasks/{new}")
    ids = [t["id"] for t in c.get(f"/api/plans/{pid}/trash").json["tasks"]]
    assert ids == [new] and all(t["id"] != old for t in fake.t["tasks"])


# ---------------- 실행 기록: 예상보다 오래 걸린 경우 ----------------
def test_log_longer_than_planned_is_ok(c):
    pid = make_plan(c)
    tid = make_task(c, pid, 60)                                             # 예상 1시간
    r = c.post(f"/api/tasks/{tid}/logs", json={"started_at": "2026-10-01T09:00:00+09:00",
                                                "ended_at": "2026-10-01T12:00:00+09:00", "actual_minutes": 170})
    assert r.status_code == 201 and r.json["actual_minutes"] == 170          # 예상의 3배 가까이도 OK
    r = c.post(f"/api/tasks/{tid}/logs", json={"started_at": "2026-10-01T09:00:00+09:00",
                                                "ended_at": "2026-10-01T10:00:00+09:00", "actual_minutes": 90})
    assert r.status_code == 400 and "시작 시각을 앞당기" in r.json["error"]   # 시작~끝보다 길면 할 일을 알려 줌


# ---------------- 카드 5: 내 것으로 채우고, 잃지 않게 ----------------
def test_card5_export_everything(c):                                          # C36
    pid = make_plan(c)
    tid = make_task(c, pid, 60, "내보낼 할 일")
    gone = make_task(c, pid, 30, "휴지통 할 일")
    c.post(f"/api/tasks/{tid}/logs", json={**times(45), "blocker": "막힘", "complete": True})
    c.put(f"/api/plans/{pid}/review", json={"miss_pattern": "on_track", "lesson": "한 줄"})
    c.patch(f"/api/plans/{pid}", json={"title": "고친 이름", "change_note": "이유"})
    c.delete(f"/api/tasks/{gone}")
    r = c.get("/api/export")
    assert r.status_code == 200
    assert r.headers["Content-Disposition"].startswith('attachment; filename="plan-do-see-export-')
    j = r.json
    assert j["schema"] == "pds-schema-v3" and j["account"] == "a@example.com" and set(j["data"]) == set(index.EXPORT_TABLES)
    assert "request_keys" not in j["data"]
    d = j["data"]
    assert [p["title"] for p in d["plans"]] == ["고친 이름"] and d["plan_revisions"][0]["title"] == "10월 1주"
    assert {t["title"] for t in d["tasks"]} == {"내보낼 할 일", "휴지통 할 일"}            # 휴지통도 포함
    assert next(t for t in d["tasks"] if t["id"] == gone)["deleted_at"]
    log = d["logs"][0]
    assert log["actual_minutes"] == 45 and log["blocker"] == "막힘" and log["started_at"].endswith("+09:00")
    assert d["reviews"][0]["lesson"] == "한 줄" and len(d["task_completions"]) == 1
    assert j["counts"] == {t: len(v) for t, v in d.items()}


def test_card5_script_text_stored_verbatim(c):                                # C57 (서버는 글자 그대로 저장)
    s = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    pid = make_plan(c, title=s[:100], success_criteria=s)
    tid = c.post(f"/api/plans/{pid}/tasks", json={"title": s[:100], "planned_minutes": 30, "tags": ["<b>태그</b>"]}).json["id"]
    c.post(f"/api/tasks/{tid}/logs", json={**times(10), "blocker": s, "note": s})
    d = c.get(f"/api/plans/{pid}").json
    assert d["plan"]["success_criteria"] == s and d["logs"][0]["blocker"] == s
    assert c.get(f"/api/plans/{pid}/tasks").json["tasks"][0]["tags"] == ["<b>태그</b>"]


def test_card5_no_secret_in_any_response(c, monkeypatch):                    # C58 (서버 응답)
    secret = "sb_secret_TESTONLY_abcdefghijklmnop"
    monkeypatch.setenv("SUPABASE_SECRET_KEY", secret)
    pid = make_plan(c)
    tid = make_task(c, pid, 30)
    bodies = [c.get(p).get_data(as_text=True) for p in (
        "/api/health", "/api/meta", "/api/plans", f"/api/plans/{pid}", f"/api/plans/{pid}/tasks",
        f"/api/plans/{pid}/review", "/api/reviews", "/api/export", "/api/nope", f"/api/plans/{pid}/trash")]
    bodies.append(c.post(f"/api/tasks/{tid}/logs", json={}).get_data(as_text=True))
    assert not any(secret in b or "sb_secret" in b for b in bodies)


def test_contract_matches_code():                                            # contracts/pds-schema-v2.json
    import json
    from pathlib import Path
    contract = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "pds-schema-v3.json").read_text())
    tables = contract["tables"]
    assert set(index.EXPORT_TABLES) <= set(tables) and "request_keys" in tables
    cols = lambda t: set(tables[t]["columns"])
    assert set(index.PLAN_FIELDS) | {"from_review_id", "change_note"} <= cols("plans")
    assert {"title", "due_date", "priority", "tags", "planned_minutes", "status", "done_at", "deleted_at"} <= cols("tasks")
    log = index.build_log({"started_at": "2026-10-01T09:00:00+09:00", "ended_at": "2026-10-01T10:00:00+09:00"}, 1, 1)
    assert set(log) <= cols("logs")
    assert {"went_well", "went_wrong", "miss_pattern", "lesson"} <= cols("reviews")
    assert all(c.get("description") for t in tables.values() for c in t["columns"].values())


# ================= 과제 7: 인증 =================
def fresh():
    """빈 가짜 DB와, 아직 로그인 안 한 클라이언트."""
    index.app.config["DB"] = FakeDb()
    cl = index.app.test_client()
    cl.environ_base["HTTP_X_REQUESTED_WITH"] = "pds"
    return cl


def cookie_of(cl):
    ck = cl.get_cookie(index.SESSION_COOKIE)
    return ck.value if ck else None


def test_auth_signup_login_logout_me():
    cl = fresh()
    assert cl.get("/api/auth/me").status_code == 401
    r = cl.post("/api/auth/signup", json={"email": " Me@Example.com ", "password": PASSWORD})
    assert r.status_code == 201 and r.json == {"email": "me@example.com"}
    assert cl.get("/api/auth/me").json == {"email": "me@example.com"}
    assert cl.post("/api/auth/logout").json == {"ok": True}
    assert cl.get("/api/auth/me").status_code == 401
    assert cl.post("/api/auth/login", json={"email": "me@example.com", "password": "wrong-password!"}).status_code == 401
    r = cl.post("/api/auth/login", json={"email": "ME@example.com", "password": PASSWORD})
    assert r.status_code == 200 and cl.get("/api/auth/me").json["email"] == "me@example.com"


def test_auth_signup_validation():
    cl = fresh()
    assert cl.post("/api/auth/signup", json={"email": "bad", "password": PASSWORD}).status_code == 400
    assert cl.post("/api/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 400
    assert cl.post("/api/auth/signup", json={"email": "abcdefghij@b.co", "password": "abcdefghij"}).status_code == 400
    assert cl.post("/api/auth/signup", json={"email": "a@b.co", "password": PASSWORD}).status_code == 201
    assert cl.post("/api/auth/signup", json={"email": "A@B.CO", "password": PASSWORD}).status_code == 409


def test_auth_password_stored_as_hash_only():                    # 저장된 비밀번호에 입력한 글자가 없다
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "h@example.com", "password": PASSWORD})
    users = index.app.config["DB"].t["users"]
    stored = users[0]["password_hash"]
    assert PASSWORD not in stored and stored.startswith("scrypt:")
    assert PASSWORD not in repr(index.app.config["DB"].t)            # 어떤 표에도 원문이 없다
    token = cookie_of(cl)
    sess = index.app.config["DB"].t["sessions"][0]
    assert token not in repr(index.app.config["DB"].t) and sess["token_hash"] == index.token_hash(token)


def test_auth_every_data_route_needs_login():                     # 로그인 없이 자료 화면 → 401
    cl = fresh()
    owner = index.app.test_client(); owner.environ_base["HTTP_X_REQUESTED_WITH"] = "pds"
    owner.post("/api/auth/signup", json={"email": "o@example.com", "password": PASSWORD})
    pid = owner.post("/api/plans", json=PLAN).json["id"]
    rules = [r for r in index.app.url_map.iter_rules() if r.rule.startswith("/api/")]
    checked = 0
    for rule in rules:
        if rule.rule in index.PUBLIC_PATHS:
            continue
        path = rule.rule.replace("<int:plan_id>", str(pid)).replace("<int:task_id>", "1") \
                        .replace("<int:log_id>", "1").replace("<int:review_id>", "1")
        for m in rule.methods - {"HEAD", "OPTIONS"}:
            r = cl.open(path, method=m, json={})
            assert r.status_code == 401 and r.json["error"] == "로그인이 필요합니다", (m, path, r.status_code)
            checked += 1
    assert checked >= 25


def test_auth_logout_then_same_cookie_rejected():                 # 로그아웃 뒤 같은 값으로 다시 → 거절
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "l@example.com", "password": PASSWORD})
    token = cookie_of(cl)
    assert cl.get("/api/plans").status_code == 200
    cl.post("/api/auth/logout")
    assert cookie_of(cl) is None                                       # 브라우저 쿠키는 비워짐
    replay = index.app.test_client()
    replay.set_cookie(index.SESSION_COOKIE, token)                     # 예전 쿠키 값을 그대로 다시 붙여 보냄
    r = replay.get("/api/plans")
    assert r.status_code == 401 and r.json["error"] == "로그인이 필요합니다"
    assert index.app.config["DB"].t["sessions"] == []


def test_auth_expired_session_rejected():
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "x@example.com", "password": PASSWORD})
    index.app.config["DB"].t["sessions"][0]["expires_at"] = "2020-01-01T00:00:00+00:00"
    assert cl.get("/api/plans").status_code == 401


def test_auth_forged_cookie_rejected():
    cl = fresh()
    cl.set_cookie(index.SESSION_COOKIE, "made-up-token-value")
    assert cl.get("/api/plans").status_code == 401


def test_auth_login_rate_limit():
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "r@example.com", "password": PASSWORD})
    cl.post("/api/auth/logout")
    for _ in range(5):
        assert cl.post("/api/auth/login", json={"email": "r@example.com", "password": "nope-nope-nope"}).status_code == 401
    r = cl.post("/api/auth/login", json={"email": "r@example.com", "password": PASSWORD})
    assert r.status_code == 429                                        # 맞는 비밀번호도 잠시 막힘
    # 없는 이메일과 틀린 비밀번호는 같은 문장
    a = cl.post("/api/auth/login", json={"email": "nobody@example.com", "password": "nope-nope-nope"}).json
    assert a["error"] == "이메일 또는 비밀번호가 맞지 않습니다"


def test_auth_csrf_header_required():
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "c@example.com", "password": PASSWORD})
    bare = index.app.test_client()
    bare.set_cookie(index.SESSION_COOKIE, cookie_of(cl))
    r = bare.post("/api/plans", json=PLAN)                             # 쿠키는 있지만 헤더 없음
    assert r.status_code == 403
    assert bare.get("/api/plans").status_code == 200                   # 읽기는 헤더 없이도


def two_users():
    index.app.config["DB"] = FakeDb()
    a, b = new_client("a@example.com"), new_client("b@example.com")
    out = {}
    for name, cl in (("a", a), ("b", b)):
        pid = cl.post("/api/plans", json={**PLAN, "title": f"{name}의 계획"}).json["id"]
        tid = cl.post(f"/api/plans/{pid}/tasks", json={"title": f"{name}의 할 일", "planned_minutes": 30}).json["id"]
        lid = cl.post(f"/api/tasks/{tid}/logs", json={**times(20), "blocker": f"{name}의 막힘"}).json["id"]
        rid = cl.put(f"/api/plans/{pid}/review", json={"miss_pattern": "on_track", "lesson": f"{name}의 교훈"}).json["id"]
        out[name] = {"cl": cl, "pid": pid, "tid": tid, "lid": lid, "rid": rid}
    return out


@pytest.mark.parametrize("me,other", [("a", "b"), ("b", "a")])          # 양방향
def test_auth_cannot_touch_others_data(me, other):
    u = two_users()
    cl, o = u[me]["cl"], u[other]
    pid, tid, lid, rid = o["pid"], o["tid"], o["lid"], o["rid"]
    attempts = [
        ("GET", f"/api/plans/{pid}", None), ("GET", f"/api/plans/{pid}/tasks", None),
        ("GET", f"/api/plans/{pid}/review", None), ("GET", f"/api/plans/{pid}/revisions", None),
        ("GET", f"/api/plans/{pid}/trash", None), ("GET", f"/api/plans/{pid}/days", None),
        ("GET", f"/api/reviews/{rid}", None),
        ("PATCH", f"/api/plans/{pid}", {"title": "뺏기", "change_note": "x"}),
        ("PUT", f"/api/plans/{pid}/review", {"miss_pattern": "on_track", "lesson": "덮어쓰기"}),
        ("POST", f"/api/plans/{pid}/tasks", {"title": "끼워넣기", "planned_minutes": 10}),
        ("POST", f"/api/plans/{pid}/logs", {**times(10), "note": "끼워넣기"}),
        ("PATCH", f"/api/tasks/{tid}", {"title": "뺏기"}),
        ("PATCH", f"/api/tasks/{tid}", {"status": "done"}),
        ("POST", f"/api/tasks/{tid}/logs", times(10)),
        ("POST", f"/api/tasks/{tid}/restore", None),
        ("DELETE", f"/api/tasks/{tid}", None),
        ("DELETE", f"/api/tasks/{tid}?permanent=1", None),
        ("DELETE", f"/api/logs/{lid}", None),
        ("DELETE", f"/api/plans/{pid}", None),
    ]
    for m, path, body_ in attempts:
        r = cl.open(path, method=m, json=body_)
        assert r.status_code == 404, (m, path, r.status_code, r.json)
    # 다음 계획에 남의 돌아보기를 이어 붙이기
    assert cl.post("/api/plans", json={**PLAN, "from_review_id": rid}).status_code == 404
    # 남의 것은 하나도 바뀌지 않았다
    oc = o["cl"]
    d = oc.get(f"/api/plans/{pid}").json
    assert d["plan"]["title"] == f"{other}의 계획" and d["review"]["lesson"] == f"{other}의 교훈"
    assert [t["title"] for t in d["tasks"]] == [f"{other}의 할 일"] and len(d["logs"]) == 1
    assert d["tasks"][0]["status"] == "open"


@pytest.mark.parametrize("me,other", [("a", "b"), ("b", "a")])
def test_auth_lists_never_mix(me, other):                              # 목록에도 섞이지 않는다
    u = two_users()
    cl = u[me]["cl"]
    dump = "".join(cl.get(p).get_data(as_text=True) for p in (
        "/api/plans", "/api/reviews", "/api/stats", "/api/export", "/api/records?kind=tasks",
        "/api/records?kind=logs", "/api/records?kind=review:on_track"))
    assert f"{me}의 계획" in dump and f"{me}의 막힘" in dump
    assert f"{other}의" not in dump
    ex = cl.get("/api/export").json
    assert ex["account"] == f"{me}@example.com"
    assert all(len(rows) <= 2 for rows in ex["data"].values())


def test_auth_owner_cannot_be_spoofed():
    u = two_users()
    a, b = u["a"], u["b"]
    r = a["cl"].post("/api/plans", json={**PLAN, "user_id": 999, "title": "속이기"})
    plan = next(p for p in index.app.config["DB"].t["plans"] if p["id"] == r.json["id"])
    assert plan["user_id"] == index.app.config["DB"].t["users"][0]["id"]
    a["cl"].patch(f"/api/plans/{a['pid']}", json={"user_id": b["pid"], "title": "x", "change_note": "y"})
    assert next(p for p in index.app.config["DB"].t["plans"] if p["id"] == a["pid"])["user_id"] == plan["user_id"]


def test_auth_idempotency_key_not_shared():
    import uuid
    u = two_users()
    key = str(uuid.uuid4())
    r = u["a"]["cl"].patch(f"/api/tasks/{u['a']['tid']}", json={"title": "a 비밀"}, headers={"Idempotency-Key": key})
    assert r.status_code == 200
    r = u["b"]["cl"].patch(f"/api/tasks/{u['a']['tid']}", json={"title": "a 비밀"}, headers={"Idempotency-Key": key})
    assert r.status_code == 422 and "a 비밀" not in r.get_data(as_text=True)


def test_rule_change_shows_in_days():
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "d@example.com", "password": PASSWORD})
    pid = cl.post("/api/plans", json={**PLAN, "rule": "예상 시간은 처음 생각 그대로"}).json["id"]
    tid = cl.post(f"/api/plans/{pid}/tasks", json={"title": "x", "planned_minutes": 30}).json["id"]
    for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
        cl.post(f"/api/tasks/{tid}/logs", json={"started_at": f"{day}T09:00:00+09:00", "ended_at": f"{day}T09:40:00+09:00"})
    r = cl.patch(f"/api/plans/{pid}", json={"rule": "예상 시간은 1.5배로", "change_note": "3일차 전 규칙 변경"})
    assert r.json["changed"]
    d = cl.get(f"/api/plans/{pid}/days").json
    assert [x["date"] for x in d["days"]] == ["2026-10-07", "2026-10-08", "2026-10-09"] and d["days"][0]["logs"] == 1
    assert d["rule_changes"][0]["from"] == "예상 시간은 처음 생각 그대로" and d["rule_changes"][0]["to"] == "예상 시간은 1.5배로"
    assert d["current_rule"] == "예상 시간은 1.5배로"


# ================= 과제 7 보강: 비밀번호 바꾸기 · 계정 삭제 · 남의 계정 적어 보내기 · 5일 지표 =================
def test_password_change_ends_every_old_session():                 # T07-C114
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "p@example.com", "password": PASSWORD})
    phone = index.app.test_client(); phone.environ_base["HTTP_X_REQUESTED_WITH"] = "pds"
    phone.post("/api/auth/login", json={"email": "p@example.com", "password": PASSWORD})   # 다른 기기
    old_here, old_phone = cookie_of(cl), cookie_of(phone)
    assert cl.post("/api/auth/password", json={"current_password": "wrong-password-x", "new_password": "new-password-123"}).status_code == 403
    assert cl.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"}).status_code == 400
    assert cl.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD}).status_code == 400
    r = cl.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "new-password-123"})
    assert r.status_code == 200 and r.json["ended_sessions"] == 2
    assert cookie_of(cl) not in (None, old_here)                      # 바꾼 브라우저는 새 세션
    assert cl.get("/api/plans").status_code == 200
    for old in (old_here, old_phone):                                  # 예전 값은 모두 거절
        replay = index.app.test_client(); replay.set_cookie(index.SESSION_COOKIE, old)
        assert replay.get("/api/plans").status_code == 401
    assert phone.get("/api/plans").status_code == 401
    out = index.app.test_client(); out.environ_base["HTTP_X_REQUESTED_WITH"] = "pds"
    assert out.post("/api/auth/login", json={"email": "p@example.com", "password": PASSWORD}).status_code == 401
    assert out.post("/api/auth/login", json={"email": "p@example.com", "password": "new-password-123"}).status_code == 200
    assert "new-password-123" not in repr(index.app.config["DB"].t)


def test_account_delete_removes_all_my_data_only():                  # T07-C134
    u = two_users()
    a, b = u["a"], u["b"]
    dbm = index.app.config["DB"]
    before_b = {t: len([r for r in dbm.t[t] if r.get("user_id") == dbm.t["users"][1]["id"]]) for t in index.EXPORT_TABLES}
    assert a["cl"].delete("/api/auth/account", json={"password": PASSWORD}).status_code == 400            # 확인 문구 없음
    assert a["cl"].delete("/api/auth/account", json={"password": "nope-nope-nope", "confirm": "계정 삭제"}).status_code == 403
    token = cookie_of(a["cl"])
    r = a["cl"].delete("/api/auth/account", json={"password": PASSWORD, "confirm": "계정 삭제"})
    assert r.status_code == 200 and r.json["deleted"] and r.json["deleted_counts"]["plans"] == 1
    assert not any(x["email"] == "a@example.com" for x in dbm.t["users"])
    a_id = 1
    for t in (*index.EXPORT_TABLES, "sessions"):
        assert not [x for x in dbm.t[t] if x.get("user_id") == a_id], t
    replay = index.app.test_client(); replay.set_cookie(index.SESSION_COOKIE, token)
    assert replay.get("/api/plans").status_code == 401
    after_b = {t: len([r for r in dbm.t[t] if r.get("user_id") == dbm.t["users"][0]["id"]]) for t in index.EXPORT_TABLES}
    assert before_b == after_b                                          # B의 자료는 그대로
    cl = index.app.test_client(); cl.environ_base["HTTP_X_REQUESTED_WITH"] = "pds"
    assert cl.post("/api/auth/login", json={"email": "a@example.com", "password": PASSWORD}).status_code == 401


def test_other_account_named_in_query_header_body_is_ignored():      # T07-C123
    u = two_users()
    a, b = u["a"], u["b"]
    b_uid = index.app.config["DB"].t["users"][1]["id"]
    for path in (f"/api/plans?user_id={b_uid}", f"/api/plans?user_id=eq.{b_uid}", f"/api/export?user_id={b_uid}",
                 f"/api/reviews?user_id={b_uid}"):
        txt = a["cl"].get(path).get_data(as_text=True)
        assert "b의" not in txt and "a의 계획" in txt, path
    txt = a["cl"].get("/api/plans", headers={"X-User-Id": str(b_uid)}).get_data(as_text=True)
    assert "b의" not in txt and "a의 계획" in txt
    n_b = len(u["b"]["cl"].get("/api/plans").json)
    r = a["cl"].post("/api/plans", json={**PLAN, "user_id": b_uid, "title": "B 것인 척"})
    assert r.status_code == 201
    assert len(u["b"]["cl"].get("/api/plans").json) == n_b             # B 쪽에 새로 생긴 것 없음
    assert "B 것인 척" in a["cl"].get("/api/plans").get_data(as_text=True)


def five_days(cl, change_at, rule_from="할 일은 한 번에 50분", rule_to="할 일은 25분 단위로 쪼갠다", minutes=(40, 55, 30, 200, 45)):
    dbm = index.app.config["DB"]
    dbm.clock = "2026-10-07T08:00:00+09:00"
    pid = cl.post("/api/plans", json={**PLAN, "rule": rule_from, "question": "규칙을 바꾸면 하루 실행 시간이 늘까?"}).json["id"]
    tid = cl.post(f"/api/plans/{pid}/tasks", json={"title": "공부", "planned_minutes": 60}).json["id"]
    for i, m in enumerate(minutes):
        day = f"2026-10-{7 + i:02d}"
        if i == 2 and change_at == "before_day3":
            dbm.clock = "2026-10-09T07:30:00+09:00"
            cl.patch(f"/api/plans/{pid}", json={"rule": rule_to, "change_note": "2일 동안 50분 덩어리를 끝까지 못 버팀"})
        dbm.clock = f"{day}T21:00:00+09:00"
        cl.post(f"/api/tasks/{tid}/logs", json={"started_at": f"{day}T18:00:00+09:00",
                                                 "ended_at": f"{day}T{18 + (m + 59) // 60:02d}:00:00+09:00",
                                                 "actual_minutes": m})
        if i == 1 and change_at == "same_evening_day2_before_record":
            dbm.clock = "2026-10-08T20:00:00+09:00"
            cl.patch(f"/api/plans/{pid}", json={"rule": rule_to, "change_note": "너무 이르게 바꿈"})
    dbm.clock = None
    return pid


def test_days_metric_totals_and_rule_placement():                  # T07-C04~C15, C23~C27, C132
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "d5@example.com", "password": PASSWORD})
    pid = five_days(cl, "before_day3")
    d = cl.get(f"/api/plans/{pid}/days").json
    assert d["metric"]["unit"] == "분" and d["metric"]["week_start"].startswith("월요일")
    assert [x["minutes"] for x in d["days"]] == [40, 55, 30, 200, 45]
    assert [x["n"] for x in d["days"]] == [1, 2, 3, 4, 5]
    assert d["days"][3]["outliers"] == [200]                           # 튀는 값은 표시하지만 더한다
    assert d["totals"]["all"]["sum"] == 370 and d["totals"]["all"]["avg"] == 74.0
    assert d["totals"]["before"]["days"] == [1, 2] and d["totals"]["before"]["avg"] == 47.5
    assert d["totals"]["after"]["days"] == [3, 4, 5] and d["totals"]["after"]["avg"] == 91.7   # 275/3 = 91.666… → 91.7
    assert d["totals"]["diff_avg"] == 44.2
    assert d["days"][0]["rule"] == "할 일은 한 번에 50분" and d["days"][2]["rule"] == "할 일은 25분 단위로 쪼갠다"
    assert d["days"][0]["week_of"] == "2026-10-05"                      # 2026-10-07(수)의 주 → 월요일 10-05
    assert d["placement"]["ok"] and all(c["ok"] for c in d["checks"]), d["checks"]
    assert d["rule_change"]["note"] == "2일 동안 50분 덩어리를 끝까지 못 버팀"


def test_days_flags_change_before_day2_record_and_duplicates():
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "d6@example.com", "password": PASSWORD})
    pid = five_days(cl, "same_evening_day2_before_record", minutes=(40, 55, 30))
    d = cl.get(f"/api/plans/{pid}/days").json
    assert not d["placement"]["ok"]                                    # 2일차 기록(21시)보다 앞(20시)에 바꿈
    assert not next(c for c in d["checks"] if c["id"] == "five_days")["ok"]
    # 같은 할 일·같은 시작 시각 기록이 또 들어오면 한 번만 더한다
    tid = cl.get(f"/api/plans/{pid}/tasks").json[0]["id"] if isinstance(cl.get(f"/api/plans/{pid}/tasks").json, list) \
        else cl.get(f"/api/plans/{pid}").json["tasks"][0]["id"]
    cl.post(f"/api/tasks/{tid}/logs", json={"started_at": "2026-10-07T18:00:00+09:00",
                                             "ended_at": "2026-10-07T19:00:00+09:00", "actual_minutes": 40})
    d = cl.get(f"/api/plans/{pid}/days").json
    assert d["days"][0]["minutes"] == 40 and d["days"][0]["duplicates_skipped"] == 1


def test_question_must_stay_fixed_after_day1():
    cl = fresh()
    cl.post("/api/auth/signup", json={"email": "q@example.com", "password": PASSWORD})
    pid = five_days(cl, "before_day3")
    assert next(c for c in cl.get(f"/api/plans/{pid}/days").json["checks"] if c["id"] == "question")["ok"]
    index.app.config["DB"].clock = "2026-10-12T09:00:00+09:00"
    cl.patch(f"/api/plans/{pid}", json={"question": "다른 질문", "change_note": "x"})
    assert not next(c for c in cl.get(f"/api/plans/{pid}/days").json["checks"] if c["id"] == "question")["ok"]


def test_password_and_hash_never_in_responses_or_logs(caplog):      # T07-C105·C106
    import logging
    caplog.set_level(logging.DEBUG)
    cl = fresh()
    bodies = [cl.post("/api/auth/signup", json={"email": "s@example.com", "password": PASSWORD})]
    bodies.append(cl.post("/api/auth/login", json={"email": "s@example.com", "password": "wrong-password-zz"}))
    bodies.append(cl.post("/api/auth/login", json={"email": "s@example.com", "password": PASSWORD}))
    cl.post("/api/plans", json=PLAN)
    for p in ("/api/auth/me", "/api/plans", "/api/export", "/api/stats"):
        bodies.append(cl.get(p))
    bodies.append(cl.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "brand-new-pass-1"}))
    stored = index.app.config["DB"].t["users"][0]["password_hash"]
    bodies.append(cl.delete("/api/auth/account", json={"password": "brand-new-pass-1", "confirm": "계정 삭제"}))
    everything = "".join(b.get_data(as_text=True) + str(b.headers) for b in bodies) + caplog.text
    for secret in (PASSWORD, "brand-new-pass-1", "wrong-password-zz", stored, stored.split("$")[-1]):
        assert secret not in everything
