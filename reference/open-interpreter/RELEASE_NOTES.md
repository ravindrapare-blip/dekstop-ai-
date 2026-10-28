# Open Interpreter 0.0.43

Open Interpreter 0.0.43 is a maintenance release based on the stable upstream
Codex `rust-v0.154.0` compatibility baseline.

## Maintenance highlights

- Google now offers `gemini-3.8-flash` and `gemini-3.7-flash` in its easy model
  choices; `glm-5.1` and the inherited OpenAI choices remain available.
- Z.AI now has an explicit `zai-zcode` Messages preset at
  `https://api.z.ai/api/anthropic`, alongside the existing generic Z.AI Chat
  providers.
- `interpreter acp` exposes Harness as a selectable session configuration and
  persists changes through the shared interpreter harness catalog.

## Models

- **Inherited upstream presets:** `gpt-6-astra`, `gpt-5.6-sol`,
  `gpt-5.6-terra`, and `gpt-5.6-luna`. These exact IDs and their metadata are
  inherited from upstream Codex; Open Interpreter does not add aliases for
  them, and availability still depends on the selected provider and account.
- **Google AI Studio IDs:** `gemini-3.8-flash`, `gemini-3.7-flash`,
  `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`, and
  `gemini-3.1-flash-lite`. These are provider-documented IDs surfaced in the
  [model guide](docs/models.md), not Open Interpreter-owned model aliases.
- **Anthropic IDs:** `claude-fable-5-1`, `claude-opus-5`, `claude-sonnet-5`, and `claude-haiku-4-5-20251001`. These are current active provider-documented IDs surfaced in the model guide.
- **Z.AI IDs:** `glm-5.1`, `glm-5`, `glm-5-turbo`, and the free/lightweight `glm-4.7-flash`. These are the provider-documented
  starting IDs; use the exact ID returned by the selected Z.AI service or plan.
  Generic Z.AI uses Chat Completions, while the separate ZCode setup uses its
  compatible Messages endpoint.
- Open Interpreter-specific work here is provider/catalog, transport,
  harness, and product integration. The list above is not a promise that every
  provider, region, account, wire API, or harness accepts every ID. Use `/model`
  and the active provider's model list as the final authority.

## CLI and branding

- Chat Completions is easier to find and select through root help, `exec` help,
  provider guidance, and generated Bash, Zsh, Fish, and PowerShell completions.
- User-facing help, version, errors, cloud guidance, and diagnostics identify
  Open Interpreter consistently. Compatibility names remain only where they
  describe an upstream protocol, crate, model, or configuration alias.
- `exec` configuration help explains user configuration loading more clearly.

## Local model diagnostics

- `scripts/ollama_qwen_smoke.py` provides a reusable, generic Ollama diagnostic
  harness for Qwen tool-use checks. It bounds captured output, cleans up timed
  out process groups, records tool-call signals, and verifies requested file
  side effects separately from an assistant's final message.
- Its deterministic Python tests are cheap enough for routine checks. Live
  Ollama inference is intentionally opt-in for a relevant model report,
  transport/tool-use change, or fix validation, and may require generous
  timeouts for slow local inference.
