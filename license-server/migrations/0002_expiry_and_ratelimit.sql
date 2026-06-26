-- License Key Management System - D1 Schema
-- Migration 0002: time-limited keys (duration) + admin auth rate limiting

-- Per-key validity window in hours. The clock starts at first activation:
-- on bind, expires_at is set to activated_at + duration_hours. Default 3 hours.
ALTER TABLE license_keys ADD COLUMN duration_hours INTEGER NOT NULL DEFAULT 3;

-- Records failed admin-auth attempts per IP for rate limiting / lockout.
CREATE TABLE IF NOT EXISTS auth_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip_address TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_auth_attempts_ip ON auth_attempts(ip_address);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_created ON auth_attempts(created_at);
