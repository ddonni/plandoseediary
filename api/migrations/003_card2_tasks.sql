-- =========================================================
-- 플랜두씨 다이어리 — 카드 2: 할 일 다루기
-- 002를 실행한 DB에 "추가로" 한 번 실행합니다.
-- =========================================================

-- 1) planned_date → due_date (마감일이라는 뜻을 이름에 드러냄)
alter table tasks rename column planned_date to due_date;

-- 2) 할 일 항목 추가
alter table tasks
  add column priority   text not null default 'medium'
                        check (priority in ('high', 'medium', 'low')),
  add column tags       text[] not null default '{}'
                        -- 10개 이하, 하나당 20자 이하 (쉼표 포함 합계로 상한 확인)
                        check (cardinality(tags) <= 10
                               and char_length(array_to_string(tags, ',')) <= 209),
  add column status     text not null default 'open'
                        check (status in ('open', 'done')),   -- 진행 중 / 완료
  add column done_at    timestamptz,                         -- 완료로 바꾼 시각
  add column updated_at timestamptz not null default now(),
  -- 완료 상태와 완료 시각은 항상 짝이 맞아야 함
  add constraint tasks_done_at_matches check ((status = 'done') = (done_at is not null));

-- 태그로 거를 때 빠르게 (배열 안 값 검색용 GIN 인덱스)
create index on tasks using gin (tags);

-- 3) 트리거: 완료 시각과 수정 시각을 DB가 최종 확정한다
--    (API가 보낸 값이 어긋나도 status와 done_at의 짝이 항상 맞게)
create or replace function tasks_touch() returns trigger
language plpgsql
set search_path = public
as $$
begin
  if tg_op = 'UPDATE' then
    new.updated_at := now();
    new.created_at := old.created_at;
    if new.status = 'done' and old.status = 'done' then
      new.done_at := old.done_at;           -- 완료 상태 유지: 처음 완료 시각 보존
    end if;
  end if;
  if new.status = 'done' then
    new.done_at := coalesce(new.done_at, now());
  else
    new.done_at := null;                    -- 진행 중으로 되돌리면 완료 시각 지움
  end if;
  return new;
end;
$$;

create trigger tasks_touch
  before insert or update on tasks
  for each row execute function tasks_touch();
