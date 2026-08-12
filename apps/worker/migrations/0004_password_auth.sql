ALTER TABLE users ADD COLUMN password_hash TEXT;
ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0;

ALTER TABLE otp_codes ADD COLUMN purpose TEXT NOT NULL DEFAULT 'login';

CREATE INDEX IF NOT EXISTS idx_otp_codes_email_purpose_created_at
  ON otp_codes(email, purpose, created_at);
