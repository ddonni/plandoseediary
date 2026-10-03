#!/usr/bin/env python3
"""
비밀키 점검 — 소스 파일, Git 기록 전체, (선택) 배포된 사이트에 비밀키 원문이 있는지 찾는다.

  python scripts/check_secrets.py                       # 지금 파일 + Git 기록
  python scripts/check_secrets.py --url https://plandoseediary.vercel.app

찾는 것
  - Supabase 새 비밀키  sb_secret_...
  - 예전 service_role 키 (JWT 안에 "role":"service_role")
  - SUPABASE_SECRET_KEY= 뒤에 실제 값이 적힌 줄 (.env.example의 자리표시 값은 제외)
  - Git이 추적하는 .env 파일
찾으면 종료 코드 1, 없으면 0. 찾은 값은 앞 12글자만 보여 준다(점검 결과가 또 새지 않게).
"""
import base64
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".vercel"}
PLACEHOLDERS = {"sb_secret_xxxxxxxx"}

NEW_KEY = re.compile(r"sb_secret_[A-Za-z0-9_\-]{8,}")
JWT = re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")
ENV_LINE = re.compile(r"SUPABASE_SECRET_KEY\s*=\s*['\"]?([^\s'\"#]+)")


def is_service_role_jwt(token: str) -> bool:
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return claims.get("role") == "service_role"
    except Exception:
        return False


def scan_text(text: str):
    hits = []
    for m in NEW_KEY.finditer(text):
        if m.group(0) not in PLACEHOLDERS and "TESTONLY" not in m.group(0):
            hits.append(("Supabase 비밀키(sb_secret_)", m.group(0)))
    for m in JWT.finditer(text):
        if is_service_role_jwt(m.group(0)):
            hits.append(("service_role JWT", m.group(0)))
    for m in ENV_LINE.finditer(text):
        v = m.group(1)
        if v not in PLACEHOLDERS and not v.startswith("$") and "TESTONLY" not in v and v not in ('",', ")"):
            if NEW_KEY.fullmatch(v) or JWT.fullmatch(v):
                hits.append(("SUPABASE_SECRET_KEY 값", v))
    return hits


def show(where, hits):
    for kind, value in hits:
        print(f"  ✗ {where}: {kind} {value[:12]}…")


def scan_files():
    found = 0
    for p in ROOT.rglob("*"):
        if p.is_dir() or any(part in SKIP_DIRS for part in p.parts) or p.name == Path(__file__).name:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = scan_text(text)
        show(p.relative_to(ROOT), hits)
        found += len(hits)
    return found


def scan_git():
    if not (ROOT / ".git").exists():
        print("  - Git 저장소가 아님: 건너뜀 (저장소 폴더 안에서 다시 실행하세요)")
        return 0
    found = 0
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    for f in tracked:
        if Path(f).name == ".env" or (Path(f).name.startswith(".env.") and f != ".env.example"):
            print(f"  ✗ Git이 추적 중인 환경변수 파일: {f}")
            found += 1
    log = subprocess.run(["git", "log", "--all", "-p", "--no-color"], cwd=ROOT,
                         capture_output=True, text=True, errors="replace").stdout
    hits = [h for h in scan_text(log)]
    show("Git 기록(모든 커밋·브랜치)", hits)
    return found + len(hits)


def scan_url(base):
    base = base.rstrip("/")
    found = 0
    pages = ["/", "/index.html", "/api/health", "/api/meta", "/api/plans", "/api/reviews", "/api/export",
             "/api/nope", "/vercel.json", "/api/index.py", "/.env", "/requirements.txt"]
    for path in pages:
        try:
            with urllib.request.urlopen(base + path, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
                status = r.status
        except urllib.error.HTTPError as e:
            body, status = e.read().decode("utf-8", "replace"), e.code
        except Exception as e:
            print(f"  - {path}: 열 수 없음 ({e})")
            continue
        hits = scan_text(body)
        if path in ("/api/index.py", "/.env") and status == 200 and "SUPABASE" in body:
            hits.append(("서버 코드/환경 파일이 그대로 공개됨", path))
        show(f"{base}{path} ({status})", hits)
        found += len(hits)
    return found


def main():
    args = sys.argv[1:]
    total = 0
    print("① 소스 파일")
    total += scan_files()
    print("② Git 기록")
    total += scan_git()
    if "--url" in args:
        url = args[args.index("--url") + 1]
        print(f"③ 배포된 사이트 {url} (화면 코드·API 응답)")
        total += scan_url(url)
    print()
    if total:
        print(f"✗ 비밀키로 보이는 값 {total}건. 키를 Supabase에서 새로 발급(교체)하고, Git 기록에서 지우세요.")
        sys.exit(1)
    print("✓ 비밀키 원문을 찾지 못했습니다.")


if __name__ == "__main__":
    main()
