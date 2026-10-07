-- T07-C103·C104: 저장된 비밀번호 값 확인 (Supabase SQL Editor에서 실행)
-- 원문은 어디에도 없고, 같은 비밀번호여도 소금값(salt)이 달라 저장값이 다르다는 것을 본다.
-- 값 전체를 붙이지 않도록 앞부분만 잘라 보여 준다.
select email,
       split_part(password_hash, '$', 1)                 as method,      -- scrypt:32768:8:1
       left(split_part(password_hash, '$', 2), 4) || '…' as salt_head,   -- 계정마다 다름
       left(split_part(password_hash, '$', 3), 8) || '…' as hash_head,   -- 계정마다 다름
       length(password_hash)                              as len
from users
where email like 'authcheck-%'          -- verify_auth.py가 만든 시험 계정 (둘의 비밀번호는 같음)
order by email;

select count(*) as accounts, count(distinct password_hash) as distinct_stored_values
from users where email like 'authcheck-%';
