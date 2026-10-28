-- Everything the assistant remembers. One local file, data/assistant.db, readable by this user only.
-- Times are local time, ISO 8601 text (no time zone). Version 1.

CREATE TABLE reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,      -- never reused, so "delete 3" can't hit a newer reminder
    text       TEXT NOT NULL,
    due_at     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'fired')),
    created_at TEXT NOT NULL,
    fired_at   TEXT
);
CREATE INDEX idx_reminders_due ON reminders (status, due_at);

CREATE TABLE tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    details      TEXT NOT NULL DEFAULT '',
    due_date     TEXT,                                  -- YYYY-MM-DD or NULL
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX idx_tasks_status ON tasks (status, due_date);

CREATE TABLE notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- What you asked for (trimmed), newest last. Kept short and pruned automatically; see memory/README.md.
CREATE TABLE command_history (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    text   TEXT NOT NULL DEFAULT '',
    tool   TEXT NOT NULL,
    status TEXT NOT NULL
);
