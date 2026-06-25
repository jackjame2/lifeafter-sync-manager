-- License Key Management System - D1 Schema
-- Migration 0001: Initial schema

CREATE TABLE IF NOT EXISTS license_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key_hash TEXT NOT NULL UNIQUE,
  key_prefix TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  activated_at TEXT,
  expires_at TEXT,
  hwid TEXT,
  hwid_2 TEXT,
  activation_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT DEFAULT ''
);

CREATE INDEX idx_keys_status ON license_keys(status);
CREATE INDEX idx_keys_hwid ON license_keys(hwid);
CREATE INDEX idx_keys_created_at ON license_keys(created_at);

CREATE TABLE IF NOT EXISTS usage_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key_id INTEGER,
  action TEXT NOT NULL,
  ip_address TEXT DEFAULT '',
  hwid TEXT DEFAULT '',
  details TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (key_id) REFERENCES license_keys(id) ON DELETE SET NULL
);

CREATE INDEX idx_logs_key_id ON usage_logs(key_id);
CREATE INDEX idx_logs_created_at ON usage_logs(created_at);
CREATE INDEX idx_logs_action ON usage_logs(action);
