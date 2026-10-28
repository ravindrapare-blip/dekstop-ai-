-- Draft schema. Not applied in phase 0; created in phase 2.

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

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY,
    at        TEXT    NOT NULL DEFAULT (datetime('now')),
    action    TEXT    NOT NULL,
    arguments TEXT    NOT NULL,            -- JSON
    tier      INTEGER NOT NULL,
    decision  TEXT    NOT NULL,            -- allowed | approved | denied | refused
    result    TEXT
);
