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
| POST | `/api/plans/{id}/tasks` | 할 일 추가 (예상 시간) |
| DELETE | `/api/tasks/{id}` | 할 일 삭제 (딸린 기록도 삭제) |
| POST | `/api/plans/{id}/logs` | 실행 기록 추가 (`task_id` 없으면 계획에 없던 일, 메모 필수) |
| DELETE | `/api/logs/{id}` | 실행 기록 삭제 |
| PUT | `/api/plans/{id}/review` | 돌아보기 쓰기/덮어쓰기 |
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

## 계획 수정 이력이 남는 방식

- `plans` 표 = **지금 값**, `plan_revisions` 표 = **예전 값들**. 계획 ID는 그대로이고 `version`만 올라갑니다.
- `plans`가 UPDATE될 때마다 DB 트리거 `plans_keep_history`가 고치기 직전 줄을 `plan_revisions`에 복사합니다. API를 거치지 않고 SQL Editor에서 고쳐도 이력이 남습니다.
- `plan_revisions`는 UPDATE가 막혀 있습니다(트리거 `plan_revisions_readonly`).

## 테스트

```bash
pip install flask requests pytest
python -m pytest -q
```
