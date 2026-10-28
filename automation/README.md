# automation/

macOS automation through Hammerspoon (phase 4).

- `hammerspoon/init.lua` is the config that Hammerspoon loads for this project.
- The backend sends named actions (for example `app.open`, `window.focus`) to a localhost-only listener with a shared token.
- Hammerspoon never evaluates text it receives. There is no Lua-eval or shell action.
- Install a Hammerspoon release that supports macOS 12. The current `main` targets macOS 13.

Phase 0 contains only an empty `init.lua`.
