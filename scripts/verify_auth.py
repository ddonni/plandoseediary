#!/usr/bin/env python3
"""
인증 확인 스크립트 — 막히는 장면을 실제 요청·응답으로 남긴다 (설명서 ④에 그대로 붙이기용).

  python scripts/verify_auth.py https://plandoseediary.vercel.app --out docs/auth-check.md
  python scripts/verify_auth.py <주소> --pause-before-delete --out docs/auth-check.md
      └ 계정을 지우기 직전에 멈춘다. 그때 Supabase SQL Editor에서 docs/hash-check.sql을 실행해
        "같은 비밀번호인데 저장값이 다르다"(T07-C104)를 확인하고 Enter.

하는 일
  · 시험용 계정 A, B를 새로 만든다. 이메일은 무작위, 비밀번호는 둘이 "같은" 무작위 값(진짜 계정 아님)
  · A·B가 각자 계획·할 일·실행 기록·돌아보기를 하나씩 만든다
  · 확인 다섯 가지를 "성공한 요청 ↔ 거절된 요청" 짝으로 실행한다
      1 로그인 없이  2 남의 자료(양방향, 건수 전후 비교)  3 남의 계정을 주소·헤더·본문에 적어 보내기
      4 로그아웃 뒤 같은 쿠키  5 비밀번호를 바꾼 뒤 예전 쿠키
  · 그 밖에 위조 쿠키·헤더 없는 쓰기·틀린 비밀번호·중복 가입, 마지막으로 계정 삭제(자료 연쇄 삭제)
  · 쿠키는 앞 4글자만 남기고, 비밀번호는 전부 가린다 (예: pds_session=Xy3k…생략, "password": "(가림)")

필요: Python 3.9+ 와 requests (pip install requests)
"""
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone

import requests

_argv = sys.argv[1:]
OUT_PATH = None                                   # --out 파일: UTF-8로 직접 저장 (Windows PowerShell의 > 는 인코딩이 깨짐)
if "--out" in _argv:
    i = _argv.index("--out")
    OUT_PATH = _argv[i + 1]
    del _argv[i:i + 2]
ARGS = [a for a in _argv if not a.startswith("--")]
BASE = (ARGS[0] if ARGS else "http://localhost:5055").rstrip("/")
for _stream in (sys.stdout, sys.stderr):          # Windows 콘솔(cp949)에서도 한글·기호가 깨지지 않게
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def emit(text):
    if OUT_PATH:
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        sys.stderr.write(f"\n저장함: {OUT_PATH}\n")
    else:
        print(text)
PAUSE = "--pause-before-delete" in sys.argv
KST = timezone(timedelta(hours=9))
H = {"X-Requested-With": "pds"}
SECRET_KEYS = {"password", "current_password", "new_password"}
results = []          # (번호, 확인 묶음, 제목, 기대, 실제, 통과)
out = []
pairs = {}            # 확인 번호 → {"ok": [번호…], "no": [번호…]}
group = ""


def mask(v: str, keep=4):
    return v[:keep] + "…생략" if v else v


def body_text(r):
    try:
        return json.dumps(r.json(), ensure_ascii=False)[:400]
    except ValueError:
        return r.text[:200]


def record(title, expect, r, req_line, req_body=None, cookie=None, note="", extra_headers=None, side=None):
    ok = r.status_code == expect if isinstance(expect, int) else r.status_code in expect
    n = len(results) + 1
    results.append((n, group, title, expect, r.status_code, ok))
    if side:
        pairs.setdefault(group, {"ok": [], "no": []})[side].append(n)
    tag = {"ok": " · 성공한 요청", "no": " · 거절된 요청"}.get(side, "")
    out.append(f"#### {n}. {title}{tag} — {'✅' if ok else '❌'}\n")
    out.append("```http")
    out.append(req_line)
    for k, v in (extra_headers or {}).items():
        out.append(f"{k}: {v}")
    if cookie is not None:
        out.append(f"Cookie: pds_session={mask(cookie)}" if cookie else "Cookie: (없음)")
    if req_body is not None:
        shown = {k: ("(가림)" if k in SECRET_KEYS else v) for k, v in req_body.items()}
        out.append("")
        out.append(json.dumps(shown, ensure_ascii=False))
    out.append("")
    out.append(f"HTTP/1.1 {r.status_code}")
    sc = r.headers.get("Set-Cookie")
    if sc:
        name_val, _, rest = sc.partition(";")
        name, _, val = name_val.partition("=")
        shown_val = mask(val) if val else '""'
        out.append(f"Set-Cookie: {name}={shown_val};{rest}")
    out.append(body_text(r))
    out.append("```")
    if note:
        out.append(note)
    out.append("")
    return r


def call(sess, method, path, title, expect, json_body=None, cookie=None, headers=None, note="", side=None,
         show_headers=None):
    hdrs = dict(H) if headers is None else dict(headers)
    hdrs.update(show_headers or {})
    if cookie is not None:          # 세션 객체 대신 쿠키 값을 직접 붙여 보내는 경우
        if cookie:
            hdrs["Cookie"] = f"pds_session={cookie}"
        r = requests.request(method, BASE + path, json=json_body, headers=hdrs, timeout=30)
        shown_cookie = cookie
    else:
        shown_cookie = sess.cookies.get("pds_session") or ""     # 이 요청에 실제로 실려 가는 쿠키 (보내기 전 값)
        r = sess.request(method, BASE + path, json=json_body, headers=hdrs, timeout=30)
    return record(title, expect, r, f"{method} {path}", json_body, shown_cookie, note, show_headers, side)


def fact(title, ok, text, side=None):
    """요청 하나가 아니라 '비교'로 확인하는 것 (건수 전후, 목록 섞임 등)."""
    n = len(results) + 1
    results.append((n, group, title, "맞음", "맞음" if ok else "틀림", ok))
    if side:
        pairs.setdefault(group, {"ok": [], "no": []})[side].append(n)
    out.append(f"#### {n}. {title} — {'✅' if ok else '❌'}\n")
    out.append(f"```\n{text}\n```\n")


def section(key, heading, why):
    global group
    group = key
    out.append(f"## {heading}\n")
    out.append(f"> {why}\n")


def kst(days_ago=0, h=9, m=0):
    d = datetime.now(KST).replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(days=days_ago)
    return d.isoformat()


def counts(sess):
    """그 계정 주인이 자기 쿠키로 본 자기 자료 건수 (내보내기의 counts)."""
    r = sess.get(BASE + "/api/export", timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"\n[중단] 자료 건수를 세려고 GET /api/export 했는데 HTTP {r.status_code}: {body_text(r)}\n"
                         "       401이면 시험 계정이나 세션이 실행 도중 지워진 것입니다 "
                         "(예: 멈춘 사이에 'delete from users …'를 실행). 처음부터 다시 실행하세요.")
    return r.json()["counts"]


def fmt_counts(c):
    names = {"plans": "계획", "tasks": "할 일", "logs": "실행 기록", "task_completions": "완료 기록",
             "reviews": "돌아보기", "plan_revisions": "수정 이력"}
    return ", ".join(f"{names.get(k, k)} {v}" for k, v in c.items())


def make_user(tag, pw):
    s = requests.Session()
    email = f"authcheck-{tag}-{secrets.token_hex(3)}@example.com"
    call(s, "POST", "/api/auth/signup", f"시험 계정 {tag.upper()} 가입", 201, {"email": email, "password": pw})
    today = datetime.now(KST).date().isoformat()
    plan = s.post(BASE + "/api/plans", headers=H, json={
        "title": f"{tag.upper()}의 비공개 계획", "start_date": today, "end_date": today, "priority": "medium",
        "success_criteria": f"{tag.upper()}만 볼 수 있다", "estimated_minutes": 60}).json()
    task = s.post(BASE + f"/api/plans/{plan['id']}/tasks", headers=H,
                  json={"title": f"{tag.upper()}의 할 일", "planned_minutes": 30}).json()
    log = s.post(BASE + f"/api/tasks/{task['id']}/logs", headers=H, json={
        "started_at": kst(0, 9, 0), "ended_at": kst(0, 9, 20), "blocker": f"{tag.upper()}의 막힌 이유"}).json()
    review = s.put(BASE + f"/api/plans/{plan['id']}/review", headers=H,
                   json={"miss_pattern": "on_track", "lesson": f"{tag.upper()}의 고칠 점"}).json()
    return {"s": s, "email": email, "pw": pw, "uid": plan["user_id"], "pid": plan["id"], "tid": task["id"],
            "lid": log["id"], "rid": review["id"]}


def main():
    out.append(f"# 인증 확인 기록\n\n- 대상: {BASE}\n- 실행: {datetime.now(KST):%Y-%m-%d %H:%M} (서울)\n"
               "- 계정: 실행할 때마다 새로 만든 시험 계정 A·B (진짜 계정 아님, 둘의 비밀번호는 일부러 같게). "
               "쿠키는 앞 4글자만, 비밀번호는 전부 가림.\n- 끝에서 두 계정을 '계정 삭제'로 지운다.\n")
    out.append("## 0. 준비 — 두 계정과 각자의 자료\n")
    group_ = "0"
    globals()["group"] = group_
    pw = secrets.token_urlsafe(12)                        # A와 B가 같은 비밀번호 → 저장값이 다른지 비교용
    a = make_user("a", pw)
    b = make_user("b", pw)
    call(requests.Session(), "POST", "/api/auth/signup", "이미 있는 A 이메일로 다시 가입", 409,
         {"email": a["email"], "password": "another-password-1"})
    out.append(f"A의 자료: 계획 #{a['pid']} · 할 일 #{a['tid']} · 실행 기록 #{a['lid']} · 돌아보기 #{a['rid']}  \n"
               f"B의 자료: 계획 #{b['pid']} · 할 일 #{b['tid']} · 실행 기록 #{b['lid']} · 돌아보기 #{b['rid']}\n")

    # ---------------------------------------------------------------- 확인 1
    section("1", "확인 1 — 로그인 없이 자료를 직접 요청하면 거절",
            "같은 주소를 A의 쿠키로 보내면 200, 쿠키 없이 보내면 401. 서버 관문: api/index.py `gate()`")
    anon = requests.Session()
    call(a["s"], "GET", "/api/plans", "A의 쿠키로 계획 목록", 200, side="ok")
    call(anon, "GET", "/api/plans", "쿠키 없이 같은 주소", 401, side="no")
    call(anon, "GET", f"/api/plans/{a['pid']}", "쿠키 없이 A의 계획 번호를 직접", 401, side="no")
    call(anon, "GET", "/api/export", "쿠키 없이 내 자료 전체 내보내기", 401, side="no")
    call(anon, "POST", "/api/plans", "쿠키 없이 계획 만들기", 401,
         {"title": "x", "start_date": "2026-10-07", "end_date": "2026-10-07", "priority": "low",
          "success_criteria": "x", "estimated_minutes": 1}, side="no")

    # ---------------------------------------------------------------- 확인 2
    section("2", "확인 2 — 남의 자료를 읽기·고치기·지우기 (A→B, B→A 양방향)",
            "주인이 보내면 200, 남이 같은 주소로 보내면 404(있는지조차 알려주지 않음). 공격 전후로 주인의 자료 건수가 같아야 한다. "
            "막는 곳: api/index.py `OwnerScoped` + DB 복합 외래키")
    for who, them, label in ((a, b, "A → B"), (b, a, "B → A")):
        out.append(f"### {label}\n")
        s, p, t, l, rv = who["s"], them["pid"], them["tid"], them["lid"], them["rid"]
        before = counts(them["s"])
        call(them["s"], "GET", f"/api/plans/{p}", f"{label[-1]}(주인)가 자기 계획 읽기", 200, side="ok")
        call(s, "GET", f"/api/plans/{p}", f"{label}: 남의 계획 읽기", 404, side="no")
        call(s, "GET", f"/api/plans/{p}/tasks", f"{label}: 남의 할 일 목록 읽기", 404, side="no")
        call(s, "GET", f"/api/reviews/{rv}", f"{label}: 남의 돌아보기 읽기", 404, side="no")
        call(them["s"], "PATCH", f"/api/tasks/{t}", f"{label[-1]}(주인)가 자기 할 일 고치기 (같은 값)", 200,
             {"title": f"{label[-1]}의 할 일"}, side="ok")
        call(s, "PATCH", f"/api/plans/{p}", f"{label}: 남의 계획 고치기", 404, {"title": "가로채기", "change_note": "x"}, side="no")
        call(s, "PATCH", f"/api/tasks/{t}", f"{label}: 남의 할 일 고치기", 404, {"title": "가로채기"}, side="no")
        call(s, "PUT", f"/api/plans/{p}/review", f"{label}: 남의 돌아보기 덮어쓰기", 404,
             {"miss_pattern": "on_track", "lesson": "덮어쓰기"}, side="no")
        call(s, "POST", f"/api/plans/{p}/tasks", f"{label}: 남의 계획에 할 일 끼워 넣기", 404,
             {"title": "끼워넣기", "planned_minutes": 5}, side="no")
        call(s, "DELETE", f"/api/logs/{l}", f"{label}: 남의 실행 기록 지우기", 404, side="no")
        call(s, "DELETE", f"/api/tasks/{t}", f"{label}: 남의 할 일 지우기", 404, side="no")
        call(s, "DELETE", f"/api/plans/{p}", f"{label}: 남의 계획 지우기", 404, side="no")
        after = counts(them["s"])
        fact(f"{label}: 공격 전후 {label[-1]}의 자료 건수가 같음 (주인이 자기 쿠키로 셈)", before == after,
             f"전: {fmt_counts(before)}\n후: {fmt_counts(after)}\n→ {'같음 — 바뀌거나 새로 생긴 것 없음' if before == after else '다름!'}",
             side="no")
        d = them["s"].get(BASE + f"/api/plans/{p}").json()
        intact = (d["plan"]["title"] == f"{label[-1]}의 비공개 계획" and d["review"]["lesson"] == f"{label[-1]}의 고칠 점"
                  and [x["title"] for x in d["tasks"]] == [f"{label[-1]}의 할 일"] and len(d["logs"]) == 1)
        fact(f"{label}: 공격 뒤 {label[-1]}의 내용 그대로", intact,
             f"계획: {d['plan']['title']} / 할 일: {[x['title'] for x in d['tasks']]} / "
             f"고칠 점: {d['review']['lesson']} / 실행 기록 {len(d['logs'])}건")
        lst = s.get(BASE + "/api/plans").json()
        mixed = any(x["id"] == p for x in lst)
        fact(f"{label}: {label[0]}의 계획 목록에 {label[-1]}의 계획이 섞이지 않음", not mixed,
             f"GET /api/plans → {label[0]}의 목록 제목: {sorted(x['title'] for x in lst)}")

    # ---------------------------------------------------------------- 확인 3
    section("3", "확인 3 — 주소·헤더·본문에 남의 계정 번호를 적어 보내도 내 것만",
            f"서버는 '누구인지'를 쿠키로 찾은 세션에서만 정한다. 주소의 ?user_id=, 헤더 X-User-Id, 본문 user_id는 무시하거나 덮어쓴다. "
            f"(A의 계정 번호 {a['uid']}, B의 계정 번호 {b['uid']} — 각자 만든 계획의 user_id에서 읽음)")
    b_before = counts(b["s"])
    r = call(a["s"], "GET", "/api/plans", "A가 평소대로 내 목록", 200, side="ok")
    a_titles = sorted(x["title"] for x in r.json())
    for path, hdr, title in ((f"/api/plans?user_id={b['uid']}", None, "주소에 B의 계정 번호"),
                             (f"/api/plans?user_id=eq.{b['uid']}", None, "주소에 DB 필터 모양으로 B의 번호"),
                             ("/api/plans", {"X-User-Id": str(b["uid"])}, "헤더에 B의 계정 번호")):
        r = call(a["s"], "GET", path, f"A의 쿠키 + {title}", 200, show_headers=hdr)
        got = sorted(x["title"] for x in r.json())
        fact(f"{title} → 돌아온 목록이 A의 것과 같고 B의 계획 없음",
             got == a_titles and not any("B의" in x for x in got), f"돌아온 제목: {got}\nA의 평소 목록: {a_titles}", side="no")
    r = call(a["s"], "GET", f"/api/export?user_id={b['uid']}", "A의 쿠키 + 주소에 B 번호를 붙여 내보내기", 200)
    ex = r.json()
    fact("내보내기 결과의 계정이 A이고 B의 자료 없음",
         ex["account"] == a["email"] and "B의" not in json.dumps(ex, ensure_ascii=False),
         f"account = {ex['account'].split('@')[0][:14]}…@example.com (A), 건수: {fmt_counts(ex['counts'])}", side="no")
    r = call(a["s"], "POST", "/api/plans", "A의 쿠키 + 본문에 user_id = B로 계획 만들기", 201,
             {"user_id": b["uid"], "title": "B 것인 척", "start_date": "2026-10-07", "end_date": "2026-10-07",
              "priority": "low", "success_criteria": "x", "estimated_minutes": 1},
             note="→ 만들어지기는 하지만 주인은 B가 아니라 A (응답의 user_id 확인).")
    spoof = r.json()
    fact("본문에 B를 적어 만든 계획의 주인이 A", spoof.get("user_id") == a["uid"],
         f"응답 user_id = {spoof.get('user_id')} (A = {a['uid']}, B = {b['uid']})", side="no")
    call(a["s"], "PATCH", f"/api/plans/{a['pid']}", "A가 자기 계획의 주인을 B로 바꾸려 함 (본문 user_id)", 200,
         {"user_id": b["uid"], "title": "A의 비공개 계획", "change_note": "주인 바꾸기 시도"})
    owner_now = a["s"].get(BASE + f"/api/plans/{a['pid']}").json()["plan"]["user_id"]
    fact("주인 바꾸기 시도 뒤에도 A의 계획 주인은 A", owner_now == a["uid"], f"user_id = {owner_now}", side="no")
    b_after = counts(b["s"])
    fact("확인 3 전후 B의 자료 건수 (B 쪽에 새로 생긴 것 없음)", b_before == b_after,
         f"전: {fmt_counts(b_before)}\n후: {fmt_counts(b_after)}", side="no")
    a["s"].delete(BASE + f"/api/plans/{spoof['id']}", headers=H)

    # ---------------------------------------------------------------- 확인 4
    section("4", "확인 4 — 로그아웃한 뒤 같은 쿠키 값으로 다시 요청",
            "같은 주소·같은 방법·같은 쿠키 값. 로그아웃 전에는 200, 로그아웃 뒤에는 401. 서버가 로그아웃 때 세션 줄을 지우기 때문 "
            "(api/index.py `logout()`)")
    old = a["s"].cookies.get("pds_session")
    call(None, "GET", f"/api/plans/{a['pid']}", "로그아웃 전: 이 쿠키로 A의 계획", 200, cookie=old, side="ok")
    call(a["s"], "POST", "/api/auth/logout", "A 로그아웃", 200)
    call(None, "GET", f"/api/plans/{a['pid']}", "로그아웃 뒤: 아까와 같은 쿠키 값 그대로", 401, cookie=old, side="no",
         note="→ 쿠키 값이 같아도 서버에 그 값의 세션이 없어 거절된다.")

    # ---------------------------------------------------------------- 확인 5
    section("5", "확인 5 — 비밀번호를 바꾸면 예전 쿠키는 모두 무효",
            "A가 두 곳(브라우저 1·2)에서 로그인 → 브라우저 1에서 비밀번호 변경 → 브라우저 2의 예전 쿠키로 같은 요청 → 401. "
            "(api/index.py `change_password()`가 그 계정의 세션을 전부 지움)")
    a1, a2 = requests.Session(), requests.Session()
    call(a1, "POST", "/api/auth/login", "A 로그인 (브라우저 1)", 200, {"email": a["email"], "password": pw})
    call(a2, "POST", "/api/auth/login", "A 로그인 (브라우저 2)", 200, {"email": a["email"], "password": pw})
    old2 = a2.cookies.get("pds_session")
    call(None, "GET", f"/api/plans/{a['pid']}", "바꾸기 전: 브라우저 2의 쿠키로 A의 계획", 200, cookie=old2, side="ok")
    new_pw = secrets.token_urlsafe(12)
    call(a1, "POST", "/api/auth/password", "브라우저 1에서 비밀번호 바꾸기", 200,
         {"current_password": pw, "new_password": new_pw})
    call(None, "GET", f"/api/plans/{a['pid']}", "바꾼 뒤: 브라우저 2의 같은 쿠키 값 그대로", 401, cookie=old2, side="no")
    call(requests.Session(), "POST", "/api/auth/login", "바꾼 뒤: 예전 비밀번호로 로그인", 401,
         {"email": a["email"], "password": pw}, side="no")
    call(a1, "GET", f"/api/plans/{a['pid']}", "바꾼 뒤: 브라우저 1(새로 받은 쿠키)은 계속 됨", 200, side="ok")
    a["s"], a["pw"] = a1, new_pw

    # ---------------------------------------------------------------- 그 밖
    section("etc", "그 밖의 거절", "다섯 가지 밖의 확인 — 위조 쿠키, 다른 사이트가 몰래 보내는 쓰기, 틀린 비밀번호와 없는 이메일")
    call(None, "GET", "/api/plans", "지어낸 쿠키 값으로 요청", 401, cookie="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    bc = b["s"].cookies.get("pds_session")
    call(None, "POST", f"/api/plans/{b['pid']}/tasks", "B의 쿠키는 있지만 X-Requested-With 헤더 없이 쓰기", 403,
         {"title": "몰래", "planned_minutes": 5}, cookie=bc, headers={})
    r1 = call(requests.Session(), "POST", "/api/auth/login", "B 이메일 + 틀린 비밀번호", 401,
              {"email": b["email"], "password": "wrong-password-123"})
    r2 = call(requests.Session(), "POST", "/api/auth/login", "가입한 적 없는 이메일", 401,
              {"email": f"nobody-{secrets.token_hex(3)}@example.com", "password": "wrong-password-123"})
    fact("틀린 비밀번호와 없는 이메일의 안내 문장이 같음", r1.json() == r2.json(),
         f"틀린 비밀번호: {r1.json()}\n없는 이메일:   {r2.json()}")

    # ---------------------------------------------------------------- 계정 삭제
    if PAUSE:
        sys.stderr.write(
            "\n[멈춤] Supabase SQL Editor에서 docs/hash-check.sql을 실행해 두 계정의 저장값을 비교하세요.\n"
            f"       대상 이메일: {a['email']} , {b['email']}\n"
            "       (둘의 비밀번호는 같습니다.) 다 봤으면 Enter → 두 계정을 지우고 끝냅니다.\n"
            "       ⚠ 멈춘 동안 SQL로 계정을 지우지 마세요. 정리는 이 스크립트가 Enter 뒤에 합니다.\n")
        sys.stderr.flush()
        sys.stdin.readline()
    section("del", "계정 삭제 — 내 자료가 함께 지워짐",
            "users 한 줄을 지우면 DB 외래키(ON DELETE CASCADE)가 계획·할 일·기록·돌아보기·이력·세션을 같이 지운다")
    b_before = counts(b["s"])
    a_cookie = a["s"].cookies.get("pds_session")
    call(a["s"], "DELETE", "/api/auth/account", "A 계정 삭제 (지금 비밀번호 + 확인 문구)", 200,
         {"password": a["pw"], "confirm": "계정 삭제"})
    call(None, "GET", "/api/plans", "삭제 뒤: A의 쿠키 값으로", 401, cookie=a_cookie)
    call(requests.Session(), "POST", "/api/auth/login", "삭제 뒤: A로 로그인", 401, {"email": a["email"], "password": a["pw"]})
    b_after = counts(b["s"])
    fact("A를 지워도 B의 자료 건수는 그대로", b_before == b_after,
         f"전: {fmt_counts(b_before)}\n후: {fmt_counts(b_after)}")
    call(b["s"], "DELETE", "/api/auth/account", "정리: B 계정 삭제", 200, {"password": b["pw"], "confirm": "계정 삭제"})

    # ---------------------------------------------------------------- 요약
    passed = sum(1 for r in results if r[5])
    titles = {n: t for n, _, t, *_ in results}
    names = {"1": "로그인 없이 자료 요청", "2": "남의 자료 읽기·고치기·지우기 (양방향)",
             "3": "주소·헤더·본문에 남의 계정을 적어 보내기", "4": "로그아웃 뒤 같은 쿠키",
             "5": "비밀번호 바꾼 뒤 예전 쿠키"}
    status = {n: s for n, _, _, _, s, _ in results}
    side_by_side = ["## 확인 다섯 가지 — 성공한 요청과 거절된 요청 나란히\n",
                    "| 확인 | 성공한 요청 → 응답 | 거절된 요청 → 응답 (확인 3은 '무시되고 내 것만' 나온 결과) |", "|---|---|---|"]
    for k, name in names.items():
        p = pairs.get(k, {"ok": [], "no": []})
        oks = "<br>".join(f"#{n} {titles[n]} → **{status[n]}**" for n in p["ok"])
        nos = "<br>".join(f"#{n} {titles[n]} → **{status[n]}**" for n in p["no"])
        side_by_side.append(f"| {k}. {name} | {oks} | {nos} |")
    summary = ["## 요약\n", f"**{passed} / {len(results)} 통과**\n",
               "| # | 묶음 | 확인 | 기대 | 실제 | 결과 |", "|---|---|---|---|---|---|"]
    for n, grp, title, exp, got, ok in results:
        summary.append(f"| {n} | {grp} | {title} | {exp} | {got} | {'✅' if ok else '❌'} |")
    emit("\n".join(out[:1] + side_by_side + [""] + summary + [""] + out[1:]))
    sys.stderr.write(f"결과: {passed} / {len(results)} 통과\n")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if isinstance(e.code, str):                      # 중간에 멈추면 여기까지의 기록이라도 남긴다
            emit("\n".join(out) + "\n\n## ❌ 실행 중단\n\n```\n" + e.code.strip() + "\n```")
            sys.stderr.write(e.code + "\n")
            sys.exit(1)
        raise
