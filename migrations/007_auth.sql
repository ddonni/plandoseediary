-- =========================================================
-- 플랜두씨 다이어리 2 — 인증: 계정·세션·자료 주인(user_id)·계획 규칙
-- 006을 실행한 DB에 "추가로" 한 번 실행합니다.
-- 실행 순서: 007 → (코드 배포) → 화면에서 내 계정 가입 → 008(기존 자료를 내 계정으로)
-- =========================================================

-- 1) 계정: 비밀번호 원문은 저장하지 않는다. 서버가 scrypt로 만든 해시만 저장.
create table users (
  id            bigint generated always as identity primary key,
  email         text not null
                check (email = lower(email) and char_length(email) between 3 and 254 and email like '%_@_%'),
  password_hash text not null
                check (password_hash like 'scrypt:%' or password_hash like 'pbkdf2:%'),  -- 해시 형식만 허용
  created_at    timestamptz not null default now()
);
create unique index users_email_key on users (email);

-- 2) 로그인 세션: 브라우저 쿠키에는 무작위 토큰, DB에는 그 토큰의 SHA-256만 저장.
--    로그아웃 = 이 줄을 지움 → 같은 쿠키로 다시 와도 찾을 줄이 없어 거절된다.
create table sessions (
  id          bigint generated always as identity primary key,
  user_id     bigint not null references users(id) on delete cascade,
  token_hash  text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,
  user_agent  text check (char_length(user_agent) <= 300)
);
create index on sessions (user_id);

-- 3) 로그인 시도 기록: 같은 이메일로 15분에 5번 넘게 틀리면 잠시 막는 데 쓴다.
create table login_attempts (
  id           bigint generated always as identity primary key,
  email        text not null check (char_length(email) <= 254),
  ok           boolean not null,
  attempted_at timestamptz not null default now()
);
create index on login_attempts (email, attempted_at);

alter table users          enable row level security;
alter table sessions       enable row level security;
alter table login_attempts enable row level security;

-- 4) 자료의 주인. 기존 자료는 아직 주인이 없으므로(빈 값) 008에서 내 계정으로 옮긴다.
alter table plans add column user_id bigint references users(id) on delete cascade;
alter table plans add constraint plans_id_user_key unique (id, user_id);
create index on plans (user_id);

-- 딸린 표에도 user_id를 두고, (plan_id, user_id)가 그 계획의 주인과 반드시 같도록 복합 외래키를 건다.
-- → 서버 코드가 실수로 남의 계획에 붙이려 해도 DB가 거부한다.
alter table tasks            add column user_id bigint;
alter table logs             add column user_id bigint;
alter table reviews          add column user_id bigint;
alter table plan_revisions   add column user_id bigint;
alter table task_completions add column user_id bigint;
alter table tasks            add constraint tasks_owner_fk            foreign key (plan_id, user_id) references plans(id, user_id);
alter table logs             add constraint logs_owner_fk             foreign key (plan_id, user_id) references plans(id, user_id);
alter table reviews          add constraint reviews_owner_fk          foreign key (plan_id, user_id) references plans(id, user_id);
alter table plan_revisions   add constraint plan_revisions_owner_fk   foreign key (plan_id, user_id) references plans(id, user_id);
alter table task_completions add constraint task_completions_owner_fk foreign key (plan_id, user_id) references plans(id, user_id);
create index on tasks (user_id);
create index on logs (user_id);
create index on reviews (user_id);
create index on plan_revisions (user_id);
create index on task_completions (user_id);

-- 새 줄의 user_id는 클라이언트가 보낸 값이 아니라 그 계획의 주인으로 DB가 채운다 (속여 넣기 불가).
create or replace function inherit_plan_owner() returns trigger
language plpgsql
set search_path = public
as $$
begin
  select user_id into new.user_id from plans where id = new.plan_id;
  return new;
end;
$$;
create trigger tasks_owner            before insert on tasks            for each row execute function inherit_plan_owner();
create trigger logs_owner             before insert on logs             for each row execute function inherit_plan_owner();
create trigger reviews_owner          before insert on reviews          for each row execute function inherit_plan_owner();
create trigger plan_revisions_owner   before insert on plan_revisions   for each row execute function inherit_plan_owner();
create trigger task_completions_owner before insert on task_completions for each row execute function inherit_plan_owner();

-- 같은 요청 키(중복 방지)도 사람별로: 남의 요청 키로 남의 응답을 받아 가지 못하게.
alter table request_keys add column user_id bigint references users(id) on delete cascade;

-- 5) 계획 규칙 (5일 사용 중 3일차 전에 하나 바꿔 보기)과, 5일 동안 답하려는 질문(1일차에 고정).
--    둘 다 바꾸면 기존 이력 트리거가 옛 값과 바뀐 시각을 plan_revisions에 남긴다.
alter table plans          add column rule     text check (char_length(rule) <= 200);
alter table plans          add column question text check (char_length(question) <= 200);
alter table plan_revisions add column rule     text;
alter table plan_revisions add column question text;

create or replace function plans_keep_history() returns trigger
language plpgsql
set search_path = public
as $$
begin
  -- 한번 정해진 계획 주인은 바뀌지 않는다
  if old.user_id is not null and new.user_id is distinct from old.user_id then
    raise exception '계획의 주인은 바꿀 수 없습니다';
  end if;

  if (new.title, new.start_date, new.end_date, new.goal,
      new.priority, new.success_criteria, new.estimated_minutes, new.rule, new.question)
     is not distinct from
     (old.title, old.start_date, old.end_date, old.goal,
      old.priority, old.success_criteria, old.estimated_minutes, old.rule, old.question) then
    new.version     := old.version;
    new.change_note := old.change_note;
    new.updated_at  := old.updated_at;
    return new;
  end if;

  insert into plan_revisions
    (plan_id, version, title, start_date, end_date, goal, priority,
     success_criteria, estimated_minutes, rule, question, change_note, valid_from)
  values
    (old.id, old.version, old.title, old.start_date, old.end_date, old.goal, old.priority,
     old.success_criteria, old.estimated_minutes, old.rule, old.question, old.change_note, old.updated_at);

  new.version    := old.version + 1;
  new.updated_at := now();
  new.created_at := old.created_at;
  return new;
end;
$$;
