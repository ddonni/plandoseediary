-- =========================================================
-- 플랜두씨 다이어리 — 1단계: 데이터베이스 스키마
-- Supabase 대시보드 > SQL Editor > New query 에 통째로 붙여넣고 Run
-- =========================================================

-- 1) 계획(Plan): 기간 단위 계획 (예: "10월 첫째 주")
create table plans (
  id             bigint generated always as identity primary key,
  title          text not null check (char_length(title) between 1 and 100),
  start_date     date not null,
  end_date       date not null,
  goal           text check (char_length(goal) <= 500),
  from_review_id bigint,          -- 어떤 '돌아보기' 결론에서 나온 계획인지 (See → 다음 Plan 연결고리)
  created_at     timestamptz not null default now(),
  check (end_date >= start_date)
);

-- 2) 할 일: 계획 안의 개별 항목 + 예상 시간
create table tasks (
  id              bigint generated always as identity primary key,
  plan_id         bigint not null references plans(id) on delete cascade,
  title           text not null check (char_length(title) between 1 and 100),
  planned_date    date,
  planned_minutes int  not null check (planned_minutes between 1 and 1440),
  created_at      timestamptz not null default now(),
  unique (id, plan_id)            -- 아래 logs의 복합 외래키용
);

-- 3) 실제로 한 일(Do): 실행 기록
--    task_id가 비어 있으면 "계획에 없던 일"
create table logs (
  id             bigint generated always as identity primary key,
  plan_id        bigint not null references plans(id) on delete cascade,
  task_id        bigint,
  done_date      date not null,
  actual_minutes int  not null check (actual_minutes between 0 and 1440),
  status         text not null check (status in ('done', 'partial', 'skipped')),
  note           text check (char_length(note) <= 500),
  created_at     timestamptz not null default now(),
  -- 기록이 가리키는 할 일은 반드시 '같은 계획'의 할 일이어야 함
  foreign key (task_id, plan_id) references tasks(id, plan_id) on delete cascade
);

-- 4) 돌아보기(See): 계획 하나당 하나
create table reviews (
  id           bigint generated always as identity primary key,
  plan_id      bigint not null unique references plans(id) on delete cascade,
  went_well    text check (char_length(went_well)  <= 1000),
  went_wrong   text check (char_length(went_wrong) <= 1000),
  -- 이번 계획이 주로 어느 쪽으로 틀렸는가
  miss_pattern text not null check (miss_pattern in
                 ('underestimate',  -- 시간을 적게 잡았다
                  'overestimate',   -- 시간을 많이 잡았다
                  'unplanned',      -- 계획에 없던 일에 밀렸다
                  'skipped',        -- 아예 못 했다
                  'on_track')),     -- 거의 맞았다
  lesson       text not null check (char_length(lesson) between 1 and 500),
  created_at   timestamptz not null default now()
);

-- 돌아보기 → 다음 계획 연결 (reviews가 만들어진 뒤에 걸어야 해서 따로)
alter table plans
  add constraint plans_from_review_fk
  foreign key (from_review_id) references reviews(id) on delete set null;

-- 조회 속도용 인덱스 (외래키는 Postgres가 자동으로 인덱스를 안 만들어 줌)
create index on tasks (plan_id);
create index on logs  (plan_id);
create index on logs  (task_id);

-- 집계용 뷰: 할 일별 예상 vs 실제
create view task_progress with (security_invoker = true) as
select
  t.id                                  as task_id,
  t.plan_id,
  t.title,
  t.planned_minutes,
  coalesce(sum(l.actual_minutes), 0)    as actual_minutes,
  coalesce(sum(l.actual_minutes), 0) - t.planned_minutes as diff_minutes,
  count(l.id)                           as log_count,
  coalesce(bool_or(l.status = 'done'), false) as is_done
from tasks t
left join logs l on l.task_id = t.id
group by t.id;

-- 보안: RLS를 켜고 정책은 하나도 만들지 않음
--  → 브라우저용 anon 키로는 아무것도 읽고 쓸 수 없음
--  → 서버(Vercel 함수)의 service_role 키만 접근 가능
alter table plans   enable row level security;
alter table tasks   enable row level security;
alter table logs    enable row level security;
alter table reviews enable row level security;
