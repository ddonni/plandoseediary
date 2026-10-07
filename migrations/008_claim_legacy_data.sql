-- =========================================================
-- 플랜두씨 다이어리 2 — 기존 자료(과제 6에서 넣은 것)를 내 계정으로 옮기기
-- 화면에서 내 계정으로 가입한 "뒤에" 한 번 실행합니다.
-- ▶ 아래 두 군데의 'me@example.com'을 내가 가입한 이메일로 바꾼 뒤 Run
-- =========================================================

do $$
begin
  if not exists (select 1 from users where email = lower('me@example.com')) then
    raise exception '그 이메일로 가입한 계정이 없습니다. 이메일을 확인하세요';
  end if;
end $$;

create temporary table claim_owner as
  select id from users where email = lower('me@example.com');

update plans            set user_id = (select id from claim_owner) where user_id is null;
update tasks            set user_id = (select id from claim_owner) where user_id is null;
update logs             set user_id = (select id from claim_owner) where user_id is null;
update reviews          set user_id = (select id from claim_owner) where user_id is null;
update task_completions set user_id = (select id from claim_owner) where user_id is null;
-- 이력은 고칠 수 없게 잠겨 있어서, 주인 칸을 채우는 이 한 번만 잠금을 잠시 푼다
alter table plan_revisions disable trigger plan_revisions_readonly;
update plan_revisions   set user_id = (select id from claim_owner) where user_id is null;
alter table plan_revisions enable trigger plan_revisions_readonly;
-- 예전 요청 키(주인 없음)는 지운다
delete from request_keys where user_id is null;

-- 이제부터는 주인 없는 자료가 생길 수 없게
alter table plans            alter column user_id set not null;
alter table tasks            alter column user_id set not null;
alter table logs             alter column user_id set not null;
alter table reviews          alter column user_id set not null;
alter table plan_revisions   alter column user_id set not null;
alter table task_completions alter column user_id set not null;

-- 결과 확인: 계정별 계획 수
select u.email, count(p.id) as plans
from users u left join plans p on p.user_id = u.id
group by u.email;
