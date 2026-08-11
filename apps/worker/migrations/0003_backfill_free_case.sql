UPDATE users
SET free_case_used = 1
WHERE free_case_used = 0
  AND EXISTS (SELECT 1 FROM cases WHERE cases.user_id = users.id);
