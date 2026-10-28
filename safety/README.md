# safety/

Decides whether a tool call may run. **It never runs anything.** It imports nothing from `backend/`, `voice/` or `automation/`, and a test enforces that.

## Risk levels

| Level | What happens |
|---|---|
| low | Runs automatically. Always logged. |
| medium | Asks first: "I can do this: ... Please confirm." |
| high | Blocked by default. Runs only after an explicit approval **and** an explicit acknowledgment of the risk. |
| critical | Never runs. No approval unlocks it. |

Each tool's level is in `policy.yaml`. A tool with no rule cannot run (deny by default). Argument checks can raise the level, never lower it:

- protected locations (`/System`, `/etc`, `~/.ssh`, `~/Library`, ...), with `..` and symlinks resolved: high
- opening files that can run code (`.pkg`, `.sh`, `.command`, `.dmg`, ...): high
- closing Finder, Dock and other core macOS apps: high
- web addresses other than http(s), such as `javascript:`, `file:` or `mailto:`: critical
- login or payment looking addresses: medium
- shell characters in app names, control characters, wrong argument types, volume outside 0-100: critical
- a tool the model flagged `requires_confirmation`: at least medium

- clicking a button named Delete, Pay, Buy ... : high; Send, Submit, Sign in ... : medium (`click_*_words`)
- typing into a password, card or one-time-code box: critical (never); typing or clicking on a sign-in page: medium, on a payment page: high
- browser calls whose target was not identified on the page (`target`, `field_kind`): critical

Blocked by default (see `blocked:` in `policy.yaml`): deleting files and folders, emptying trash, renaming or moving files, logging in to websites, changing system settings (high); shell commands, scripts, software installs (critical). None of these tools exist, so the model cannot select them. The list gives a clear refusal if anything ever asks.

## Flow

```
evaluate(call)            ->  ALLOW      the executor may run it now
                              CONFIRM    ask the user; nothing runs yet (returns a confirmation_id)
                              BLOCK      refused, with a reason
                              DRY_RUN    describe only
confirm(id, approved,     ->  ALLOW      exactly the call that was asked about, once
        acknowledge_risk)     BLOCK      denied, expired, unknown, or risk not acknowledged
```

- Confirmations expire after `safety.approval_timeout_seconds` (default 60). At most `safety.max_pending` wait at once.
- Approval authorizes the stored call only. The caller cannot swap in different arguments.
- `confirm` re-checks the call, in case something changed since it was asked.
- **Dry run:** `evaluate(call, dry_run=True)`, or `safety.dry_run: true` for everything. Nothing is authorized and no confirmation is created. Blocks stay blocks.
- **Audit log:** every decision is written to `data/logs/audit.jsonl` *before* it is returned. If it can't be written, nothing is authorized. Values are cut to 80 characters; text typed into web pages (`value`) is recorded only as its length. The file is capped at about 2 MB with one backup.

## HTTP

`POST /route` returns the tool call, a `guard` object (null while a question is open or when nothing was found) and a `result` (what was done). `POST /confirm` with `{"confirmation_id", "approved", "acknowledge_risk"}` returns the final decision plus the result. `POST /choose` answers a "which one?" question. The backend runs an action only after an authorized decision (`backend/pipeline.py`).

The backend listens on localhost only, but until the shared token is added (phase 9) any local process could call `/confirm`.

## Rules for executors (phases 5 to 8)

1. Run a tool only for a decision with `authorized: true`, using the call inside that decision.
2. If the executor resolves a name to a real path or address (for example "Downloads" to a folder, or a bare domain to `https://...`), the resolved value must be judged before acting. The guard can only check what it is given. For apps and files this is built in: `backend/pipeline.py` resolves the name first and gives the safety layer the real target.
3. Never pass arguments to a shell.
