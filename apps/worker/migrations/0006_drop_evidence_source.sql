CREATE TABLE IF NOT EXISTS evidence_v2 (
  id TEXT PRIMARY KEY NOT NULL,
  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  name TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT '',
  object_key TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  processing_stage TEXT NOT NULL DEFAULT 'ready',
  processing_progress INTEGER NOT NULL DEFAULT 100,
  analysis_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

INSERT INTO evidence_v2 (
  id, case_id, original_name, name, purpose, object_key, mime_type, size_bytes, sha256, status, processing_stage, processing_progress, analysis_json, created_at
)
SELECT
  id, case_id, original_name, name, purpose, object_key, mime_type, size_bytes, sha256, status, processing_stage, processing_progress, analysis_json, created_at
FROM evidence;

DROP TABLE evidence;
ALTER TABLE evidence_v2 RENAME TO evidence;

CREATE INDEX IF NOT EXISTS idx_evidence_case_id ON evidence(case_id);
