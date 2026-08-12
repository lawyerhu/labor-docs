ALTER TABLE evidence ADD COLUMN processing_stage TEXT NOT NULL DEFAULT 'ready';
ALTER TABLE evidence ADD COLUMN processing_progress INTEGER NOT NULL DEFAULT 100;
ALTER TABLE evidence ADD COLUMN analysis_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE generation_jobs ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE generation_jobs ADD COLUMN progress INTEGER NOT NULL DEFAULT 0;
