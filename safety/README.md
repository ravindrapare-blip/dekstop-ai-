# safety/

Every action the model proposes passes through this package before anything runs.

Phase 0 contains only the risk tiers (`risk.py`) and a draft policy (`policy.yaml`). The classifier, approval flow and audit log are built in phase 2.

Principles (full text in `docs/architecture.md` section 4):

- Deny by default: unlisted actions do not exist.
- Tier 0 and 1 run automatically, tier 2 needs approval each time, tier 3 is never offered.
- No shell or Lua evaluation tools.
- Web, file and clipboard content is data and can never raise a tier or skip an approval.
- Approvals show the exact action and time out to deny.
- Every proposal, decision and result is logged.
