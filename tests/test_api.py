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
        self.t = {"plans": [], "tasks": [], "logs": [], "reviews": [], "plan_revisions": []}
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
        if table == "plans":
            row.setdefault("from_review_id", None)
            row.setdefault("goal", None)
            row.update(version=1, change_note=None, updated_at=f"t{row['id']}")
        self.t[table].append(row)
        return dict(row)

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
            r.update(new)
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
                for child in ("tasks", "logs", "reviews", "plan_revisions"):
                    self.delete(child, plan_id=r["id"])
            if table == "tasks":
                self.delete("logs", task_id=r["id"])
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


def log(c, pid, minutes, status="done", task_id=None, note=None):
    r = c.post(f"/api/plans/{pid}/logs", json={
        "task_id": task_id, "done_date": "2026-10-02",
        "actual_minutes": minutes, "status": status, "note": note})
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
    r = c.post(f"/api/plans/{pid}/logs", json={"done_date": "2026-10-02", "actual_minutes": 10, "status": "done"})
    assert r.status_code == 400
    # 상태값 검사
    r = c.post(f"/api/plans/{pid}/logs", json={"done_date": "2026-10-02", "actual_minutes": 10,
                                                "status": "finished", "note": "x"})
    assert r.status_code == 400


def test_log_cannot_point_to_other_plans_task(c):
    p1, p2 = make_plan(c), make_plan(c, title="다른 계획")
    t1 = make_task(c, p1, 30)
    r = c.post(f"/api/plans/{p2}/logs", json={"task_id": t1, "done_date": "2026-10-02",
                                               "actual_minutes": 10, "status": "done"})
    assert r.status_code == 400


def test_verdicts():
    v = index.verdict
    L = lambda m, s="done": {"actual_minutes": m, "status": s}
    assert v(60, []) == "pending"
    assert v(60, [L(90)]) == "over"
    assert v(60, [L(80, "partial")]) == "over"          # 안 끝났는데 이미 초과
    assert v(60, [L(30)]) == "under"
    assert v(60, [L(60)]) == "on_track"
    assert v(60, [L(72)]) == "on_track"                  # 경계: 정확히 +20%
    assert v(60, [L(20, "partial")]) == "in_progress"
    assert v(60, [L(0, "skipped")]) == "skipped"


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
