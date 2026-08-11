PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY NOT NULL,
  email TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS otp_codes (
  id TEXT PRIMARY KEY NOT NULL,
  email TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_otp_codes_email_created_at ON otp_codes(email, created_at);

CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  case_stage TEXT NOT NULL CHECK (case_stage IN ('arbitration', 'litigation')),
  party_side TEXT NOT NULL CHECK (party_side IN ('worker', 'employer')),
  status TEXT NOT NULL DEFAULT 'draft',
  access_status TEXT NOT NULL DEFAULT 'locked',
  data_json TEXT NOT NULL DEFAULT '{}',
  generation_count INTEGER NOT NULL DEFAULT 0,
  generation_version TEXT NOT NULL DEFAULT 'rules-1.0/templates-1.0',
  redeemed_at TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_user_created_at ON cases(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cases_expires_at ON cases(expires_at);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY NOT NULL,
  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  name TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  purpose TEXT NOT NULL DEFAULT '',
  object_key TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_case_created_at ON evidence(case_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY NOT NULL,
  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  kind TEXT NOT NULL,
  object_key TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_case_created_at ON artifacts(case_id, created_at);

CREATE TABLE IF NOT EXISTS generation_jobs (
  id TEXT PRIMARY KEY NOT NULL,
  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'queued',
  error TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_generation_jobs_case_status ON generation_jobs(case_id, status);

CREATE TABLE IF NOT EXISTS redemption_codes (
  id TEXT PRIMARY KEY NOT NULL,
  code_hash TEXT NOT NULL UNIQUE,
  note TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  redeemed_case_id TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  case_id TEXT REFERENCES cases(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_created_at ON notifications(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  case_id TEXT REFERENCES cases(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_case_created_at ON audit_events(case_id, created_at);
