"""
플랜두씨 다이어리 — 서버 API (Vercel Python 서버리스 함수)

브라우저는 이 API만 부르고, Supabase 비밀키는 이 파일이 실행되는
서버(Vercel) 환경변수에만 있습니다. 브라우저로는 절대 내려가지 않습니다.

인증(과제 7): 가입·로그인하면 HttpOnly 쿠키에 무작위 세션 토큰을 준다. DB에는 토큰의
SHA-256만 저장한다. /api/auth/* 와 /api/health 를 뺀 모든 /api 요청은 유효한 세션이 있어야 하고,
모든 자료 접근은 OwnerScoped(db())를 거쳐 로그인한 사람의 user_id 조건이 붙는다.

환경변수
  SUPABASE_URL         예) https://abcd1234.supabase.co
  SUPABASE_SECRET_KEY  sb_secret_... (또는 예전 service_role 키)
"""

import os
import re
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import hashlib
import secrets

import requests
from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

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

    def delete_where(self, table, params: dict):
        """eq 말고 다른 조건(lt. 등)으로 지울 때. params는 PostgREST 필터 그대로."""
        return len(self._send("DELETE", table, params, prefer="return=representation"))

    def select_where(self, table, params: dict, order="id"):
        """eq 말고 다른 조건(gte. 등)으로 읽을 때. params는 PostgREST 필터 그대로."""
        return self._send("GET", table, {"select": "*", "order": order, **params})


def raw_db():
    """주인 확인 없이 DB를 그대로 쓴다. 계정·세션 표처럼 로그인 전에 써야 하는 곳에서만.
    테스트에서는 app.config["DB"]에 가짜 DB를 넣어 바꿔 끼운다."""
    if "DB" not in app.config:
        url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
        missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_SECRET_KEY", key)) if not v]
        if missing:
            raise ApiError(500, "서버 설정 누락: " + ", ".join(missing))
        app.config["DB"] = Supabase(url, key)
    return app.config["DB"]


# 주인이 있는 표. 이 표들은 db()를 거치면 언제나 "로그인한 사람의 것"만 읽고·고치고·지운다.
OWNED_TABLES = {"plans", "tasks", "logs", "reviews", "plan_revisions", "task_completions", "request_keys"}


class OwnerScoped:
    """모든 자료 접근이 지나가는 관문. 주인 있는 표에는 user_id = 로그인한 사람 조건을 강제로 붙인다.
    라우트 코드가 조건을 빠뜨려도 남의 자료가 섞일 수 없게 하려는 장치."""

    def __init__(self, inner, user_id):
        self.inner, self.user_id = inner, user_id

    def _uid(self, table):
        if table not in OWNED_TABLES:
            return None
        if self.user_id is None:                     # 로그인 없이 자료 표에 닿는 길은 없어야 한다
            raise ApiError(401, "로그인이 필요합니다")
        return self.user_id

    def _eq(self, table, eq):
        uid = self._uid(table)
        return eq if uid is None else {**eq, "user_id": uid}

    def _row(self, table, row):
        uid = self._uid(table)
        return row if uid is None else {**row, "user_id": uid}   # 클라이언트가 보낸 user_id는 덮어씀

    def _params(self, table, params):
        uid = self._uid(table)
        return params if uid is None else {**params, "user_id": f"eq.{uid}"}

    def select(self, table, order="id", **eq):
        return self.inner.select(table, order, **self._eq(table, eq))

    def select_where(self, table, params, order="id"):
        return self.inner.select_where(table, self._params(table, params), order)

    def insert(self, table, row):
        return self.inner.insert(table, self._row(table, row))

    def insert_ignore(self, table, row, on_conflict):
        return self.inner.insert_ignore(table, self._row(table, row), on_conflict)

    def upsert(self, table, row, on_conflict):
        return self.inner.upsert(table, self._row(table, row), on_conflict)

    def update(self, table, row, **eq):
        row = {k: v for k, v in row.items() if k != "user_id"}  # 주인은 고칠 수 없다
        return self.inner.update(table, row, **self._eq(table, eq))

    def delete(self, table, **eq):
        return self.inner.delete(table, **self._eq(table, eq))

    def delete_where(self, table, params):
        return self.inner.delete_where(table, self._params(table, params))


def db():
    """라우트는 언제나 이것을 쓴다 → 로그인한 사람의 자료만 보인다."""
    return OwnerScoped(raw_db(), g.get("user_id"))


# ---------------------------------------------------------------------------
# 입력 검사: DB에 보내기 전에 서버에서 한 번 더 거른다
# ---------------------------------------------------------------------------
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUSES = {"done", "partial", "skipped"}
PATTERNS = {"underestimate", "overestimate", "unplanned", "skipped", "on_track"}
PRIORITIES = {"high", "medium", "low"}
# 사람이 고칠 수 있는 계획 칸 (version, updated_at 등은 DB 트리거가 관리)
PLAN_FIELDS = ("title", "start_date", "end_date", "goal",
               "priority", "success_criteria", "estimated_minutes", "rule", "question")


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


def get_task(task_id, deleted=False):
    """deleted=False: 살아 있는 할 일만. deleted=True: 휴지통에 있는 할 일만."""
    t = get_one("tasks", task_id)
    if bool(t.get("deleted_at")) != deleted:
        raise ApiError(404, "휴지통에 없는 할 일입니다" if deleted else "해당 항목이 없습니다 (휴지통에 있을 수 있어요)")
    return t


# ---------------------------------------------------------------------------
# 인증: 가입·로그인·로그아웃, 그리고 모든 /api 요청 앞의 관문
# ---------------------------------------------------------------------------
SESSION_COOKIE = "pds_session"
SESSION_DAYS = 7
LOGIN_WINDOW_MIN, LOGIN_MAX_FAILS = 15, 5          # 같은 이메일로 15분에 5번 틀리면 잠시 막음
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
PUBLIC_PATHS = {"/api/health", "/api/auth/signup", "/api/auth/login", "/api/auth/logout"}
# 없는 이메일로 로그인할 때도 해시 비교를 한 번 해서, 응답 시간으로 가입 여부를 알아내기 어렵게
_DUMMY_HASH = generate_password_hash("dummy-password-for-timing")


def now_utc():
    return datetime.now(timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def is_https():
    return request.is_secure or request.headers.get("X-Forwarded-Proto", "").split(",")[0] == "https"


@app.before_request
def gate():
    """① 상태를 바꾸는 요청은 우리 화면이 붙이는 헤더가 있어야 한다(다른 사이트가 몰래 보내는 요청 차단).
       ② 공개 경로를 뺀 /api 요청은 유효한 세션 쿠키가 있어야 한다. 없으면 401."""
    if not request.path.startswith("/api/"):
        return None
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("X-Requested-With") != "pds":
        raise ApiError(403, "허용되지 않은 요청입니다 (X-Requested-With 헤더 없음)")
    g.user_id, g.email, g.session_id = None, None, None
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        rows = raw_db().select("sessions", token_hash=token_hash(token))
        if rows and parse_ts(rows[0]["expires_at"]) > now_utc():
            g.user_id, g.session_id = rows[0]["user_id"], rows[0]["id"]
    if g.user_id is None and request.path not in PUBLIC_PATHS:
        raise ApiError(401, "로그인이 필요합니다")
    return None


@app.after_request
def no_store(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"        # 내 자료가 중간 캐시에 남지 않게
    return resp


def set_session_cookie(resp, token, max_age):
    resp.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True,
                    secure=is_https(), samesite="Lax", path="/")
    return resp


def start_session(user_id):
    token = secrets.token_urlsafe(32)                 # 256비트 무작위 — 쿠키에만 원문이 있다
    raw_db().insert("sessions", {
        "user_id": user_id,
        "token_hash": token_hash(token),
        "expires_at": (now_utc() + timedelta(days=SESSION_DAYS)).isoformat(),
        "user_agent": (request.headers.get("User-Agent") or "")[:300] or None,
    })
    return token


def read_credentials():
    d = body()
    email, password = d.get("email"), d.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise ApiError(400, "이메일과 비밀번호를 글자로 보내 주세요")
    email = email.strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ApiError(400, "이메일 형식이 올바르지 않습니다")
    return email, password


@app.post("/api/auth/signup")
def signup():
    email, password = read_credentials()
    if not 10 <= len(password) <= 128:
        raise ApiError(400, "비밀번호는 10자 이상 128자 이하로 정해 주세요")
    if password.strip().lower() == email.split("@")[0]:
        raise ApiError(400, "이메일 아이디와 같은 비밀번호는 쓸 수 없습니다")
    if raw_db().select("users", email=email):
        raise ApiError(409, "이미 가입한 이메일입니다")
    user = raw_db().insert("users", {"email": email, "password_hash": generate_password_hash(password)})
    token = start_session(user["id"])
    resp = jsonify(email=user["email"])
    resp.status_code = 201
    return set_session_cookie(resp, token, SESSION_DAYS * 86400)


@app.post("/api/auth/login")
def login():
    email, password = read_credentials()
    since = (now_utc() - timedelta(minutes=LOGIN_WINDOW_MIN)).isoformat()
    fails = raw_db().select_where("login_attempts", {"email": f"eq.{email}", "ok": "eq.false",
                                                      "attempted_at": f"gte.{since}"})
    if len(fails) >= LOGIN_MAX_FAILS:
        raise ApiError(429, f"로그인 실패가 많습니다. {LOGIN_WINDOW_MIN}분 뒤 다시 시도해 주세요")
    rows = raw_db().select("users", email=email)
    ok = check_password_hash(rows[0]["password_hash"] if rows else _DUMMY_HASH, password) and bool(rows)
    raw_db().insert("login_attempts", {"email": email, "ok": ok, "attempted_at": now_utc().isoformat()})
    if not ok:
        raise ApiError(401, "이메일 또는 비밀번호가 맞지 않습니다")   # 어느 쪽이 틀렸는지 알려주지 않는다
    raw_db().delete_where("sessions", {"user_id": f"eq.{rows[0]['id']}",
                                       "expires_at": f"lt.{now_utc().isoformat()}"})  # 만료 세션 정리
    token = start_session(rows[0]["id"])
    return set_session_cookie(jsonify(email=rows[0]["email"]), token, SESSION_DAYS * 86400)


@app.post("/api/auth/logout")
def logout():
    """세션 줄을 지운다 → 같은 쿠키 값으로 다시 요청해도 401. 쿠키도 비운다."""
    if g.session_id is not None:
        raw_db().delete("sessions", id=g.session_id)
    return set_session_cookie(jsonify(ok=True), "", 0)


@app.get("/api/auth/me")
def me():
    rows = raw_db().select("users", id=g.user_id)
    return jsonify(email=rows[0]["email"] if rows else None)


def check_new_password(password, email):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise ApiError(400, "비밀번호는 10자 이상 128자 이하로 정해 주세요")
    if password.strip().lower() == email.split("@")[0]:
        raise ApiError(400, "이메일 아이디와 같은 비밀번호는 쓸 수 없습니다")


def confirm_password(user, password):
    """민감한 일(비밀번호 바꾸기·계정 삭제) 전에 지금 비밀번호를 한 번 더 확인한다.
    틀린 횟수는 로그인 실패와 같은 장부에 쌓여서, 여기로 비밀번호를 맞혀 보는 것도 15분에 5번까지."""
    since = (now_utc() - timedelta(minutes=LOGIN_WINDOW_MIN)).isoformat()
    fails = raw_db().select_where("login_attempts", {"email": f"eq.{user['email']}", "ok": "eq.false",
                                                      "attempted_at": f"gte.{since}"})
    if len(fails) >= LOGIN_MAX_FAILS:
        raise ApiError(429, f"비밀번호 확인 실패가 많습니다. {LOGIN_WINDOW_MIN}분 뒤 다시 시도해 주세요")
    if not isinstance(password, str) or not check_password_hash(user["password_hash"], password):
        raw_db().insert("login_attempts", {"email": user["email"], "ok": False, "attempted_at": now_utc().isoformat()})
        raise ApiError(403, "지금 비밀번호가 맞지 않습니다")


@app.post("/api/auth/password")
def change_password():
    """비밀번호 바꾸기. 바꾸는 순간 이 계정의 세션을 '모두' 지운다
    → 다른 기기·예전 쿠키 값은 전부 401. 바꾼 이 브라우저에만 새 세션을 준다."""
    d = body()
    user = raw_db().select("users", id=g.user_id)[0]
    confirm_password(user, d.get("current_password"))
    new = d.get("new_password")
    check_new_password(new, user["email"])
    if check_password_hash(user["password_hash"], new):
        raise ApiError(400, "지금과 다른 비밀번호로 정해 주세요")
    raw_db().update("users", {"password_hash": generate_password_hash(new)}, id=user["id"])
    ended = raw_db().delete("sessions", user_id=user["id"])          # 예전 세션 전부 무효
    token = start_session(user["id"])
    return set_session_cookie(jsonify(email=user["email"], ended_sessions=ended), token, SESSION_DAYS * 86400)


@app.delete("/api/auth/account")
def delete_account():
    """계정 삭제. users 한 줄을 지우면 DB 외래키(ON DELETE CASCADE)가
    계획 → 할 일·실행 기록·완료 기록·돌아보기·수정 이력, 세션, 요청 키를 함께 지운다. 되돌릴 수 없다."""
    d = body()
    user = raw_db().select("users", id=g.user_id)[0]
    if d.get("confirm") != "계정 삭제":
        raise ApiError(400, "확인 문구 '계정 삭제'를 정확히 적어 주세요")
    confirm_password(user, d.get("password"))
    counts = {t: len(db().select(t)) for t in EXPORT_TABLES}           # 지워질 내 자료 건수 (안내용)
    raw_db().delete("users", id=user["id"])                            # ← 여기서 내 자료 전부가 연쇄 삭제
    raw_db().delete("login_attempts", email=user["email"])
    return set_session_cookie(jsonify(deleted=True, email=user["email"], deleted_counts=counts), "", 0)


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
            mine = db().select("request_keys", order="key", key=key)   # 내 키만 보인다
            if not mine:                                      # 남이 쓴 키 → 그 응답을 돌려주지 않는다
                raise ApiError(422, "이 요청 키는 쓸 수 없습니다")
            seen = mine[0]
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
    alive_ids = {t["id"] for t in prog}
    active = [c for c in (completions or [])
              if c.get("revoked_at") is None and c["task_id"] in alive_ids]   # 휴지통의 할 일은 빼고
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
    """살아 있는(휴지통에 없는) 할 일과, 그 할 일들 + 계획에 없던 일의 실행 기록."""
    f = {} if plan_id is None else {"plan_id": plan_id}
    tasks = db().select("tasks", order="due_date.asc.nullslast,id", deleted_at=None, **f)
    alive = {t["id"] for t in tasks}
    logs = [l for l in db().select("logs", order="done_date.desc,id.desc", **f)
            if l["task_id"] is None or l["task_id"] in alive]
    return tasks, logs


def load_completions(plan_id=None):
    f = {} if plan_id is None else {"plan_id": plan_id}
    return db().select("task_completions", order="completed_at.desc,id.desc", **f)


# ---------------------------------------------------------------------------
# 라우트
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    # 실제로 DB에 한 번 다녀온다 (로그인 없이도 열리므로 자료는 읽지 않고 id 하나만)
    raw_db().select_where("users", {"select": "id", "limit": "1"})
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
        "rule":              lambda: text(d, "rule", 200),   # 계획 규칙 (예: 예상 시간은 1.5배로)
        "question":          lambda: text(d, "question", 200),  # 5일 동안 답하려는 질문 (1일차에 고정)
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


def today_kst() -> date:
    """서울 기준 오늘. 테스트에서 바꿔 끼울 수 있게 함수로 둔다."""
    return datetime.now(KST).date()


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
    current = get_task(task_id)
    changes = task_fields(body(), partial=True)
    if not changes:
        raise ApiError(400, "고칠 칸을 하나 이상 보내 주세요")
    if changes.get("status") == current["status"]:   # 이미 그 상태면 완료 시각을 건드리지 않음
        changes.pop("status")
        changes.pop("done_at")
    if not changes:
        return jsonify(current)
    return jsonify(db().update("tasks", changes, id=task_id)[0])


# --- 휴지통 ------------------------------------------------------------------
#   지우기 = 휴지통으로 옮기기(deleted_at 표시). 딸린 실행 기록·완료 기록은 그대로 두고 화면·집계에서만 뺀다.
#   되돌리면 전부 원래대로. 30일이 지나거나 '영구 삭제'를 누르면 그때 실제로 지운다(연쇄 삭제).
TRASH_DAYS = 30


def purge_expired(plan_id=None):
    """휴지통에서 30일 지난 할 일을 실제로 지운다. 휴지통을 읽거나 지울 때마다 함께 정리."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=TRASH_DAYS)).isoformat()
    params = {"deleted_at": f"lt.{cutoff}"}
    if plan_id is not None:
        params["plan_id"] = f"eq.{plan_id}"
    db().delete_where("tasks", params)


@app.delete("/api/tasks/<int:task_id>")
def delete_task(task_id):
    """기본은 휴지통으로. ?permanent=1 이면 휴지통에 있는 할 일만 영구 삭제."""
    if request.args.get("permanent") == "1":
        task = get_task(task_id, deleted=True)
        db().delete("tasks", id=task["id"])
        return "", 204
    task = get_task(task_id)
    moved = db().update("tasks", {"deleted_at": datetime.now(timezone.utc).isoformat()}, id=task_id)[0]
    purge_expired(task["plan_id"])
    return jsonify(moved)


@app.post("/api/tasks/<int:task_id>/restore")
def restore_task(task_id):
    get_task(task_id, deleted=True)
    return jsonify(db().update("tasks", {"deleted_at": None}, id=task_id)[0])


@app.get("/api/plans/<int:plan_id>/trash")
def trash(plan_id):
    get_one("plans", plan_id)
    purge_expired(plan_id)
    rows = [t for t in db().select("tasks", order="deleted_at.desc,id.desc", plan_id=plan_id)
            if t.get("deleted_at")]
    now = datetime.now(timezone.utc)
    out = []
    for t in rows:
        gone = datetime.fromisoformat(t["deleted_at"].replace("Z", "+00:00")) + timedelta(days=TRASH_DAYS)
        out.append({**t, "purge_at": gone.isoformat(),
                    "days_left": max(0, -(-int((gone - now).total_seconds()) // 86400))})
    return jsonify(tasks=out, keep_days=TRASH_DAYS)


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
    today = today_kst()

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
        raise ApiError(400, f"실제 시간({actual}분)이 시작~끝 사이({span_min}분)보다 깁니다. "
                            "시작 시각을 앞당기거나 끝난 시각을 늦춰 주세요")
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
    task = get_task(task_id)
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
#   집계 숫자와 "그 숫자를 누르면 나오는 기록"은 같은 판정(review_rows의 is_* 값)에서 나온다.
#   그래서 숫자와 근거 목록의 개수가 어긋날 수 없다.
def review_rows(tasks: list, logs: list, today: date) -> list:
    by_task = defaultdict(list)
    for l in logs:
        if l["task_id"] is not None:
            by_task[l["task_id"]].append(l)
    rows = []
    for t in tasks:
        ls = sorted(by_task[t["id"]], key=lambda l: (l.get("started_at") or "", l["id"]))
        actual = sum(l["actual_minutes"] for l in ls)            # 분 단위
        done = t["status"] == "done"
        due = date.fromisoformat(t["due_date"]) if t.get("due_date") else None
        rows.append({
            "id": t["id"], "title": t["title"], "status": t["status"], "due_date": t.get("due_date"),
            "priority": t.get("priority"), "tags": t.get("tags", []),
            "planned_minutes": t["planned_minutes"],                 # 분 단위
            "actual_minutes": actual,
            "diff_minutes": actual - t["planned_minutes"],           # 실제 − 예상 (같은 단위)
            "logs": ls,
            "is_done": done,
            "is_delayed": (not done) and due is not None and due < today,   # 완료한 건 지연이 아님
            "is_blocked": any(l.get("blocker") for l in ls),
            "has_logs": bool(ls),
        })
    return rows


REVIEW_KINDS = {
    #  kind       화면 이름                                   이 숫자에 들어가는 할 일
    "tasks":   ("계획한 할 일",                               lambda r: True),
    "done":    ("완료한 할 일",                               lambda r: r["is_done"]),
    "delayed": ("지연: 완료 안 됐고 마감일(서울 기준)이 지남",   lambda r: r["is_delayed"]),
    "blocked": ("막힘: 막힌 이유가 적힌 기록이 있는 할 일",     lambda r: r["is_blocked"]),
    "planned": ("예상 시간: 할 일마다 잡은 예상 시간",          lambda r: True),
    "actual":  ("실제 시간: 할 일마다 실행 기록 합계",          lambda r: r["has_logs"]),
    "diff":    ("차이: 할 일마다 실제 − 예상 (차이 큰 순)",     lambda r: True),
}


def review_summary(rows: list) -> dict:
    planned = sum(r["planned_minutes"] for r in rows)
    actual = sum(r["actual_minutes"] for r in rows)
    return {
        "tasks": len(rows),
        "done": sum(r["is_done"] for r in rows),
        "delayed": sum(r["is_delayed"] for r in rows),
        "blocked": sum(r["is_blocked"] for r in rows),
        "planned": planned,                       # 아무것도 없으면 0
        "actual": actual,
        "diff": actual - planned,
    }


def suggest_pattern(rows: list, unplanned_count: int) -> str:
    """데이터로 본 '주로 어느 쪽으로 빗나갔나' 추천 (최종 선택은 사람이)."""
    score = {
        "underestimate": sum(1 for r in rows if r["has_logs"] and r["actual_minutes"] > r["planned_minutes"] * OVER_RATIO),
        "overestimate": sum(1 for r in rows if r["is_done"] and r["actual_minutes"] < r["planned_minutes"] * UNDER_RATIO),
        "skipped": sum(1 for r in rows if r["is_delayed"]),
        "unplanned": unplanned_count,
    }
    best = max(score, key=score.get)
    return best if score[best] > 0 else "on_track"


def plan_review(plan_id: int, kind: str | None = None) -> dict:
    plan = get_one("plans", plan_id)
    tasks, logs = load(plan_id)
    rows = review_rows(tasks, logs, today_kst())
    out = {
        "plan": plan,
        "today": today_kst().isoformat(),
        "summary": review_summary(rows),
        "kinds": {k: v[0] for k, v in REVIEW_KINDS.items()},
        "suggested_pattern": suggest_pattern(rows, sum(1 for l in logs if l["task_id"] is None)),
    }
    if kind is not None:
        if kind not in REVIEW_KINDS:
            raise ApiError(400, f"kind: {', '.join(REVIEW_KINDS)} 중 하나여야 합니다")
        picked = [r for r in rows if REVIEW_KINDS[kind][1](r)]
        if kind == "diff":
            picked.sort(key=lambda r: (-abs(r["diff_minutes"]), r["id"]))
        out["kind"] = kind
        out["label"] = REVIEW_KINDS[kind][0]
        out["records"] = picked
    return out


def review_links(plan: dict, review):
    """이 계획을 낳은 지난 고칠 점(source), 이 계획의 고칠 점을 이어받은 다음 계획들(next)."""
    source = None
    if plan.get("from_review_id"):
        rows = db().select("reviews", id=plan["from_review_id"])
        if rows:
            src_plan = db().select("plans", id=rows[0]["plan_id"])
            source = {**rows[0], "plan_title": src_plan[0]["title"] if src_plan else None}
    nxt = []
    if review:
        nxt = [{"id": p["id"], "title": p["title"], "start_date": p["start_date"], "end_date": p["end_date"]}
               for p in db().select("plans", order="id", from_review_id=review["id"])]
    return source, nxt


@app.get("/api/plans/<int:plan_id>/review")
def get_plan_review(plan_id):
    """?kind=delayed 처럼 주면, 그 숫자가 나온 할 일(과 실행 기록)을 records로 함께 돌려준다."""
    out = plan_review(plan_id, request.args.get("kind"))
    review = (db().select("reviews", plan_id=plan_id) or [None])[0]
    source, nxt = review_links(out["plan"], review)
    return jsonify(**out, review=review, source_review=source, next_plans=nxt)


@app.get("/api/reviews")
def list_reviews():
    """기간별 돌아보기: 계획(=기간) 하나당 한 줄."""
    plans = db().select("plans", order="start_date.desc,id.desc")
    tasks, logs = load()
    reviews = {r["plan_id"]: r for r in db().select("reviews")}
    today = today_kst()
    out = []
    for p in plans:
        rows = review_rows([t for t in tasks if t["plan_id"] == p["id"]],
                           [l for l in logs if l["plan_id"] == p["id"]], today)
        r = reviews.get(p["id"])
        out.append({"plan_id": p["id"], "title": p["title"], "start_date": p["start_date"],
                    "end_date": p["end_date"], "from_review_id": p.get("from_review_id"),
                    "summary": review_summary(rows),
                    "lesson": r["lesson"] if r else None, "review_id": r["id"] if r else None})
    return jsonify(periods=out, today=today.isoformat())


@app.get("/api/reviews/<int:review_id>")
def get_review(review_id):
    r = get_one("reviews", review_id)
    p = get_one("plans", r["plan_id"])
    return jsonify(**r, plan_title=p["title"], plan_start=p["start_date"], plan_end=p["end_date"])


@app.put("/api/plans/<int:plan_id>/review")
def put_review(plan_id):
    get_one("plans", plan_id)
    d = body()
    row = {
        "plan_id": plan_id,
        "went_well": text(d, "went_well", 1000),
        "went_wrong": text(d, "went_wrong", 1000),
        "miss_pattern": choice(d, "miss_pattern", PATTERNS),
        # 다음 계획으로 넘길 고칠 점 '한 줄'
        "lesson": text(d, "lesson", 200, required=True),
    }
    if "\n" in row["lesson"]:
        raise ApiError(400, "lesson: 고칠 점은 한 줄로 적어 주세요")
    return jsonify(db().upsert("reviews", row, on_conflict="plan_id"))


# --- 날짜별 기록 · 5일 사용 지표 ------------------------------------------------------
#   지표·단위·계산 규칙은 코드에 한 번만 적고(METRIC), 화면·설명서·내보내기가 모두 이 값을 쓴다.
#   → 규칙 변경 전과 후가 반드시 같은 지표·같은 단위·같은 계산으로 비교된다.
OUTLIER_MINUTES = 180
METRIC = {
    "name": "하루 실제 실행 시간",
    "unit": "분",
    "calc": "그날(서울 날짜, done_date)에 남긴 실행 기록의 '실제 걸린 시간'을 모두 더한다. "
            "기록한 날마다 한 줄, 1일차 = 기록이 처음 있는 날.",
    "missing": "실행 기록이 하나도 없는 날은 0분으로 채우지 않고 '기록한 날'에서 뺀다(5일에 세지 않음). "
               "기록 한 건의 실제 시간은 DB가 비워 둘 수 없게 막는다.",
    "duplicate": "같은 할 일·같은 시작 시각의 기록이 두 번 있으면 한 번만 더한다(먼저 적은 것). "
                 "저장 단추 연타는 요청 키로 처음부터 막힌다.",
    "outlier": f"기록 한 건이 {OUTLIER_MINUTES}분을 넘으면 '튀는 값'으로 표시하지만, 실제로 한 시간이므로 빼지 않고 더한다.",
    "rounding": "분은 정수 그대로 더한다. 평균만 소수 첫째 자리까지, 둘째 자리에서 반올림(0.05 → 0.1).",
    "week_start": "월요일 (주 = 월요일~일요일)",
}


def round1(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def to_kst(v: str) -> datetime:
    return parse_ts(v).astimezone(KST)


@app.get("/api/plans/<int:plan_id>/days")
def plan_days(plan_id):
    plan = get_one("plans", plan_id)
    tasks, logs = load(plan_id)
    # 계획 버전들(옛 것 + 지금). 버전마다 그 버전이 시작된 시각(valid_from)이 있다.
    versions = db().select("plan_revisions", order="version", plan_id=plan_id) + [
        {**plan, "valid_from": plan.get("updated_at")}]

    def value_at(field, at: datetime):           # 그 시각에 적용 중이던 값
        current = None
        for v in versions:
            if v.get("valid_from") and parse_ts(v["valid_from"]) <= at:
                current = v.get(field)
        return current

    def changes_of(field):
        out = []
        for prev, cur in zip(versions, versions[1:]):
            if (prev.get(field) or None) != (cur.get(field) or None):
                at = to_kst(cur["valid_from"])
                out.append({"version": cur["version"], "at": at.isoformat(), "date": at.date().isoformat(),
                            "from": prev.get(field), "to": cur.get(field), "note": cur.get("change_note")})
        return out

    # 1) 날짜별로 묶고, 중복(같은 할 일·같은 시작 시각)은 먼저 적은 한 건만
    by_day, seen, dup_count = defaultdict(list), set(), defaultdict(int)
    for l in sorted(logs, key=lambda x: (x.get("created_at") or "", x["id"])):
        key = (l["task_id"], l["started_at"]) if l.get("started_at") else ("id", l["id"])
        if key in seen:
            dup_count[l["done_date"]] += 1
            continue
        seen.add(key)
        by_day[l["done_date"]].append(l)

    days = []
    for n, day in enumerate(sorted(by_day), 1):
        rows = by_day[day]
        made = sorted(to_kst(l["created_at"]) for l in rows if l.get("created_at"))
        first = made[0] if made else datetime.combine(date.fromisoformat(day), datetime.min.time(), KST)
        d0 = date.fromisoformat(day)
        days.append({
            "n": n, "date": day, "weekday": "월화수목금토일"[d0.weekday()],
            "week_of": (d0 - timedelta(days=d0.weekday())).isoformat(),        # 그 주 월요일
            "logs": len(rows), "minutes": sum(l["actual_minutes"] for l in rows),
            "minutes_each": [l["actual_minutes"] for l in rows],
            "duplicates_skipped": dup_count[day],
            "outliers": [l["actual_minutes"] for l in rows if l["actual_minutes"] > OUTLIER_MINUTES],
            "first_recorded_at": made[0].isoformat() if made else None,
            "last_recorded_at": made[-1].isoformat() if made else None,
            "recorded_same_day": sum(1 for m in made if m.date() == d0),
            "rule": value_at("rule", first),                 # 그날 첫 기록을 남길 때 적용 중이던 규칙
        })

    # 2) 규칙 변경: 1일차 첫 기록 뒤에 일어난 변경만 '사용 중 변경'으로 센다 (처음 정한 것은 변경이 아님)
    rule_changes = changes_of("rule")
    start = parse_ts(days[0]["first_recorded_at"]) if days and days[0]["first_recorded_at"] else None
    during = [c for c in rule_changes if start and parse_ts(c["at"]) > start]
    for c in rule_changes:
        c["during_use"] = c in during
    change = during[0] if len(during) == 1 else None
    placement = None
    if change and len(days) >= 3:
        at = parse_ts(change["at"])
        after_day2 = parse_ts(days[1]["last_recorded_at"]) < at
        before_day3 = at < parse_ts(days[2]["first_recorded_at"])
        placement = {"after_day2_last_record": days[1]["last_recorded_at"], "change_at": change["at"],
                     "before_day3_first_record": days[2]["first_recorded_at"],
                     "ok": after_day2 and before_day3}

    # 3) 합계·평균 — 화면에 보이는 숫자 그대로 손으로 더해 맞춰 볼 수 있게 식도 함께 돌려준다
    def block(ds):
        total = sum(d["minutes"] for d in ds)
        return {"days": [d["n"] for d in ds], "sum": total, "avg": round1(total / len(ds)) if ds else None,
                "formula": (" + ".join(str(d["minutes"]) for d in ds) + f" = {total}분, {total} ÷ {len(ds)} = "
                            f"{round1(total / len(ds))}분") if ds else None}
    if change:
        before = [d for d in days if parse_ts(d["first_recorded_at"]) < parse_ts(change["at"])]
        after = [d for d in days if d not in before]
    else:
        before, after = days, []
    totals = {"all": block(days), "before": block(before), "after": block(after)}
    if before and after:
        totals["diff_avg"] = round1(totals["after"]["avg"] - totals["before"]["avg"])

    # 4) 질문은 1일차에 고정: 2일차 첫 기록 전까지 정해져 있고, 그 뒤로 바뀌지 않았는지
    q_changes = changes_of("question")
    day2_start = parse_ts(days[1]["first_recorded_at"]) if len(days) >= 2 else None
    q_fixed = bool(plan.get("question")) and (
        day2_start is None or (value_at("question", day2_start) == plan.get("question")
                               and not any(parse_ts(c["at"]) > day2_start for c in q_changes)))

    checks = [
        {"id": "question", "ok": q_fixed, "label": "1일차에 정한 질문이 그 뒤로 바뀌지 않음"},
        {"id": "five_days", "ok": len(days) == 5, "label": f"기록한 날이 정확히 5일 (지금 {len(days)}일)"},
        {"id": "same_day", "ok": bool(days) and all(d["recorded_same_day"] == d["logs"] for d in days),
         "label": "모든 기록을 그 날짜 당일(서울)에 적음"},
        {"id": "one_change", "ok": len(during) == 1, "label": f"사용 중 규칙 변경이 딱 한 번 (지금 {len(during)}번)"},
        {"id": "placement", "ok": bool(placement and placement["ok"]),
         "label": "규칙 변경이 2일차 마지막 기록 뒤, 3일차 첫 기록 앞"},
    ]
    return jsonify(plan_id=plan_id, question=plan.get("question"), question_changes=q_changes,
                   metric=METRIC, days=days, rule_changes=rule_changes, rule_change=change,
                   placement=placement, totals=totals, checks=checks, current_rule=plan.get("rule"))


# --- 내 자료 전체 내보내기 ---------------------------------------------------------
#   표 그대로(행·ID·시각·단위 그대로) JSON 파일 하나로. request_keys는 중복 방지용 내부 장부라 뺀다.
EXPORT_TABLES = ("plans", "plan_revisions", "tasks", "logs", "task_completions", "reviews")


@app.get("/api/export")
def export_all():
    now = datetime.now(timezone.utc)
    data = {t: db().select(t, order="id") for t in EXPORT_TABLES}
    payload = {
        "format": "plan-do-see-export",
        "schema": "pds-schema-v3",                  # contracts/pds-schema-v3.json 과 같은 구조
        "account": (raw_db().select("users", id=g.user_id) or [{}])[0].get("email"),
        "exported_at": now.isoformat(),
        "rules": {
            "timestamps": "timestamptz — ISO 8601, 시간대 포함. 화면은 Asia/Seoul로 보여 줌",
            "dates": "date — YYYY-MM-DD, 서울(Asia/Seoul) 달력 날짜",
            "durations": "정수, 분(minute) 단위 (planned_minutes, estimated_minutes, actual_minutes)",
            "trash": "tasks.deleted_at이 차 있으면 휴지통에 있는 할 일 (30일 뒤 영구 삭제)",
        },
        "metric": METRIC,                           # 5일 사용 기록에 쓴 지표·단위·계산 규칙
        "counts": {t: len(rows) for t, rows in data.items()},
        "data": data,
    }
    resp = jsonify(payload)
    stamp = now.astimezone(KST).strftime("%Y%m%d-%H%M")
    resp.headers["Content-Disposition"] = f'attachment; filename="plan-do-see-export-{stamp}.json"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
