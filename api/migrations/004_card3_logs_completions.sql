-- =========================================================
-- 플랜두씨 다이어리 — 카드 3: 실제로 한 일 적기
-- 003을 실행한 DB에 "추가로" 한 번 실행합니다.
-- =========================================================

-- 1) 실행 기록(logs)에 시작·끝 시각, 막혔던 이유 추가
--    실행 기록은 계획(plans)·할 일(tasks)과 다른 표라서, 저장해도 계획 값을 덮어쓰지 않는다.
alter table logs
  add column started_at timestamptz,
  add column ended_at   timestamptz,
  add column blocker    text check (char_length(blocker) <= 500),   -- 막혔던 이유
  add constraint logs_time_order check (ended_at >= started_at),
  add constraint logs_span_24h   check (ended_at - started_at <= interval '24 hours'),
  -- 실제로 걸린 시간은 시작~끝 사이 시간을 넘을 수 없다 (쉬는 시간이 있으면 더 짧을 수는 있음)
  add constraint logs_actual_within_span
    check (actual_minutes <= ceil(extract(epoch from (ended_at - started_at)) / 60));

-- 2) 완료 기록 표: 할 일이 '완료'로 바뀐 사건 하나 = 한 줄
create table task_completions (
  id           bigint generated always as identity primary key,
  task_id      bigint not null references tasks(id) on delete cascade,
  plan_id      bigint not null references plans(id) on delete cascade,
  completed_at timestamptz not null default now(),
  revoked_at   timestamptz,            -- 진행 중으로 되돌리면 지우지 않고 '취소됨'으로 표시
  check (revoked_at is null or revoked_at >= completed_at)
);
create index on task_completions (plan_id);

-- ★ 중복 방지의 마지막 방어선:
--   할 일 하나에 '살아 있는(취소되지 않은)' 완료 기록은 딱 하나만 허용
create unique index task_completions_one_active
  on task_completions (task_id) where revoked_at is null;

alter table task_completions enable row level security;

-- 3) 트리거: 할 일 상태가 '진행 중 → 완료'로 "바뀔 때만" 완료 기록을 만든다
--    이미 완료인 할 일에 완료를 또 보내면 상태가 바뀌지 않으므로 기록도 안 생긴다
create or replace function tasks_completion_events() returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.status = 'done' and (tg_op = 'INSERT' or old.status <> 'done') then
    insert into task_completions (task_id, plan_id, completed_at)
    values (new.id, new.plan_id, coalesce(new.done_at, now()))
    on conflict (task_id) where revoked_at is null do nothing;
  elsif tg_op = 'UPDATE' and new.status = 'open' and old.status = 'done' then
    update task_completions set revoked_at = now()
     where task_id = new.id and revoked_at is null;
  end if;
  return null;
end;
$$;

create trigger tasks_completion_events
  after insert or update of status on tasks
  for each row execute function tasks_completion_events();

-- 이미 완료 상태인 할 일은 완료 기록을 한 번 채워 둔다
insert into task_completions (task_id, plan_id, completed_at)
select id, plan_id, coalesce(done_at, now()) from tasks where status = 'done'
on conflict (task_id) where revoked_at is null do nothing;

-- 4) 같은 요청 알아보기: 화면이 보낸 요청 키(Idempotency-Key)를 기억해 둔다
--    같은 키가 다시 오면 서버는 처음 응답을 그대로 돌려주고, 일을 다시 하지 않는다
create table request_keys (
  key        uuid primary key,
  method     text not null,
  path       text not null,
  status     int,          -- 처리 끝나기 전에는 비어 있음
  response   jsonb,
  created_at timestamptz not null default now()
);
alter table request_keys enable row level security;
