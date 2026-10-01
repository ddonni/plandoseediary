"""
플랜두씨 다이어리 — 2단계: 서버 API (Vercel Python 서버리스 함수)

브라우저는 이 API만 부르고, Supabase 비밀키는 이 파일이 실행되는
서버(Vercel) 환경변수에만 있습니다. 브라우저로는 절대 내려가지 않습니다.

환경변수
  SUPABASE_URL         예) https://abcd1234.supabase.co
  SUPABASE_SECRET_KEY  sb_secret_... (또는 예전 service_role 키)
"""

import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import requests
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
app.json.ensure_ascii = False  # 응답 JSON에 한글을 그대로


# ---------------------------------------------------------------------------
# 에러 처리: 어떤 실패든 {"error": "..."} 모양으로 돌려준다
# ---------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@app.errorhandler(ApiError)
def _api_error(e: ApiError):
    return jsonify(error=e.message), e.status


@app.errorhandler(HTTPException)
def _http_error(e: HTTPException):
    messages = {404: "없는 주소입니다", 405: "허용되지 않는 요청 방식입니다"}
    return jsonify(error=messages.get(e.code, e.description)), e.code


@app.errorhandler(Exception)
def _unexpected(e: Exception):
    app.logger.exception("unexpected error")  # 상세 내용은 Vercel 로그에만
    return jsonify(error="서버 내부 오류가 났습니다"), 500


# ---------------------------------------------------------------------------
# Supabase 접근 계층 (PostgREST REST API를 requests로 호출)
# ---------------------------------------------------------------------------
PG_ERRORS = {
    "23514": (400, "입력값이 데이터베이스 규칙에 맞지 않습니다"),
    "23503": (400, "연결하려는 항목이 없거나, 다른 계획에 속한 항목입니다"),
    "23505": (409, "이미 같은 항목이 있습니다"),
    "22P02": (400, "값의 형식이 올바르지 않습니다"),
}


class Supabase:
    def __init__(self, url: str, key: str, timeout: float = 8):
        # Data API 화면에서 복사하면 끝에 /rest/v1이 붙어 오는 경우가 있어 정리한다
        url = url.strip().rstrip("/")
        if url.endswith("/rest/v1"):
            url = url[: -len("/rest/v1")]
        self.base = url + "/rest/v1/"
        key = key.strip()  # 붙여넣을 때 섞인 공백·줄바꿈 제거
        self.timeout = timeout
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):  # 예전 JWT 형식 키는 Authorization도 필요
            self.headers["Authorization"] = f"Bearer {key}"

    @staticmethod
    def _filters(eq: dict) -> dict:
        return {k: ("is.null" if v is None else f"eq.{v}") for k, v in eq.items()}

    def _send(self, method, table, params=None, body=None, prefer=None):
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        try:
            r = requests.request(method, self.base + table, params=params,
                                 json=body, headers=headers, timeout=self.timeout)
        except requests.RequestException:
            raise ApiError(503, "데이터베이스에 연결할 수 없습니다")
        if r.status_code >= 400:
            try:
                code = r.json().get("code", "")
            except ValueError:
                code = ""
            app.logger.warning("supabase %s %s -> %s %s", method, table, r.status_code, r.text[:300])
            if code in PG_ERRORS:
                raise ApiError(*PG_ERRORS[code])
            if r.status_code in (401, 403):
                raise ApiError(500, "서버의 데이터베이스 키 설정이 잘못되었습니다")
            if code in ("PGRST205", "42P01") or r.status_code == 404:
                raise ApiError(502, f"데이터베이스에 '{table}' 표가 없습니다. "
                                    f"SUPABASE_URL과 SQL 실행 여부를 확인하세요 (HTTP {r.status_code} {code})")
            if code == "42703":
                raise ApiError(502, "데이터베이스에 필요한 칸이 없습니다. 추가 SQL(migrations)을 실행했는지 확인하세요")
            # 원인 코드(표 이름·HTTP 상태·오류 코드)만 보여주고 상세 문장은 로그에만 남긴다
            raise ApiError(502, f"데이터베이스 요청이 실패했습니다 (HTTP {r.status_code} {code})".strip())
        return r.json() if r.content else []

    def select(self, table, order="id", **eq):
        return self._send("GET", table, {"select": "*", "order": order, **self._filters(eq)})

    def insert(self, table, row):
        return self._send("POST", table, body=row, prefer="return=representation")[0]

    def update(self, table, row, **eq):
        return self._send("PATCH", table, self._filters(eq), row, "return=representation")

    def insert_ignore(self, table, row, on_conflict):
        """이미 같은 값이 있으면 아무것도 안 하고 None, 새로 넣었으면 그 줄을 돌려준다."""
        rows = self._send("POST", table, {"on_conflict": on_conflict}, row,
                          "resolution=ignore-duplicates,return=representation")
        return rows[0] if rows else None

    def upsert(self, table, row, on_conflict):
        return self._send("POST", table, {"on_conflict": on_conflict}, row,
                          "resolution=merge-duplicates,return=representation")[0]

    def delete(self, table, **eq):
        return len(self._send("DELETE", table, self._filters(eq), prefer="return=representation"))


def db():
    """테스트에서는 app.config["DB"]에 가짜 DB를 넣어 바꿔 끼운다."""
    if "DB" not in app.config:
        url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
        missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_SECRET_KEY", key)) if not v]
        if missing:
            raise ApiError(500, "서버 설정 누락: " + ", ".join(missing))
        app.config["DB"] = Supabase(url, key)
    return app.config["DB"]


# ---------------------------------------------------------------------------
# 입력 검사: DB에 보내기 전에 서버에서 한 번 더 거른다
# ---------------------------------------------------------------------------
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUSES = {"done", "partial", "skipped"}
PATTERNS = {"underestimate", "overestimate", "unplanned", "skipped", "on_track"}
PRIORITIES = {"high", "medium", "low"}
# 사람이 고칠 수 있는 계획 칸 (version, updated_at 등은 DB 트리거가 관리)
PLAN_FIELDS = ("title", "start_date", "end_date", "goal",
               "priority", "success_criteria", "estimated_minutes")


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON 객체로 보내 주세요")
    return data


def text(data, key, max_len, required=False):
    v = data.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ApiError(400, f"{key}: 비어 있으면 안 됩니다")
        return None
    if not isinstance(v, str):
        raise ApiError(400, f"{key}: 글자로 보내 주세요")
    v = v.strip()
    if len(v) > max_len:
        raise ApiError(400, f"{key}: {max_len}자 이하로 써 주세요")
    return v


def iso_date(data, key, required=True):
    v = data.get(key)
    if v in (None, ""):
        if required:
            raise ApiError(400, f"{key}: 날짜가 필요합니다")
        return None
    if not isinstance(v, str) or not DATE_RE.match(v):
        raise ApiError(400, f"{key}: YYYY-MM-DD 형식이어야 합니다")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ApiError(400, f"{key}: 없는 날짜입니다")
    return v


def integer(data, key, lo, hi, required=True):
    v = data.get(key)
    if v is None:
        if required:
            raise ApiError(400, f"{key}: 숫자가 필요합니다")
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ApiError(400, f"{key}: {lo}~{hi} 사이 정수여야 합니다")
    return v


def choice(data, key, allowed):
    v = data.get(key)
    if v not in allowed:
        raise ApiError(400, f"{key}: {', '.join(sorted(allowed))} 중 하나여야 합니다")
    return v


def get_one(table, id_):
    rows = db().select(table, id=id_)
    if not rows:
        raise ApiError(404, "해당 항목이 없습니다")
    return rows[0]


# ---------------------------------------------------------------------------
# 같은 요청 알아보기 (Idempotency-Key)
#   화면은 "한 번의 의도"마다 고유 키를 만들어 헤더에 붙인다.
#   연타·재전송으로 같은 키가 또 오면, 일을 다시 하지 않고 처음 응답을 그대로 돌려준다.
#   키를 먼저 DB에 '찜'(유일 키 insert)하기 때문에 동시에 두 요청이 와도 하나만 실행된다.
# ---------------------------------------------------------------------------
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def idempotent(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if key is None:
            return view(*args, **kwargs)
        if not UUID_RE.match(key):
            raise ApiError(400, "Idempotency-Key: UUID 형식이어야 합니다")
        key = key.lower()
        claimed = db().insert_ignore("request_keys",
                                     {"key": key, "method": request.method, "path": request.path},
                                     on_conflict="key")
        if claimed is None:                                   # 이미 본 키
            seen = db().select("request_keys", order="key", key=key)[0]
            if (seen["method"], seen["path"]) != (request.method, request.path):
                raise ApiError(422, "이 요청 키는 다른 요청에 이미 쓰였습니다")
            if seen["status"] is None:
                raise ApiError(409, "같은 요청을 처리하는 중입니다")
            resp = jsonify(seen["response"])
            resp.status_code = seen["status"]
            resp.headers["Idempotent-Replay"] = "true"
            return resp
        try:
            result = view(*args, **kwargs)
        except Exception:
            db().delete("request_keys", key=key)              # 실패한 요청은 고쳐서 다시 보낼 수 있게
            raise
        resp = app.make_response(result)
        db().update("request_keys", {"status": resp.status_code, "response": resp.get_json()}, key=key)
        return resp
    return wrapper


# ---------------------------------------------------------------------------
# 집계: 할 일마다 '예상 vs 실제'를 판정하고, 숫자마다 근거 기록을 붙인다
# ---------------------------------------------------------------------------
OVER_RATIO, UNDER_RATIO = 1.2, 0.8   # ±20%를 넘으면 '틀렸다'로 본다

VERDICTS = {
    "over":        "예상보다 오래 걸림 (+20% 초과)",
    "under":       "예상보다 빨리 끝남 (-20% 미만)",
    "on_track":    "예상과 비슷하게 끝남",
    "in_progress": "하는 중 (아직 완료 기록 없음)",
    "skipped":     "못 함 (건너뜀만 기록됨)",
    "pending":     "아직 기록 없음",
}

KINDS = {
    "tasks":     "계획한 할 일 전체",
    "done":      "완료한 할 일",
    "planned":   "예상 시간 합계의 근거 (할 일)",
    "actual":    "실제 시간 합계의 근거 (실행 기록 전체)",
    "unplanned": "계획에 없던 일",
    "completions": "완료 기록 (할 일당 1건)",
    "logs":      "실행 기록 전체",
    "blocked":   "막혔던 이유가 적힌 실행 기록",
    **VERDICTS,
}


def verdict(planned: int, logs: list, done: bool) -> str:
    """done = 할 일 상태가 '완료'인지 (카드 2부터 체크박스가 기준)"""
    if not logs:
        return "pending"
    actual = sum(l["actual_minutes"] for l in logs)
    if actual > planned * OVER_RATIO:
        return "over"                       # 끝났든 아니든 이미 초과
    if not done:
        return "skipped" if all(l["status"] == "skipped" for l in logs) else "in_progress"
    if actual < planned * UNDER_RATIO:
        return "under"
    return "on_track"


def progress(tasks: list, logs: list) -> list:
    by_task = defaultdict(list)
    for l in logs:
        if l["task_id"] is not None:
            by_task[l["task_id"]].append(l)
    out = []
    for t in tasks:
        ls = by_task[t["id"]]
        actual = sum(l["actual_minutes"] for l in ls)
        done = t.get("status") == "done"
        out.append({**t,
                    "actual_minutes": actual,
                    "diff_minutes": actual - t["planned_minutes"],
                    "is_done": done,
                    "verdict": verdict(t["planned_minutes"], ls, done)})
    return out


def records_for(kind: str, prog: list, logs: list) -> dict:
    """집계 숫자 하나가 '어떤 기록에서 나왔는지'를 돌려준다."""
    if kind not in KINDS:
        raise ApiError(400, f"kind: {', '.join(KINDS)} 중 하나여야 합니다")
    if kind == "unplanned":
        return {"tasks": [], "logs": [l for l in logs if l["task_id"] is None]}
    if kind in ("actual", "logs"):
        return {"tasks": [], "logs": logs}
    if kind == "blocked":
        return {"tasks": [], "logs": [l for l in logs if l.get("blocker")]}
    if kind in ("tasks", "planned"):
        picked = prog
    elif kind in ("done", "completions"):
        picked = [t for t in prog if t["is_done"]]
    else:
        picked = [t for t in prog if t["verdict"] == kind]
    ids = {t["id"] for t in picked}
    return {"tasks": picked, "logs": [l for l in logs if l["task_id"] in ids]}


def summary(prog: list, logs: list, completions=None) -> dict:
    count = {k: 0 for k in VERDICTS}
    for t in prog:
        count[t["verdict"]] += 1
    unplanned = [l for l in logs if l["task_id"] is None]
    misses = {"over": count["over"], "under": count["under"],
              "unplanned": len(unplanned), "skipped": count["skipped"]}
    top = max(misses, key=misses.get)
    active = [c for c in (completions or []) if c.get("revoked_at") is None]
    return {
        "tasks": len(prog),
        "done": sum(t["is_done"] for t in prog),
        # 돌아보기의 '완료 수' = 취소되지 않은 완료 기록 수 (DB가 할 일당 1개만 허용)
        "completions": len(active),
        "logs": len(logs),
        "blocked": sum(1 for l in logs if l.get("blocker")),
        "planned": sum(t["planned_minutes"] for t in prog),
        "actual": sum(l["actual_minutes"] for l in logs),
        "unplanned": len(unplanned),
        "unplanned_minutes": sum(l["actual_minutes"] for l in unplanned),
        **count,
        # 가장 자주 틀린 쪽 (한 번도 안 틀렸으면 None)
        "top_miss": top if misses[top] > 0 else None,
    }


def load(plan_id=None):
    f = {} if plan_id is None else {"plan_id": plan_id}
    tasks = db().select("tasks", order="due_date.asc.nullslast,id", **f)
    logs = db().select("logs", order="done_date.desc,id.desc", **f)
    return tasks, logs


def load_completions(plan_id=None):
    f = {} if plan_id is None else {"plan_id": plan_id}
    return db().select("task_completions", order="completed_at.desc,id.desc", **f)


# ---------------------------------------------------------------------------
# 라우트
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    db().select("plans", order="id")  # 실제로 DB에 한 번 다녀온다
    return jsonify(ok=True, db="connected")


@app.get("/api/meta")
def meta():
    return jsonify(kinds=KINDS, verdicts=VERDICTS,
                   over_ratio=OVER_RATIO, under_ratio=UNDER_RATIO)


# --- Plan ---------------------------------------------------------------
@app.get("/api/plans")
def list_plans():
    plans = db().select("plans", order="start_date.desc,id.desc")
    tasks, logs = load()
    completions = load_completions()
    reviews = {r["plan_id"]: r for r in db().select("reviews")}
    out = []
    for p in plans:
        pt = [t for t in tasks if t["plan_id"] == p["id"]]
        pl = [l for l in logs if l["plan_id"] == p["id"]]
        pc = [c for c in completions if c["plan_id"] == p["id"]]
        out.append({**p, "summary": summary(progress(pt, pl), pl, pc),
                    "has_review": p["id"] in reviews})
    return jsonify(out)


def plan_fields(d: dict, partial: bool) -> dict:
    """계획 칸 검사. partial=True(고치기)면 보낸 칸만 검사해서 돌려준다."""
    checks = {
        "title":             lambda: text(d, "title", 100, required=True),
        "start_date":        lambda: iso_date(d, "start_date"),
        "end_date":          lambda: iso_date(d, "end_date"),
        "goal":              lambda: text(d, "goal", 500),
        "priority":          lambda: choice(d, "priority", PRIORITIES),
        "success_criteria":  lambda: text(d, "success_criteria", 500, required=True),
        "estimated_minutes": lambda: integer(d, "estimated_minutes", 1, 100000),
    }
    return {k: f() for k, f in checks.items() if not partial or k in d}


@app.post("/api/plans")
def create_plan():
    d = body()
    row = plan_fields(d, partial=False)
    if row["end_date"] < row["start_date"]:
        raise ApiError(400, "end_date: 시작일보다 빠를 수 없습니다")
    row["from_review_id"] = integer(d, "from_review_id", 1, 2**62, required=False)
    if row["from_review_id"] is not None:
        get_one("reviews", row["from_review_id"])
    return jsonify(db().insert("plans", row)), 201


@app.patch("/api/plans/<int:plan_id>")
def update_plan(plan_id):
    """계획 고치기. 고치기 전 내용은 DB 트리거가 plan_revisions에 옮겨 담는다.
    계획 ID는 그대로, 버전 번호만 올라간다."""
    current = get_one("plans", plan_id)
    d = body()
    changes = plan_fields(d, partial=True)
    if not changes:
        raise ApiError(400, "고칠 칸을 하나 이상 보내 주세요")
    start = changes.get("start_date", current["start_date"])
    end = changes.get("end_date", current["end_date"])
    if end < start:
        raise ApiError(400, "end_date: 시작일보다 빠를 수 없습니다")
    if all(current.get(k) == v for k, v in changes.items()):
        return jsonify(plan=current, changed=False)
    changes["change_note"] = text(d, "change_note", 200, required=True)
    updated = db().update("plans", changes, id=plan_id)[0]
    return jsonify(plan=updated, changed=updated["version"] != current["version"])


@app.get("/api/plans/<int:plan_id>/revisions")
def plan_revisions(plan_id):
    """버전 1(처음 계획)부터 지금까지. 마지막 항목이 현재 계획."""
    plan = get_one("plans", plan_id)
    old = db().select("plan_revisions", order="version", plan_id=plan_id)
    current = {**{k: plan.get(k) for k in PLAN_FIELDS},
               "version": plan["version"], "change_note": plan.get("change_note"),
               "valid_from": plan.get("updated_at"), "replaced_at": None}
    return jsonify(plan_id=plan_id, versions=old + [current])


@app.get("/api/plans/<int:plan_id>")
def get_plan(plan_id):
    plan = get_one("plans", plan_id)
    tasks, logs = load(plan_id)
    prog = progress(tasks, logs)
    completions = load_completions(plan_id)
    review = (db().select("reviews", plan_id=plan_id) or [None])[0]
    source = None  # 이 계획을 낳은 지난 돌아보기 (See → Plan)
    if plan["from_review_id"]:
        rows = db().select("reviews", id=plan["from_review_id"])
        if rows:
            src_plan = db().select("plans", id=rows[0]["plan_id"])
            source = {**rows[0], "plan_title": src_plan[0]["title"] if src_plan else None}
    return jsonify(plan=plan, tasks=prog, logs=logs, review=review,
                   completions=completions, source_review=source,
                   summary=summary(prog, logs, completions))


@app.delete("/api/plans/<int:plan_id>")
def delete_plan(plan_id):
    if not db().delete("plans", id=plan_id):
        raise ApiError(404, "해당 항목이 없습니다")
    return "", 204


# --- Plan의 할 일 ---------------------------------------------------------
TASK_STATUSES = {"open", "done"}          # open = 진행 중
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
KST = timezone(timedelta(hours=9))         # '오늘'은 한국 날짜 기준 (서버는 UTC로 돈다)


def tags_field(d, key="tags"):
    """["API", "백엔드"] 또는 "API, 백엔드" 모두 받는다. 앞뒤 공백·중복 제거, 순서 유지."""
    v = d.get(key)
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, list) or not all(isinstance(t, str) for t in v):
        raise ApiError(400, f"{key}: 글자 목록으로 보내 주세요")
    out = []
    for t in (t.strip().lstrip("#") for t in v):
        if not t:
            continue
        if len(t) > 20:
            raise ApiError(400, f"{key}: 태그 하나는 20자 이하로 써 주세요")
        if t not in out:
            out.append(t)
    if len(out) > 10:
        raise ApiError(400, f"{key}: 태그는 10개까지 붙일 수 있습니다")
    return out


def task_fields(d: dict, partial: bool) -> dict:
    checks = {
        "title":           lambda: text(d, "title", 100, required=True),
        "due_date":        lambda: iso_date(d, "due_date", required=False),
        "priority":        lambda: choice(d, "priority", PRIORITIES) if "priority" in d else "medium",
        "tags":            lambda: tags_field(d),
        "planned_minutes": lambda: integer(d, "planned_minutes", 1, 1440),
    }
    row = {k: f() for k, f in checks.items() if not partial or k in d}
    if partial and "status" in d:
        row["status"] = choice(d, "status", TASK_STATUSES)
        row["done_at"] = datetime.now(timezone.utc).isoformat() if row["status"] == "done" else None
    return row


@app.post("/api/plans/<int:plan_id>/tasks")
@idempotent
def create_task(plan_id):
    get_one("plans", plan_id)
    row = {"plan_id": plan_id, **task_fields(body(), partial=False)}
    return jsonify(db().insert("tasks", row)), 201


@app.patch("/api/tasks/<int:task_id>")
@idempotent
def update_task(task_id):
    """내용 고치기, 완료로 바꾸기(status=done), 되돌리기(status=open) 모두 여기서."""
    current = get_one("tasks", task_id)
    changes = task_fields(body(), partial=True)
    if not changes:
        raise ApiError(400, "고칠 칸을 하나 이상 보내 주세요")
    if changes.get("status") == current["status"]:   # 이미 그 상태면 완료 시각을 건드리지 않음
        changes.pop("status")
        changes.pop("done_at")
    if not changes:
        return jsonify(current)
    return jsonify(db().update("tasks", changes, id=task_id)[0])


@app.delete("/api/tasks/<int:task_id>")
def delete_task(task_id):
    if not db().delete("tasks", id=task_id):
        raise ApiError(404, "해당 항목이 없습니다")
    return "", 204


# 정렬 규칙: 화면에 그대로 보여줄 설명 + 실제 비교 열쇠.
# 모든 규칙의 마지막 열쇠는 id(만든 순서, 절대 겹치지 않음)라서
# 값이 같은 할 일이 있어도 순서가 매번 똑같이 나온다.
def _due_key(t):
    return (t["due_date"] is None, t["due_date"] or "")


SORTS = {
    "due": {
        "label": "마감일 빠른 순",
        "steps": ["마감일 빠른 순 (마감일 없으면 맨 뒤)", "우선순위 높은 순", "먼저 만든 순"],
        "key": lambda t: (*_due_key(t), PRIORITY_RANK[t["priority"]], t["id"]),
    },
    "priority": {
        "label": "우선순위 높은 순",
        "steps": ["우선순위 높은 순", "마감일 빠른 순 (마감일 없으면 맨 뒤)", "먼저 만든 순"],
        "key": lambda t: (PRIORITY_RANK[t["priority"]], *_due_key(t), t["id"]),
    },
    "minutes": {
        "label": "예상 시간 긴 순",
        "steps": ["예상 시간 긴 순", "우선순위 높은 순", "먼저 만든 순"],
        "key": lambda t: (-t["planned_minutes"], PRIORITY_RANK[t["priority"]], t["id"]),
    },
    "title": {
        "label": "이름 가나다순",
        "steps": ["이름 가나다순 (대소문자 무시)", "먼저 만든 순"],
        "key": lambda t: (t["title"].casefold(), t["id"]),
    },
    "recent": {
        "label": "최근에 만든 순",
        "steps": ["최근에 만든 순"],
        "key": lambda t: -t["id"],
    },
}
DUE_FILTERS = {"all", "overdue", "today", "week", "none"}


def due_matches(t, due, today):
    d = t["due_date"]
    if due == "all":
        return True
    if due == "none":
        return d is None
    if d is None:
        return False
    d = date.fromisoformat(d)
    if due == "overdue":
        return d < today and t["status"] != "done"
    if due == "today":
        return d == today
    return today <= d <= today + timedelta(days=6)   # week: 오늘부터 7일 안


@app.get("/api/plans/<int:plan_id>/tasks")
def list_tasks(plan_id):
    """검색·거르기·정렬을 모두 서버에서 한다. 화면은 받은 순서를 그대로 그린다.
    ?q=글자 &status=all|open|done &priority=all|high|medium|low &tag=태그
    &due=all|overdue|today|week|none &sort=due|priority|minutes|title|recent"""
    get_one("plans", plan_id)
    a = request.args
    q = a.get("q", "").strip().casefold()
    status = a.get("status", "all")
    priority = a.get("priority", "all")
    tag = a.get("tag", "").strip()
    due = a.get("due", "all")
    sort = a.get("sort", "due")
    if status not in TASK_STATUSES | {"all"}:
        raise ApiError(400, "status: all, open, done 중 하나여야 합니다")
    if priority not in PRIORITIES | {"all"}:
        raise ApiError(400, "priority: all, high, medium, low 중 하나여야 합니다")
    if due not in DUE_FILTERS:
        raise ApiError(400, f"due: {', '.join(sorted(DUE_FILTERS))} 중 하나여야 합니다")
    if sort not in SORTS:
        raise ApiError(400, f"sort: {', '.join(SORTS)} 중 하나여야 합니다")

    tasks, logs = load(plan_id)
    prog = progress(tasks, logs)
    today = datetime.now(KST).date()

    def keep(t):
        if q and q not in t["title"].casefold() and not any(q in g.casefold() for g in t["tags"]):
            return False
        if status != "all" and t["status"] != status:
            return False
        if priority != "all" and t["priority"] != priority:
            return False
        if tag and tag not in t["tags"]:
            return False
        return due_matches(t, due, today)

    shown = sorted(filter(keep, prog), key=SORTS[sort]["key"])
    all_tags = sorted({g for t in prog for g in t["tags"]}, key=str.casefold)
    # 실행 기록: 할 일마다 붙여서 보내고, 전체 목록에는 어느 할 일의 기록인지 제목을 달아 보낸다
    title_of = {t["id"]: t["title"] for t in prog}
    logs_sorted = sorted(logs, key=lambda l: (l.get("started_at") or "", l["id"]), reverse=True)
    by_task = defaultdict(list)
    for l in logs_sorted:
        by_task[l["task_id"]].append(l)
    shown = [{**t, "logs": by_task[t["id"]]} for t in shown]
    return jsonify(
        tasks=shown, total=len(prog), shown=len(shown), tags=all_tags, today=today.isoformat(),
        summary=summary(prog, logs, load_completions(plan_id)),
        logs=[{**l, "task_title": title_of.get(l["task_id"])} for l in logs_sorted],
        query={"q": a.get("q", ""), "status": status, "priority": priority, "tag": tag, "due": due},
        sort={"key": sort, "label": SORTS[sort]["label"], "steps": SORTS[sort]["steps"]},
        sorts={k: v["label"] for k, v in SORTS.items()},
    )


# --- Do: 실행 기록 ----------------------------------------------------------
#   실행 기록은 logs 표에만 쓴다. 계획(plans)·할 일(tasks)의 예상 값은 절대 건드리지 않는다.
#   유일한 예외는 "이 기록으로 할 일 완료"를 골랐을 때 할 일의 '상태'만 완료로 바꾸는 것.
def iso_datetime(data, key):
    v = data.get(key)
    if not isinstance(v, str) or not v:
        raise ApiError(400, f"{key}: 시각이 필요합니다")
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, f"{key}: 시각 형식이 올바르지 않습니다 (ISO 8601)")
    if dt.tzinfo is None:
        raise ApiError(400, f"{key}: 시간대(+09:00 등)가 포함된 시각이어야 합니다")
    return dt


def build_log(d: dict, plan_id: int, task_id) -> dict:
    started, ended = iso_datetime(d, "started_at"), iso_datetime(d, "ended_at")
    if ended < started:
        raise ApiError(400, "ended_at: 시작 시각보다 빠를 수 없습니다")
    span = ended - started
    if span > timedelta(hours=24):
        raise ApiError(400, "ended_at: 한 기록은 24시간을 넘을 수 없습니다")
    span_min = -(-int(span.total_seconds()) // 60)          # 올림
    actual = integer(d, "actual_minutes", 0, 1440, required=False)
    if actual is None:
        actual = round(span.total_seconds() / 60)            # 안 적으면 시작~끝 사이 시간
    if actual > span_min:
        raise ApiError(400, f"actual_minutes: 시작~끝 사이({span_min}분)보다 길 수 없습니다")
    complete = d.get("complete", False)
    if not isinstance(complete, bool):
        raise ApiError(400, "complete: true/false로 보내 주세요")
    return {
        "plan_id": plan_id,
        "task_id": task_id,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "done_date": ended.astimezone(KST).date().isoformat(),   # 끝난 날(한국 날짜)
        "actual_minutes": actual,
        "status": "done" if complete else "partial",
        "blocker": text(d, "blocker", 500),
        "note": text(d, "note", 500),
    }


@app.post("/api/tasks/<int:task_id>/logs")
@idempotent
def create_task_log(task_id):
    """할 일에 붙는 실행 기록. complete=true면 할 일도 완료로 바꾼다(이미 완료면 그대로)."""
    task = get_one("tasks", task_id)
    row = build_log(body(), task["plan_id"], task_id)
    log = db().insert("logs", row)
    if row["status"] == "done" and task["status"] != "done":
        db().update("tasks", {"status": "done",
                              "done_at": datetime.now(timezone.utc).isoformat()}, id=task_id)
    return jsonify(log), 201


@app.post("/api/plans/<int:plan_id>/logs")
@idempotent
def create_log(plan_id):
    """계획에 없던 일(할 일에 안 붙는 기록). 무엇을 했는지 note가 필수."""
    get_one("plans", plan_id)
    d = body()
    if d.get("task_id") is not None:
        raise ApiError(400, "task_id: 할 일에 붙는 기록은 /api/tasks/{id}/logs로 보내 주세요")
    row = build_log({**d, "complete": False}, plan_id, None)
    if row["note"] is None:
        raise ApiError(400, "note: 계획에 없던 일은 무엇을 했는지 적어 주세요")
    return jsonify(db().insert("logs", row)), 201


@app.delete("/api/logs/<int:log_id>")
def delete_log(log_id):
    if not db().delete("logs", id=log_id):
        raise ApiError(404, "해당 항목이 없습니다")
    return "", 204


# --- See: 돌아보기 (계획당 하나, 다시 쓰면 덮어쓴다) ---------------------------
@app.put("/api/plans/<int:plan_id>/review")
def put_review(plan_id):
    get_one("plans", plan_id)
    d = body()
    row = {
        "plan_id": plan_id,
        "went_well": text(d, "went_well", 1000),
        "went_wrong": text(d, "went_wrong", 1000),
        "miss_pattern": choice(d, "miss_pattern", PATTERNS),
        "lesson": text(d, "lesson", 500, required=True),
    }
    return jsonify(db().upsert("reviews", row, on_conflict="plan_id"))


# --- 집계와 드릴다운 ----------------------------------------------------------
@app.get("/api/stats")
def stats():
    tasks, logs = load()
    reviews = db().select("reviews")
    patterns = {p: 0 for p in PATTERNS}
    for r in reviews:
        patterns[r["miss_pattern"]] += 1
    return jsonify(summary=summary(progress(tasks, logs), logs, load_completions()),
                   review_patterns=patterns)


@app.get("/api/records")
def records():
    """?kind=over 처럼 집계 이름을 주면, 그 숫자가 나온 기록들을 돌려준다.
    plan_id를 주면 그 계획 안에서만, 없으면 전체에서 찾는다."""
    kind = request.args.get("kind", "")
    raw = request.args.get("plan_id")
    if raw is not None and not raw.isdigit():
        raise ApiError(400, "plan_id: 숫자여야 합니다")
    plan_id = int(raw) if raw else None

    if kind.startswith("review:"):          # 돌아보기 패턴 집계의 근거
        pattern = kind.split(":", 1)[1]
        if pattern not in PATTERNS:
            raise ApiError(400, "알 수 없는 돌아보기 패턴입니다")
        return jsonify(kind=kind, label=f"돌아보기: {pattern}",
                       reviews=db().select("reviews", miss_pattern=pattern), tasks=[], logs=[])

    tasks, logs = load(plan_id)
    found = records_for(kind, progress(tasks, logs), logs)
    return jsonify(kind=kind, label=KINDS[kind], plan_id=plan_id, **found)
