"""API 테스트 — 진짜 Supabase 대신 메모리 위의 가짜 DB로 돌린다.
실행: python -m pytest -q
"""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
import index  # noqa: E402


class FakeDb:
    """Supabase 클래스와 같은 메서드를 가진 메모리 DB (외래키·연쇄삭제 흉내)."""

    def __init__(self):
        self.t = {"plans": [], "tasks": [], "logs": [], "reviews": [], "plan_revisions": [],
                  "task_completions": [], "request_keys": []}
        self.ids = itertools.count(1)

    def _match(self, row, eq):
        return all(row.get(k) == v for k, v in eq.items())

    def select(self, table, order="id", **eq):
        return [dict(r) for r in self.t[table] if self._match(r, eq)]

    def insert(self, table, row):
        if table == "logs" and row["task_id"] is not None:
            task = next(t for t in self.t["tasks"] if t["id"] == row["task_id"])
            if task["plan_id"] != row["plan_id"]:
                raise index.ApiError(*index.PG_ERRORS["23503"])
        row = {"id": next(self.ids), **row}
        if table == "tasks":
            for k, v in (("due_date", None), ("priority", "medium"), ("tags", []),
                         ("status", "open"), ("done_at", None)):
                row.setdefault(k, v)
        if table == "plans":
            row.setdefault("from_review_id", None)
            row.setdefault("goal", None)
            row.update(version=1, change_note=None, updated_at=f"t{row['id']}")
        self.t[table].append(row)
        if table == "tasks":
            self._completion_events(None, row)
        return dict(row)

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
                                               "plan_id": new["plan_id"], "completed_at": "now",
                                               "revoked_at": None})
        elif old is not None and old["status"] == "done" and new["status"] == "open":
            for c in active:
                c["revoked_at"] = "now"

    def update(self, table, row, **eq):
        """plans_keep_history 트리거 흉내: 내용이 바뀌면 옛 값을 이력에 쌓고 버전+1"""
        out = []
        for r in self.t[table]:
            if not self._match(r, eq):
                continue
            new = {**r, **row}
            if table == "plans" and any(new[k] != r[k] for k in index.PLAN_FIELDS):
                self.t["plan_revisions"].append({
                    "id": next(self.ids), "plan_id": r["id"],
                    **{k: r[k] for k in (*index.PLAN_FIELDS, "version", "change_note")},
                    "valid_from": r["updated_at"], "replaced_at": "now"})
                new.update(version=r["version"] + 1, updated_at="now")
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
                for child in ("tasks", "logs", "reviews", "plan_revisions", "task_completions"):
                    self.delete(child, plan_id=r["id"])
            if table == "tasks":
                self.delete("logs", task_id=r["id"])
                self.delete("task_completions", task_id=r["id"])
        return len(gone)


@pytest.fixture
def c():
    index.app.config["DB"] = FakeDb()
    index.app.config["TESTING"] = True
    yield index.app.test_client()
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
    assert c.delete(f"/api/tasks/{tid}").status_code == 204
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
    assert c.delete(f"/api/tasks/{tid}").status_code == 204
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
