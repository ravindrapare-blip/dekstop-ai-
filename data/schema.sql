-- Draft schema. Created in phase 7.

CREATE TABLE IF NOT EXISTS reminders (
    id        INTEGER PRIMARY KEY,
    text      TEXT    NOT NULL,
    due_at    TEXT    NOT NULL,            -- ISO 8601, local time
    done      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT   NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memory (
    id        INTEGER PRIMARY KEY,
    key       TEXT    NOT NULL,
    value     TEXT    NOT NULL,
    created_at TEXT   NOT NULL DEFAULT (datetime('now'))
);

-- The audit log is a JSON-lines file (data/logs/audit.jsonl), not a table.
