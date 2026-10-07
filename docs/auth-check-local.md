# 인증 확인 기록

- 대상: http://localhost:5055
- 실행: 2026-10-07 12:08 (서울)
- 계정: 실행할 때마다 새로 만든 시험 계정 A·B (진짜 계정 아님, 둘의 비밀번호는 일부러 같게). 쿠키는 앞 4글자만, 비밀번호는 전부 가림.
- 끝에서 두 계정을 '계정 삭제'로 지운다.

## 확인 다섯 가지 — 성공한 요청과 거절된 요청 나란히

| 확인 | 성공한 요청 → 응답 | 거절된 요청 → 응답 (확인 3은 '무시되고 내 것만' 나온 결과) |
|---|---|---|
| 1. 로그인 없이 자료 요청 | #4 A의 쿠키로 계획 목록 → **200** | #5 쿠키 없이 같은 주소 → **401**<br>#6 쿠키 없이 A의 계획 번호를 직접 → **401**<br>#7 쿠키 없이 내 자료 전체 내보내기 → **401**<br>#8 쿠키 없이 계획 만들기 → **401** |
| 2. 남의 자료 읽기·고치기·지우기 (양방향) | #9 B(주인)가 자기 계획 읽기 → **200**<br>#13 B(주인)가 자기 할 일 고치기 (같은 값) → **200**<br>#24 A(주인)가 자기 계획 읽기 → **200**<br>#28 A(주인)가 자기 할 일 고치기 (같은 값) → **200** | #10 A → B: 남의 계획 읽기 → **404**<br>#11 A → B: 남의 할 일 목록 읽기 → **404**<br>#12 A → B: 남의 돌아보기 읽기 → **404**<br>#14 A → B: 남의 계획 고치기 → **404**<br>#15 A → B: 남의 할 일 고치기 → **404**<br>#16 A → B: 남의 돌아보기 덮어쓰기 → **404**<br>#17 A → B: 남의 계획에 할 일 끼워 넣기 → **404**<br>#18 A → B: 남의 실행 기록 지우기 → **404**<br>#19 A → B: 남의 할 일 지우기 → **404**<br>#20 A → B: 남의 계획 지우기 → **404**<br>#21 A → B: 공격 전후 B의 자료 건수가 같음 (주인이 자기 쿠키로 셈) → **맞음**<br>#25 B → A: 남의 계획 읽기 → **404**<br>#26 B → A: 남의 할 일 목록 읽기 → **404**<br>#27 B → A: 남의 돌아보기 읽기 → **404**<br>#29 B → A: 남의 계획 고치기 → **404**<br>#30 B → A: 남의 할 일 고치기 → **404**<br>#31 B → A: 남의 돌아보기 덮어쓰기 → **404**<br>#32 B → A: 남의 계획에 할 일 끼워 넣기 → **404**<br>#33 B → A: 남의 실행 기록 지우기 → **404**<br>#34 B → A: 남의 할 일 지우기 → **404**<br>#35 B → A: 남의 계획 지우기 → **404**<br>#36 B → A: 공격 전후 A의 자료 건수가 같음 (주인이 자기 쿠키로 셈) → **맞음** |
| 3. 주소·헤더·본문에 남의 계정을 적어 보내기 | #39 A가 평소대로 내 목록 → **200** | #41 주소에 B의 계정 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 → **맞음**<br>#43 주소에 DB 필터 모양으로 B의 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 → **맞음**<br>#45 헤더에 B의 계정 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 → **맞음**<br>#47 내보내기 결과의 계정이 A이고 B의 자료 없음 → **맞음**<br>#49 본문에 B를 적어 만든 계획의 주인이 A → **맞음**<br>#51 주인 바꾸기 시도 뒤에도 A의 계획 주인은 A → **맞음**<br>#52 확인 3 전후 B의 자료 건수 (B 쪽에 새로 생긴 것 없음) → **맞음** |
| 4. 로그아웃 뒤 같은 쿠키 | #53 로그아웃 전: 이 쿠키로 A의 계획 → **200** | #55 로그아웃 뒤: 아까와 같은 쿠키 값 그대로 → **401** |
| 5. 비밀번호 바꾼 뒤 예전 쿠키 | #58 바꾸기 전: 브라우저 2의 쿠키로 A의 계획 → **200**<br>#62 바꾼 뒤: 브라우저 1(새로 받은 쿠키)은 계속 됨 → **200** | #60 바꾼 뒤: 브라우저 2의 같은 쿠키 값 그대로 → **401**<br>#61 바꾼 뒤: 예전 비밀번호로 로그인 → **401** |

## 요약

**72 / 72 통과**

| # | 묶음 | 확인 | 기대 | 실제 | 결과 |
|---|---|---|---|---|---|
| 1 | 0 | 시험 계정 A 가입 | 201 | 201 | ✅ |
| 2 | 0 | 시험 계정 B 가입 | 201 | 201 | ✅ |
| 3 | 0 | 이미 있는 A 이메일로 다시 가입 | 409 | 409 | ✅ |
| 4 | 1 | A의 쿠키로 계획 목록 | 200 | 200 | ✅ |
| 5 | 1 | 쿠키 없이 같은 주소 | 401 | 401 | ✅ |
| 6 | 1 | 쿠키 없이 A의 계획 번호를 직접 | 401 | 401 | ✅ |
| 7 | 1 | 쿠키 없이 내 자료 전체 내보내기 | 401 | 401 | ✅ |
| 8 | 1 | 쿠키 없이 계획 만들기 | 401 | 401 | ✅ |
| 9 | 2 | B(주인)가 자기 계획 읽기 | 200 | 200 | ✅ |
| 10 | 2 | A → B: 남의 계획 읽기 | 404 | 404 | ✅ |
| 11 | 2 | A → B: 남의 할 일 목록 읽기 | 404 | 404 | ✅ |
| 12 | 2 | A → B: 남의 돌아보기 읽기 | 404 | 404 | ✅ |
| 13 | 2 | B(주인)가 자기 할 일 고치기 (같은 값) | 200 | 200 | ✅ |
| 14 | 2 | A → B: 남의 계획 고치기 | 404 | 404 | ✅ |
| 15 | 2 | A → B: 남의 할 일 고치기 | 404 | 404 | ✅ |
| 16 | 2 | A → B: 남의 돌아보기 덮어쓰기 | 404 | 404 | ✅ |
| 17 | 2 | A → B: 남의 계획에 할 일 끼워 넣기 | 404 | 404 | ✅ |
| 18 | 2 | A → B: 남의 실행 기록 지우기 | 404 | 404 | ✅ |
| 19 | 2 | A → B: 남의 할 일 지우기 | 404 | 404 | ✅ |
| 20 | 2 | A → B: 남의 계획 지우기 | 404 | 404 | ✅ |
| 21 | 2 | A → B: 공격 전후 B의 자료 건수가 같음 (주인이 자기 쿠키로 셈) | 맞음 | 맞음 | ✅ |
| 22 | 2 | A → B: 공격 뒤 B의 내용 그대로 | 맞음 | 맞음 | ✅ |
| 23 | 2 | A → B: A의 계획 목록에 B의 계획이 섞이지 않음 | 맞음 | 맞음 | ✅ |
| 24 | 2 | A(주인)가 자기 계획 읽기 | 200 | 200 | ✅ |
| 25 | 2 | B → A: 남의 계획 읽기 | 404 | 404 | ✅ |
| 26 | 2 | B → A: 남의 할 일 목록 읽기 | 404 | 404 | ✅ |
| 27 | 2 | B → A: 남의 돌아보기 읽기 | 404 | 404 | ✅ |
| 28 | 2 | A(주인)가 자기 할 일 고치기 (같은 값) | 200 | 200 | ✅ |
| 29 | 2 | B → A: 남의 계획 고치기 | 404 | 404 | ✅ |
| 30 | 2 | B → A: 남의 할 일 고치기 | 404 | 404 | ✅ |
| 31 | 2 | B → A: 남의 돌아보기 덮어쓰기 | 404 | 404 | ✅ |
| 32 | 2 | B → A: 남의 계획에 할 일 끼워 넣기 | 404 | 404 | ✅ |
| 33 | 2 | B → A: 남의 실행 기록 지우기 | 404 | 404 | ✅ |
| 34 | 2 | B → A: 남의 할 일 지우기 | 404 | 404 | ✅ |
| 35 | 2 | B → A: 남의 계획 지우기 | 404 | 404 | ✅ |
| 36 | 2 | B → A: 공격 전후 A의 자료 건수가 같음 (주인이 자기 쿠키로 셈) | 맞음 | 맞음 | ✅ |
| 37 | 2 | B → A: 공격 뒤 A의 내용 그대로 | 맞음 | 맞음 | ✅ |
| 38 | 2 | B → A: B의 계획 목록에 A의 계획이 섞이지 않음 | 맞음 | 맞음 | ✅ |
| 39 | 3 | A가 평소대로 내 목록 | 200 | 200 | ✅ |
| 40 | 3 | A의 쿠키 + 주소에 B의 계정 번호 | 200 | 200 | ✅ |
| 41 | 3 | 주소에 B의 계정 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 | 맞음 | 맞음 | ✅ |
| 42 | 3 | A의 쿠키 + 주소에 DB 필터 모양으로 B의 번호 | 200 | 200 | ✅ |
| 43 | 3 | 주소에 DB 필터 모양으로 B의 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 | 맞음 | 맞음 | ✅ |
| 44 | 3 | A의 쿠키 + 헤더에 B의 계정 번호 | 200 | 200 | ✅ |
| 45 | 3 | 헤더에 B의 계정 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 | 맞음 | 맞음 | ✅ |
| 46 | 3 | A의 쿠키 + 주소에 B 번호를 붙여 내보내기 | 200 | 200 | ✅ |
| 47 | 3 | 내보내기 결과의 계정이 A이고 B의 자료 없음 | 맞음 | 맞음 | ✅ |
| 48 | 3 | A의 쿠키 + 본문에 user_id = B로 계획 만들기 | 201 | 201 | ✅ |
| 49 | 3 | 본문에 B를 적어 만든 계획의 주인이 A | 맞음 | 맞음 | ✅ |
| 50 | 3 | A가 자기 계획의 주인을 B로 바꾸려 함 (본문 user_id) | 200 | 200 | ✅ |
| 51 | 3 | 주인 바꾸기 시도 뒤에도 A의 계획 주인은 A | 맞음 | 맞음 | ✅ |
| 52 | 3 | 확인 3 전후 B의 자료 건수 (B 쪽에 새로 생긴 것 없음) | 맞음 | 맞음 | ✅ |
| 53 | 4 | 로그아웃 전: 이 쿠키로 A의 계획 | 200 | 200 | ✅ |
| 54 | 4 | A 로그아웃 | 200 | 200 | ✅ |
| 55 | 4 | 로그아웃 뒤: 아까와 같은 쿠키 값 그대로 | 401 | 401 | ✅ |
| 56 | 5 | A 로그인 (브라우저 1) | 200 | 200 | ✅ |
| 57 | 5 | A 로그인 (브라우저 2) | 200 | 200 | ✅ |
| 58 | 5 | 바꾸기 전: 브라우저 2의 쿠키로 A의 계획 | 200 | 200 | ✅ |
| 59 | 5 | 브라우저 1에서 비밀번호 바꾸기 | 200 | 200 | ✅ |
| 60 | 5 | 바꾼 뒤: 브라우저 2의 같은 쿠키 값 그대로 | 401 | 401 | ✅ |
| 61 | 5 | 바꾼 뒤: 예전 비밀번호로 로그인 | 401 | 401 | ✅ |
| 62 | 5 | 바꾼 뒤: 브라우저 1(새로 받은 쿠키)은 계속 됨 | 200 | 200 | ✅ |
| 63 | etc | 지어낸 쿠키 값으로 요청 | 401 | 401 | ✅ |
| 64 | etc | B의 쿠키는 있지만 X-Requested-With 헤더 없이 쓰기 | 403 | 403 | ✅ |
| 65 | etc | B 이메일 + 틀린 비밀번호 | 401 | 401 | ✅ |
| 66 | etc | 가입한 적 없는 이메일 | 401 | 401 | ✅ |
| 67 | etc | 틀린 비밀번호와 없는 이메일의 안내 문장이 같음 | 맞음 | 맞음 | ✅ |
| 68 | del | A 계정 삭제 (지금 비밀번호 + 확인 문구) | 200 | 200 | ✅ |
| 69 | del | 삭제 뒤: A의 쿠키 값으로 | 401 | 401 | ✅ |
| 70 | del | 삭제 뒤: A로 로그인 | 401 | 401 | ✅ |
| 71 | del | A를 지워도 B의 자료 건수는 그대로 | 맞음 | 맞음 | ✅ |
| 72 | del | 정리: B 계정 삭제 | 200 | 200 | ✅ |

## 0. 준비 — 두 계정과 각자의 자료

#### 1. 시험 계정 A 가입 — ✅

```http
POST /api/auth/signup
Cookie: (없음)

{"email": "authcheck-a-11383c@example.com", "password": "(가림)"}

HTTP/1.1 201
Set-Cookie: pds_session=S8yv…생략; Expires=Wed, 14 Oct 2026 03:08:32 GMT; Max-Age=604800; HttpOnly; Path=/; SameSite=Lax
{"email": "authcheck-a-11383c@example.com"}
```

#### 2. 시험 계정 B 가입 — ✅

```http
POST /api/auth/signup
Cookie: (없음)

{"email": "authcheck-b-c7b5ae@example.com", "password": "(가림)"}

HTTP/1.1 201
Set-Cookie: pds_session=t_WE…생략; Expires=Wed, 14 Oct 2026 03:08:33 GMT; Max-Age=604800; HttpOnly; Path=/; SameSite=Lax
{"email": "authcheck-b-c7b5ae@example.com"}
```

#### 3. 이미 있는 A 이메일로 다시 가입 — ✅

```http
POST /api/auth/signup
Cookie: (없음)

{"email": "authcheck-a-11383c@example.com", "password": "(가림)"}

HTTP/1.1 409
{"error": "이미 가입한 이메일입니다"}
```

A의 자료: 계획 #4 · 할 일 #22 · 실행 기록 #13 · 돌아보기 #2  
B의 자료: 계획 #5 · 할 일 #23 · 실행 기록 #14 · 돌아보기 #3

## 확인 1 — 로그인 없이 자료를 직접 요청하면 거절

> 같은 주소를 A의 쿠키로 보내면 200, 쿠키 없이 보내면 401. 서버 관문: api/index.py `gate()`

#### 4. A의 쿠키로 계획 목록 · 성공한 요청 — ✅

```http
GET /api/plans
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
[{"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09:00", "end_date": "2026-10-07", "estimated_minutes": 60, "from_review_id": null, "goal": null, "has_review": true, "id": 4, "priority": "medium", "question": null, "rule": null, "start_date": "2026-10-07", "success_criteria": "A만 볼 수 있다", "summary": {"actual": 20, "blocked": 1, "completions": 0, "done": 0, "in_progress": 1, "logs":
```

#### 5. 쿠키 없이 같은 주소 · 거절된 요청 — ✅

```http
GET /api/plans
Cookie: (없음)

HTTP/1.1 401
{"error": "로그인이 필요합니다"}
```

#### 6. 쿠키 없이 A의 계획 번호를 직접 · 거절된 요청 — ✅

```http
GET /api/plans/4
Cookie: (없음)

HTTP/1.1 401
{"error": "로그인이 필요합니다"}
```

#### 7. 쿠키 없이 내 자료 전체 내보내기 · 거절된 요청 — ✅

```http
GET /api/export
Cookie: (없음)

HTTP/1.1 401
{"error": "로그인이 필요합니다"}
```

#### 8. 쿠키 없이 계획 만들기 · 거절된 요청 — ✅

```http
POST /api/plans
Cookie: (없음)

{"title": "x", "start_date": "2026-10-07", "end_date": "2026-10-07", "priority": "low", "success_criteria": "x", "estimated_minutes": 1}

HTTP/1.1 401
{"error": "로그인이 필요합니다"}
```

## 확인 2 — 남의 자료를 읽기·고치기·지우기 (A→B, B→A 양방향)

> 주인이 보내면 200, 남이 같은 주소로 보내면 404(있는지조차 알려주지 않음). 공격 전후로 주인의 자료 건수가 같아야 한다. 막는 곳: api/index.py `OwnerScoped` + DB 복합 외래키

### A → B

#### 9. B(주인)가 자기 계획 읽기 · 성공한 요청 — ✅

```http
GET /api/plans/5
Cookie: pds_session=t_WE…생략

HTTP/1.1 200
{"completions": [], "logs": [{"actual_minutes": 20, "blocker": "B의 막힌 이유", "created_at": "2026-10-07T12:08:33.174533+09:00", "done_date": "2026-10-07", "ended_at": "2026-10-07T09:20:00+09:00", "id": 14, "note": null, "plan_id": 5, "started_at": "2026-10-07T09:00:00+09:00", "status": "partial", "task_id": 23, "user_id": 2}], "plan": {"change_note": null, "created_at": "2026-10-07T12:08:33.156042+09
```

#### 10. A → B: 남의 계획 읽기 · 거절된 요청 — ✅

```http
GET /api/plans/5
Cookie: pds_session=S8yv…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 11. A → B: 남의 할 일 목록 읽기 · 거절된 요청 — ✅

```http
GET /api/plans/5/tasks
Cookie: pds_session=S8yv…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 12. A → B: 남의 돌아보기 읽기 · 거절된 요청 — ✅

```http
GET /api/reviews/3
Cookie: pds_session=S8yv…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 13. B(주인)가 자기 할 일 고치기 (같은 값) · 성공한 요청 — ✅

```http
PATCH /api/tasks/23
Cookie: pds_session=t_WE…생략

{"title": "B의 할 일"}

HTTP/1.1 200
{"created_at": "2026-10-07T12:08:33.165521+09:00", "deleted_at": null, "done_at": null, "due_date": null, "id": 23, "plan_id": 5, "planned_minutes": 30, "priority": "medium", "status": "open", "tags": [], "title": "B의 할 일", "updated_at": "2026-10-07T12:08:33.269293+09:00", "user_id": 2}
```

#### 14. A → B: 남의 계획 고치기 · 거절된 요청 — ✅

```http
PATCH /api/plans/5
Cookie: pds_session=S8yv…생략

{"title": "가로채기", "change_note": "x"}

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 15. A → B: 남의 할 일 고치기 · 거절된 요청 — ✅

```http
PATCH /api/tasks/23
Cookie: pds_session=S8yv…생략

{"title": "가로채기"}

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 16. A → B: 남의 돌아보기 덮어쓰기 · 거절된 요청 — ✅

```http
PUT /api/plans/5/review
Cookie: pds_session=S8yv…생략

{"miss_pattern": "on_track", "lesson": "덮어쓰기"}

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 17. A → B: 남의 계획에 할 일 끼워 넣기 · 거절된 요청 — ✅

```http
POST /api/plans/5/tasks
Cookie: pds_session=S8yv…생략

{"title": "끼워넣기", "planned_minutes": 5}

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 18. A → B: 남의 실행 기록 지우기 · 거절된 요청 — ✅

```http
DELETE /api/logs/14
Cookie: pds_session=S8yv…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 19. A → B: 남의 할 일 지우기 · 거절된 요청 — ✅

```http
DELETE /api/tasks/23
Cookie: pds_session=S8yv…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 20. A → B: 남의 계획 지우기 · 거절된 요청 — ✅

```http
DELETE /api/plans/5
Cookie: pds_session=S8yv…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 21. A → B: 공격 전후 B의 자료 건수가 같음 (주인이 자기 쿠키로 셈) — ✅

```
전: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
후: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
→ 같음 — 바뀌거나 새로 생긴 것 없음
```

#### 22. A → B: 공격 뒤 B의 내용 그대로 — ✅

```
계획: B의 비공개 계획 / 할 일: ['B의 할 일'] / 고칠 점: B의 고칠 점 / 실행 기록 1건
```

#### 23. A → B: A의 계획 목록에 B의 계획이 섞이지 않음 — ✅

```
GET /api/plans → A의 목록 제목: ['A의 비공개 계획']
```

### B → A

#### 24. A(주인)가 자기 계획 읽기 · 성공한 요청 — ✅

```http
GET /api/plans/4
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
{"completions": [], "logs": [{"actual_minutes": 20, "blocker": "A의 막힌 이유", "created_at": "2026-10-07T12:08:33.020592+09:00", "done_date": "2026-10-07", "ended_at": "2026-10-07T09:20:00+09:00", "id": 13, "note": null, "plan_id": 4, "started_at": "2026-10-07T09:00:00+09:00", "status": "partial", "task_id": 22, "user_id": 1}], "plan": {"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09
```

#### 25. B → A: 남의 계획 읽기 · 거절된 요청 — ✅

```http
GET /api/plans/4
Cookie: pds_session=t_WE…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 26. B → A: 남의 할 일 목록 읽기 · 거절된 요청 — ✅

```http
GET /api/plans/4/tasks
Cookie: pds_session=t_WE…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 27. B → A: 남의 돌아보기 읽기 · 거절된 요청 — ✅

```http
GET /api/reviews/2
Cookie: pds_session=t_WE…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 28. A(주인)가 자기 할 일 고치기 (같은 값) · 성공한 요청 — ✅

```http
PATCH /api/tasks/22
Cookie: pds_session=S8yv…생략

{"title": "A의 할 일"}

HTTP/1.1 200
{"created_at": "2026-10-07T12:08:33.008318+09:00", "deleted_at": null, "done_at": null, "due_date": null, "id": 22, "plan_id": 4, "planned_minutes": 30, "priority": "medium", "status": "open", "tags": [], "title": "A의 할 일", "updated_at": "2026-10-07T12:08:33.410264+09:00", "user_id": 1}
```

#### 29. B → A: 남의 계획 고치기 · 거절된 요청 — ✅

```http
PATCH /api/plans/4
Cookie: pds_session=t_WE…생략

{"title": "가로채기", "change_note": "x"}

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 30. B → A: 남의 할 일 고치기 · 거절된 요청 — ✅

```http
PATCH /api/tasks/22
Cookie: pds_session=t_WE…생략

{"title": "가로채기"}

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 31. B → A: 남의 돌아보기 덮어쓰기 · 거절된 요청 — ✅

```http
PUT /api/plans/4/review
Cookie: pds_session=t_WE…생략

{"miss_pattern": "on_track", "lesson": "덮어쓰기"}

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 32. B → A: 남의 계획에 할 일 끼워 넣기 · 거절된 요청 — ✅

```http
POST /api/plans/4/tasks
Cookie: pds_session=t_WE…생략

{"title": "끼워넣기", "planned_minutes": 5}

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 33. B → A: 남의 실행 기록 지우기 · 거절된 요청 — ✅

```http
DELETE /api/logs/13
Cookie: pds_session=t_WE…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 34. B → A: 남의 할 일 지우기 · 거절된 요청 — ✅

```http
DELETE /api/tasks/22
Cookie: pds_session=t_WE…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 35. B → A: 남의 계획 지우기 · 거절된 요청 — ✅

```http
DELETE /api/plans/4
Cookie: pds_session=t_WE…생략

HTTP/1.1 404
{"error": "해당 항목이 없습니다"}
```

#### 36. B → A: 공격 전후 A의 자료 건수가 같음 (주인이 자기 쿠키로 셈) — ✅

```
전: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
후: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
→ 같음 — 바뀌거나 새로 생긴 것 없음
```

#### 37. B → A: 공격 뒤 A의 내용 그대로 — ✅

```
계획: A의 비공개 계획 / 할 일: ['A의 할 일'] / 고칠 점: A의 고칠 점 / 실행 기록 1건
```

#### 38. B → A: B의 계획 목록에 A의 계획이 섞이지 않음 — ✅

```
GET /api/plans → B의 목록 제목: ['B의 비공개 계획']
```

## 확인 3 — 주소·헤더·본문에 남의 계정 번호를 적어 보내도 내 것만

> 서버는 '누구인지'를 쿠키로 찾은 세션에서만 정한다. 주소의 ?user_id=, 헤더 X-User-Id, 본문 user_id는 무시하거나 덮어쓴다. (A의 계정 번호 1, B의 계정 번호 2 — 각자 만든 계획의 user_id에서 읽음)

#### 39. A가 평소대로 내 목록 · 성공한 요청 — ✅

```http
GET /api/plans
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
[{"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09:00", "end_date": "2026-10-07", "estimated_minutes": 60, "from_review_id": null, "goal": null, "has_review": true, "id": 4, "priority": "medium", "question": null, "rule": null, "start_date": "2026-10-07", "success_criteria": "A만 볼 수 있다", "summary": {"actual": 20, "blocked": 1, "completions": 0, "done": 0, "in_progress": 1, "logs":
```

#### 40. A의 쿠키 + 주소에 B의 계정 번호 — ✅

```http
GET /api/plans?user_id=2
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
[{"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09:00", "end_date": "2026-10-07", "estimated_minutes": 60, "from_review_id": null, "goal": null, "has_review": true, "id": 4, "priority": "medium", "question": null, "rule": null, "start_date": "2026-10-07", "success_criteria": "A만 볼 수 있다", "summary": {"actual": 20, "blocked": 1, "completions": 0, "done": 0, "in_progress": 1, "logs":
```

#### 41. 주소에 B의 계정 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 — ✅

```
돌아온 제목: ['A의 비공개 계획']
A의 평소 목록: ['A의 비공개 계획']
```

#### 42. A의 쿠키 + 주소에 DB 필터 모양으로 B의 번호 — ✅

```http
GET /api/plans?user_id=eq.2
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
[{"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09:00", "end_date": "2026-10-07", "estimated_minutes": 60, "from_review_id": null, "goal": null, "has_review": true, "id": 4, "priority": "medium", "question": null, "rule": null, "start_date": "2026-10-07", "success_criteria": "A만 볼 수 있다", "summary": {"actual": 20, "blocked": 1, "completions": 0, "done": 0, "in_progress": 1, "logs":
```

#### 43. 주소에 DB 필터 모양으로 B의 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 — ✅

```
돌아온 제목: ['A의 비공개 계획']
A의 평소 목록: ['A의 비공개 계획']
```

#### 44. A의 쿠키 + 헤더에 B의 계정 번호 — ✅

```http
GET /api/plans
X-User-Id: 2
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
[{"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09:00", "end_date": "2026-10-07", "estimated_minutes": 60, "from_review_id": null, "goal": null, "has_review": true, "id": 4, "priority": "medium", "question": null, "rule": null, "start_date": "2026-10-07", "success_criteria": "A만 볼 수 있다", "summary": {"actual": 20, "blocked": 1, "completions": 0, "done": 0, "in_progress": 1, "logs":
```

#### 45. 헤더에 B의 계정 번호 → 돌아온 목록이 A의 것과 같고 B의 계획 없음 — ✅

```
돌아온 제목: ['A의 비공개 계획']
A의 평소 목록: ['A의 비공개 계획']
```

#### 46. A의 쿠키 + 주소에 B 번호를 붙여 내보내기 — ✅

```http
GET /api/export?user_id=2
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
{"account": "authcheck-a-11383c@example.com", "counts": {"logs": 1, "plan_revisions": 0, "plans": 1, "reviews": 1, "task_completions": 0, "tasks": 1}, "data": {"logs": [{"actual_minutes": 20, "blocker": "A의 막힌 이유", "created_at": "2026-10-07T12:08:33.020592+09:00", "done_date": "2026-10-07", "ended_at": "2026-10-07T09:20:00+09:00", "id": 13, "note": null, "plan_id": 4, "started_at": "2026-10-07T09:
```

#### 47. 내보내기 결과의 계정이 A이고 B의 자료 없음 — ✅

```
account = authcheck-a-11…@example.com (A), 건수: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
```

#### 48. A의 쿠키 + 본문에 user_id = B로 계획 만들기 — ✅

```http
POST /api/plans
Cookie: pds_session=S8yv…생략

{"user_id": 2, "title": "B 것인 척", "start_date": "2026-10-07", "end_date": "2026-10-07", "priority": "low", "success_criteria": "x", "estimated_minutes": 1}

HTTP/1.1 201
{"change_note": null, "created_at": "2026-10-07T12:08:33.590518+09:00", "end_date": "2026-10-07", "estimated_minutes": 1, "from_review_id": null, "goal": null, "id": 6, "priority": "low", "question": null, "rule": null, "start_date": "2026-10-07", "success_criteria": "x", "title": "B 것인 척", "updated_at": "2026-10-07T12:08:33.590518+09:00", "user_id": 1, "version": 1}
```
→ 만들어지기는 하지만 주인은 B가 아니라 A (응답의 user_id 확인).

#### 49. 본문에 B를 적어 만든 계획의 주인이 A — ✅

```
응답 user_id = 1 (A = 1, B = 2)
```

#### 50. A가 자기 계획의 주인을 B로 바꾸려 함 (본문 user_id) — ✅

```http
PATCH /api/plans/4
Cookie: pds_session=S8yv…생략

{"user_id": 2, "title": "A의 비공개 계획", "change_note": "주인 바꾸기 시도"}

HTTP/1.1 200
{"changed": false, "plan": {"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09:00", "end_date": "2026-10-07", "estimated_minutes": 60, "from_review_id": null, "goal": null, "id": 4, "priority": "medium", "question": null, "rule": null, "start_date": "2026-10-07", "success_criteria": "A만 볼 수 있다", "title": "A의 비공개 계획", "updated_at": "2026-10-07T12:08:32.997124+09:00", "user_id": 1, "v
```

#### 51. 주인 바꾸기 시도 뒤에도 A의 계획 주인은 A — ✅

```
user_id = 1
```

#### 52. 확인 3 전후 B의 자료 건수 (B 쪽에 새로 생긴 것 없음) — ✅

```
전: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
후: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
```

## 확인 4 — 로그아웃한 뒤 같은 쿠키 값으로 다시 요청

> 같은 주소·같은 방법·같은 쿠키 값. 로그아웃 전에는 200, 로그아웃 뒤에는 401. 서버가 로그아웃 때 세션 줄을 지우기 때문 (api/index.py `logout()`)

#### 53. 로그아웃 전: 이 쿠키로 A의 계획 · 성공한 요청 — ✅

```http
GET /api/plans/4
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
{"completions": [], "logs": [{"actual_minutes": 20, "blocker": "A의 막힌 이유", "created_at": "2026-10-07T12:08:33.020592+09:00", "done_date": "2026-10-07", "ended_at": "2026-10-07T09:20:00+09:00", "id": 13, "note": null, "plan_id": 4, "started_at": "2026-10-07T09:00:00+09:00", "status": "partial", "task_id": 22, "user_id": 1}], "plan": {"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09
```

#### 54. A 로그아웃 — ✅

```http
POST /api/auth/logout
Cookie: pds_session=S8yv…생략

HTTP/1.1 200
Set-Cookie: pds_session=""; Expires=Wed, 07 Oct 2026 03:08:33 GMT; Max-Age=0; HttpOnly; Path=/; SameSite=Lax
{"ok": true}
```

#### 55. 로그아웃 뒤: 아까와 같은 쿠키 값 그대로 · 거절된 요청 — ✅

```http
GET /api/plans/4
Cookie: pds_session=S8yv…생략

HTTP/1.1 401
{"error": "로그인이 필요합니다"}
```
→ 쿠키 값이 같아도 서버에 그 값의 세션이 없어 거절된다.

## 확인 5 — 비밀번호를 바꾸면 예전 쿠키는 모두 무효

> A가 두 곳(브라우저 1·2)에서 로그인 → 브라우저 1에서 비밀번호 변경 → 브라우저 2의 예전 쿠키로 같은 요청 → 401. (api/index.py `change_password()`가 그 계정의 세션을 전부 지움)

#### 56. A 로그인 (브라우저 1) — ✅

```http
POST /api/auth/login
Cookie: (없음)

{"email": "authcheck-a-11383c@example.com", "password": "(가림)"}

HTTP/1.1 200
Set-Cookie: pds_session=GiNn…생략; Expires=Wed, 14 Oct 2026 03:08:33 GMT; Max-Age=604800; HttpOnly; Path=/; SameSite=Lax
{"email": "authcheck-a-11383c@example.com"}
```

#### 57. A 로그인 (브라우저 2) — ✅

```http
POST /api/auth/login
Cookie: (없음)

{"email": "authcheck-a-11383c@example.com", "password": "(가림)"}

HTTP/1.1 200
Set-Cookie: pds_session=VQjR…생략; Expires=Wed, 14 Oct 2026 03:08:33 GMT; Max-Age=604800; HttpOnly; Path=/; SameSite=Lax
{"email": "authcheck-a-11383c@example.com"}
```

#### 58. 바꾸기 전: 브라우저 2의 쿠키로 A의 계획 · 성공한 요청 — ✅

```http
GET /api/plans/4
Cookie: pds_session=VQjR…생략

HTTP/1.1 200
{"completions": [], "logs": [{"actual_minutes": 20, "blocker": "A의 막힌 이유", "created_at": "2026-10-07T12:08:33.020592+09:00", "done_date": "2026-10-07", "ended_at": "2026-10-07T09:20:00+09:00", "id": 13, "note": null, "plan_id": 4, "started_at": "2026-10-07T09:00:00+09:00", "status": "partial", "task_id": 22, "user_id": 1}], "plan": {"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09
```

#### 59. 브라우저 1에서 비밀번호 바꾸기 — ✅

```http
POST /api/auth/password
Cookie: pds_session=GiNn…생략

{"current_password": "(가림)", "new_password": "(가림)"}

HTTP/1.1 200
Set-Cookie: pds_session=DDdY…생략; Expires=Wed, 14 Oct 2026 03:08:34 GMT; Max-Age=604800; HttpOnly; Path=/; SameSite=Lax
{"email": "authcheck-a-11383c@example.com", "ended_sessions": 2}
```

#### 60. 바꾼 뒤: 브라우저 2의 같은 쿠키 값 그대로 · 거절된 요청 — ✅

```http
GET /api/plans/4
Cookie: pds_session=VQjR…생략

HTTP/1.1 401
{"error": "로그인이 필요합니다"}
```

#### 61. 바꾼 뒤: 예전 비밀번호로 로그인 · 거절된 요청 — ✅

```http
POST /api/auth/login
Cookie: (없음)

{"email": "authcheck-a-11383c@example.com", "password": "(가림)"}

HTTP/1.1 401
{"error": "이메일 또는 비밀번호가 맞지 않습니다"}
```

#### 62. 바꾼 뒤: 브라우저 1(새로 받은 쿠키)은 계속 됨 · 성공한 요청 — ✅

```http
GET /api/plans/4
Cookie: pds_session=DDdY…생략

HTTP/1.1 200
{"completions": [], "logs": [{"actual_minutes": 20, "blocker": "A의 막힌 이유", "created_at": "2026-10-07T12:08:33.020592+09:00", "done_date": "2026-10-07", "ended_at": "2026-10-07T09:20:00+09:00", "id": 13, "note": null, "plan_id": 4, "started_at": "2026-10-07T09:00:00+09:00", "status": "partial", "task_id": 22, "user_id": 1}], "plan": {"change_note": null, "created_at": "2026-10-07T12:08:32.997124+09
```

## 그 밖의 거절

> 다섯 가지 밖의 확인 — 위조 쿠키, 다른 사이트가 몰래 보내는 쓰기, 틀린 비밀번호와 없는 이메일

#### 63. 지어낸 쿠키 값으로 요청 — ✅

```http
GET /api/plans
Cookie: pds_session=AAAA…생략

HTTP/1.1 401
{"error": "로그인이 필요합니다"}
```

#### 64. B의 쿠키는 있지만 X-Requested-With 헤더 없이 쓰기 — ✅

```http
POST /api/plans/5/tasks
Cookie: pds_session=t_WE…생략

{"title": "몰래", "planned_minutes": 5}

HTTP/1.1 403
{"error": "허용되지 않은 요청입니다 (X-Requested-With 헤더 없음)"}
```

#### 65. B 이메일 + 틀린 비밀번호 — ✅

```http
POST /api/auth/login
Cookie: (없음)

{"email": "authcheck-b-c7b5ae@example.com", "password": "(가림)"}

HTTP/1.1 401
{"error": "이메일 또는 비밀번호가 맞지 않습니다"}
```

#### 66. 가입한 적 없는 이메일 — ✅

```http
POST /api/auth/login
Cookie: (없음)

{"email": "nobody-ba7499@example.com", "password": "(가림)"}

HTTP/1.1 401
{"error": "이메일 또는 비밀번호가 맞지 않습니다"}
```

#### 67. 틀린 비밀번호와 없는 이메일의 안내 문장이 같음 — ✅

```
틀린 비밀번호: {'error': '이메일 또는 비밀번호가 맞지 않습니다'}
없는 이메일:   {'error': '이메일 또는 비밀번호가 맞지 않습니다'}
```

## 계정 삭제 — 내 자료가 함께 지워짐

> users 한 줄을 지우면 DB 외래키(ON DELETE CASCADE)가 계획·할 일·기록·돌아보기·이력·세션을 같이 지운다

#### 68. A 계정 삭제 (지금 비밀번호 + 확인 문구) — ✅

```http
DELETE /api/auth/account
Cookie: pds_session=DDdY…생략

{"password": "(가림)", "confirm": "계정 삭제"}

HTTP/1.1 200
Set-Cookie: pds_session=""; Expires=Wed, 07 Oct 2026 03:08:34 GMT; Max-Age=0; HttpOnly; Path=/; SameSite=Lax
{"deleted": true, "deleted_counts": {"logs": 1, "plan_revisions": 0, "plans": 1, "reviews": 1, "task_completions": 0, "tasks": 1}, "email": "authcheck-a-11383c@example.com"}
```

#### 69. 삭제 뒤: A의 쿠키 값으로 — ✅

```http
GET /api/plans
Cookie: pds_session=DDdY…생략

HTTP/1.1 401
{"error": "로그인이 필요합니다"}
```

#### 70. 삭제 뒤: A로 로그인 — ✅

```http
POST /api/auth/login
Cookie: (없음)

{"email": "authcheck-a-11383c@example.com", "password": "(가림)"}

HTTP/1.1 401
{"error": "이메일 또는 비밀번호가 맞지 않습니다"}
```

#### 71. A를 지워도 B의 자료 건수는 그대로 — ✅

```
전: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
후: 실행 기록 1, 수정 이력 0, 계획 1, 돌아보기 1, 완료 기록 0, 할 일 1
```

#### 72. 정리: B 계정 삭제 — ✅

```http
DELETE /api/auth/account
Cookie: pds_session=t_WE…생략

{"password": "(가림)", "confirm": "계정 삭제"}

HTTP/1.1 200
Set-Cookie: pds_session=""; Expires=Wed, 07 Oct 2026 03:08:35 GMT; Max-Age=0; HttpOnly; Path=/; SameSite=Lax
{"deleted": true, "deleted_counts": {"logs": 1, "plan_revisions": 0, "plans": 1, "reviews": 1, "task_completions": 0, "tasks": 1}, "email": "authcheck-b-c7b5ae@example.com"}
```

