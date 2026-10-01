# 플랜두씨 다이어리

계획(Plan) → 실제로 한 일(Do) → 돌아보기(See)를 하나로 잇는 다이어리.
아직 로그인이 없습니다. 링크를 아는 사람은 누구나 볼 수 있습니다.

## 구조

```
브라우저 (index.html)  ──fetch('/api/...')──▶  Vercel Python 함수 (api/index.py)  ──REST + 비밀키──▶  Supabase Postgres
```

- 비밀키는 Vercel 환경변수에만 있고, 브라우저로 내려가지 않습니다.
- Supabase 테이블은 RLS가 켜져 있고 정책이 없어서, 공개 키로는 아무것도 읽고 쓸 수 없습니다.

## 폴더

| 경로 | 설명 |
|---|---|
| `schema.sql` | DB 스키마 (Supabase SQL Editor에서 처음 한 번 실행) |
| `migrations/002_card1_plan_history.sql` | 카드 1: 우선순위·성공 기준·예상 시간 칸 + 수정 이력 표·트리거 |
| `migrations/003_card2_tasks.sql` | 카드 2: 할 일에 마감일·우선순위·태그·완료 상태 |
| `migrations/004_card3_logs_completions.sql` | 카드 3: 실행 기록 시각·막힌 이유, 완료 기록 표(할 일당 1건), 요청 키 표 |
| `migrations/005_task_trash.sql` | 할 일 휴지통: `deleted_at` (30일 동안 되돌리기) |
| `api/index.py` | 서버 API (Flask) |
| `tests/test_api.py` | 가짜 DB로 돌리는 API 테스트 |
| `index.html` | 화면 |
| `vercel.json` | `/api/*` 요청을 `api/index.py` 하나로 모음 |
| `requirements.txt` | Vercel이 설치할 Python 패키지 |

## API

| 메서드 | 주소 | 하는 일 |
|---|---|---|
| GET | `/api/health` | DB까지 실제로 다녀와서 연결 확인 |
| GET | `/api/meta` | 판정 이름표, ±20% 기준값 |
| GET / POST | `/api/plans` | 계획 목록(집계 포함) / 계획 만들기 (`from_review_id`로 지난 돌아보기 연결) |
| GET / DELETE | `/api/plans/{id}` | 계획 상세(할 일·기록·돌아보기·집계·출처 교훈) / 삭제 |
| PATCH | `/api/plans/{id}` | 계획 고치기 (바뀐 칸 + `change_note` 필수). 고치기 전 내용은 DB 트리거가 이력에 보관 |
| GET | `/api/plans/{id}/revisions` | 버전 1(처음 계획)부터 지금까지 |
| GET | `/api/plans/{id}/tasks?q=&status=&priority=&tag=&due=&sort=` | 할 일 검색·거르기·정렬 (서버에서 처리) |
| POST | `/api/plans/{id}/tasks` | 할 일 추가 (마감일·우선순위·태그·예상 시간) |
| PATCH | `/api/tasks/{id}` | 할 일 고치기, 완료(`status: done`), 되돌리기(`status: open`) |
| DELETE | `/api/tasks/{id}` | 할 일을 휴지통으로 (`?permanent=1`: 휴지통에 있는 할 일 영구 삭제, 딸린 기록까지) |
| POST | `/api/tasks/{id}/restore` | 휴지통에서 되돌리기 (실행 기록·완료까지 그대로) |
| GET | `/api/plans/{id}/trash` | 휴지통 목록 + 영구 삭제까지 남은 날 (30일 지난 것은 이때 정리) |
| POST | `/api/tasks/{id}/logs` | 할 일에 붙는 실행 기록 (시작·끝 시각, 실제 시간, 막힌 이유, `complete`) |
| POST | `/api/plans/{id}/logs` | 계획에 없던 일 기록 (메모 필수) |
| DELETE | `/api/logs/{id}` | 실행 기록 삭제 |
| GET | `/api/plans/{id}/review?kind=` | 돌아보기 집계 + (kind를 주면) 그 숫자가 나온 할 일·실행 기록 |
| GET | `/api/reviews` | 기간별 돌아보기 (계획 하나 = 기간 하나) |
| GET | `/api/reviews/{id}` | 돌아보기 한 건 (다음 계획 폼에 고칠 점 불러오기) |
| PUT | `/api/plans/{id}/review` | 돌아보기 쓰기/덮어쓰기 — `lesson`은 다음 계획으로 넘길 고칠 점 한 줄 |
| GET | `/api/stats` | 전체 집계 + 돌아보기 패턴 개수 |
| GET | `/api/records?kind=...&plan_id=...` | 집계 숫자 하나의 근거 기록 |

### 판정 기준 (할 일마다)

| verdict | 뜻 |
|---|---|
| `over` | 실제 시간 > 예상 × 1.2 (끝났든 아니든) |
| `under` | 완료했고 실제 시간 < 예상 × 0.8 |
| `on_track` | 완료했고 ±20% 안 |
| `in_progress` | 기록은 있는데 아직 완료 기록 없음 |
| `skipped` | 건너뜀 기록만 있음 |
| `pending` | 기록 없음 |

## 할 일 정렬 규칙

검색·거르기·정렬은 모두 서버(`/api/plans/{id}/tasks`)에서 하고, 화면은 받은 순서대로 그립니다.
모든 규칙의 마지막 기준은 **먼저 만든 순(id)** 이라 값이 같은 할 일이 있어도 순서가 항상 같습니다.

| sort | 순서 |
|---|---|
| `due` (기본) | 마감일 빠른 순(없으면 맨 뒤) → 우선순위 높은 순 → 먼저 만든 순 |
| `priority` | 우선순위 높은 순 → 마감일 빠른 순 → 먼저 만든 순 |
| `minutes` | 예상 시간 긴 순 → 우선순위 높은 순 → 먼저 만든 순 |
| `title` | 이름 가나다순(대소문자 무시) → 먼저 만든 순 |
| `recent` | 최근에 만든 순 |

'오늘'·'지난 마감'은 한국 날짜(UTC+9) 기준입니다.

## 완료가 두 번 쌓이지 않는 방식 (세 겹)

1. **같은 요청 키** — 화면은 한 번의 의도(완료 누르기, 기록 저장, 할 일 추가)마다 `Idempotency-Key`(UUID)를 붙인다.
   서버는 키를 먼저 `request_keys` 표에 넣어 '찜'하고, 같은 키가 또 오면 일을 다시 하지 않고 처음 응답을 돌려준다(`Idempotent-Replay: true`).
   동시에 온 같은 키 요청은 하나만 실행되고 나머지는 409(처리 중)를 받는다.
2. **DB 제약** — 완료 기록(`task_completions`)은 DB 트리거가 할 일 상태가 진행 중→완료로 *바뀔 때만* 만든다.
   여기에 `task_completions_one_active` 유일 인덱스가 할 일 하나당 '살아 있는' 완료 기록을 1건으로 묶는다.
   그래서 키가 다른 요청 8개가 동시에 와도 완료 기록은 1건이다.
3. **화면 잠금** — 버튼 비활성화와 0.8초 연타 무시는 편의일 뿐, 위 두 겹이 없어도 되는 장치가 아니다.

되돌리기를 하면 완료 기록을 지우지 않고 `revoked_at`을 찍어 집계에서 뺀다.

## 돌아보기 숫자의 정의 (카드 4)

모든 숫자와 '그 숫자를 누르면 나오는 기록'은 서버의 같은 판정(`review_rows`)에서 나온다.

| 숫자 | 정의 |
|---|---|
| 계획 | 그 계획의 할 일 수 (지운 할 일은 표에서 사라지므로 자동 제외) |
| 완료 | 지금 완료 상태인 할 일 수 |
| 지연 | 완료 안 됐고 마감일이 **서울 기준 오늘보다 앞선** 할 일 수 (완료한 할 일·오늘 마감은 아님) |
| 막힘 | 막힌 이유가 하나라도 적힌 실행 기록이 있는 **할 일** 수 |
| 예상 / 실제 / 차이 | 할 일 예상 시간 합 / 그 할 일들의 실행 기록 합 / 실제 − 예상. 모두 분 단위, 없으면 0 |

'계획에 없던 일' 기록은 할 일에 붙지 않으므로 실제 시간에 넣지 않는다.
돌아보기의 고칠 점(`lesson`)으로 새 계획을 만들면 `plans.from_review_id`로 이어지고,
새 계획 화면에 '지난 돌아보기에서 넘어온 고칠 점'이, 지난 돌아보기에 '이어받은 계획'이 보인다.

## 할 일 휴지통

- 지우기는 `deleted_at`만 찍는다. 휴지통의 할 일은 목록·검색·돌아보기 집계·실행 기록 목록에서 빠지고, 고치거나 기록할 수 없다.
- 되돌리면 실행 기록과 완료 기록까지 원래대로 돌아온다(지운 동안에도 지우지 않았으므로).
- 30일이 지나면 휴지통을 읽거나 할 일을 지울 때 함께 실제로 지운다(`purge_expired`). '영구 삭제'는 휴지통을 거친 할 일만 가능하다.

## 계획 수정 이력이 남는 방식

- `plans` 표 = **지금 값**, `plan_revisions` 표 = **예전 값들**. 계획 ID는 그대로이고 `version`만 올라갑니다.
- `plans`가 UPDATE될 때마다 DB 트리거 `plans_keep_history`가 고치기 직전 줄을 `plan_revisions`에 복사합니다. API를 거치지 않고 SQL Editor에서 고쳐도 이력이 남습니다.
- `plan_revisions`는 UPDATE가 막혀 있습니다(트리거 `plan_revisions_readonly`).

## 테스트

```bash
pip install flask requests pytest
python -m pytest -q
```
