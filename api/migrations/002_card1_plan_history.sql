-- =========================================================
-- 플랜두씨 다이어리 — 카드 1: 계획 세우기
-- 이미 schema.sql을 실행한 DB에 "추가로" 실행하는 파일입니다.
-- Supabase > SQL Editor > New query 에 통째로 붙여넣고 Run (한 번만)
-- =========================================================

-- 1) 계획에 우선순위 · 성공 기준 · 예상 시간 · 버전 칸 추가
alter table plans
  add column priority          text not null default 'medium'
                               check (priority in ('high', 'medium', 'low')),
  add column success_criteria  text check (char_length(success_criteria) <= 500),
  add column estimated_minutes int  check (estimated_minutes between 1 and 100000),
  add column version           int  not null default 1,
  add column change_note       text check (char_length(change_note) <= 200),  -- 이 버전으로 고친 이유
  add column updated_at        timestamptz not null default now();

-- 2) 수정 이력 표: 고치기 "직전" 내용을 한 줄씩 쌓는다
--    plans = 지금 값,  plan_revisions = 예전 값들  (계획 ID는 그대로)
create table plan_revisions (
  id                bigint generated always as identity primary key,
  plan_id           bigint not null references plans(id) on delete cascade,
  version           int  not null,          -- 1 = 처음 세운 계획
  title             text not null,
  start_date        date not null,
  end_date          date not null,
  goal              text,
  priority          text not null,
  success_criteria  text,
  estimated_minutes int,
  change_note       text,                   -- 그 버전이 만들어진 이유
  valid_from        timestamptz not null,   -- 그 버전이 생긴 시각
  replaced_at       timestamptz not null default now(),  -- 다음 버전으로 바뀐 시각
  unique (plan_id, version)
);
create index on plan_revisions (plan_id);
alter table plan_revisions enable row level security;

-- 3) 트리거: plans가 UPDATE될 때마다 DB가 스스로 옛 값을 이력에 복사
--    → API를 거치든 SQL Editor에서 직접 고치든 이력이 빠지지 않는다
create or replace function plans_keep_history() returns trigger
language plpgsql
set search_path = public
as $$
begin
  -- 계획 내용이 실제로 바뀌지 않았으면(예: 연결 칸만 바뀜) 이력을 쌓지 않음
  if (new.title, new.start_date, new.end_date, new.goal,
      new.priority, new.success_criteria, new.estimated_minutes)
     is not distinct from
     (old.title, old.start_date, old.end_date, old.goal,
      old.priority, old.success_criteria, old.estimated_minutes) then
    new.version     := old.version;
    new.change_note := old.change_note;
    new.updated_at  := old.updated_at;
    return new;
  end if;

  insert into plan_revisions
    (plan_id, version, title, start_date, end_date, goal, priority,
     success_criteria, estimated_minutes, change_note, valid_from)
  values
    (old.id, old.version, old.title, old.start_date, old.end_date, old.goal, old.priority,
     old.success_criteria, old.estimated_minutes, old.change_note, old.updated_at);

  new.version    := old.version + 1;   -- 버전은 DB가 매긴다 (클라이언트가 못 바꿈)
  new.updated_at := now();
  new.created_at := old.created_at;
  return new;
end;
$$;

create trigger plans_keep_history
  before update on plans
  for each row execute function plans_keep_history();

-- 4) 이력은 고칠 수 없게 잠금 (지우기는 계획 자체를 지울 때만 연쇄로)
create or replace function plan_revisions_readonly() returns trigger
language plpgsql
set search_path = public
as $$
begin
  raise exception '수정 이력은 고칠 수 없습니다';
end;
$$;

create trigger plan_revisions_readonly
  before update on plan_revisions
  for each row execute function plan_revisions_readonly();
