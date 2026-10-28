-- Tests for automation/hammerspoon/init.lua with a pretend `hs`.
-- Usage: lua5.4 test_hammerspoon.lua <project root> <folder with data/.token and no hotkeys>
local project, root = arg[1], arg[2]
local cjson = require("cjson")

local TOKEN = "abcdefghijklmnopqrstuvwxyz012345"
local calls, bindings = {}, {}
local known_bundles = { ["com.apple.Safari"] = true }
local known_paths = { ["/Applications/Pathy.app"] = true }
local running = {}

local function fake_app(id)
  return {
    unhide = function() calls[#calls + 1] = "unhide " .. id end,
    activate = function() calls[#calls + 1] = "activate " .. id end,
    kill = function() calls[#calls + 1] = "kill " .. id end,
  }
end

local server = { log = {} }
function server:setInterface(v) self.log[#self.log + 1] = "interface " .. v; return self end
function server:setPort(v) self.log[#self.log + 1] = "port " .. tostring(v); return self end
function server:setCallback(f) self.callback = f; self.log[#self.log + 1] = "callback"; return self end
function server:start() self.log[#self.log + 1] = "start"; return self end
function server:stop() self.log[#self.log + 1] = "stop"; return self end

hs = {
  json = { encode = cjson.encode, decode = cjson.decode },
  application = {
    launchOrFocusByBundleID = function(id) calls[#calls + 1] = "launch bundle " .. id; return known_bundles[id] or false end,
    launchOrFocus = function(path) calls[#calls + 1] = "launch path " .. path; return known_paths[path] or false end,
    applicationsForBundleID = function(id) if running[id] then return { fake_app(id) } end return {} end,
  },
  hotkey = { bind = function(mods, key, fn) bindings[#bindings + 1] = { mods = mods, key = key, fn = fn } end },
  httpserver = { new = function(ssl, bonjour) server.ssl, server.bonjour = ssl, bonjour; return server end },
}

ASSISTANT_NO_AUTOSTART = true
local M = dofile(project .. "/automation/hammerspoon/init.lua")

local failures = 0
local function check(name, condition)
  if not condition then failures = failures + 1; print("FAIL " .. name) end
end

local function write(path, text)
  local f = assert(io.open(path, "w")); f:write(text); f:close()
end

local function request(method, path, headers, body)
  local out, status = M.handle(method, path, headers, body, root)
  return status, cjson.decode(out)
end

local function act(action, args, headers)
  local body = cjson.encode({ action = action, args = args })
  return request("POST", "/action", headers or { ["X-Assistant-Token"] = TOKEN }, body)
end

-- ---- the token ----------------------------------------------------------------------------------------------
os.remove(root .. "/data/.token")
local status, body = request("GET", "/ping", { ["X-Assistant-Token"] = TOKEN })
check("no token file -> 503", status == 503 and body.ok == false)

write(root .. "/data/.token", "short\n")
status = request("GET", "/ping", { ["X-Assistant-Token"] = "short" })
check("a too-short token is refused", status == 503)

write(root .. "/data/.token", TOKEN .. "\n")
status = request("GET", "/ping", {})
check("no header -> 401", status == 401)
status = request("GET", "/ping", { ["X-Assistant-Token"] = "wrong" })
check("wrong token -> 401", status == 401)
status = request("GET", "/ping", { ["X-Assistant-Token"] = TOKEN:sub(1, -2) })
check("almost right token -> 401", status == 401)
status, body = request("GET", "/ping", { ["x-assistant-token"] = TOKEN })
check("header names are case-insensitive", status == 200 and body.ok == true and body.version == M.VERSION)
status = act("open_app", { bundle_id = "com.apple.Safari" }, { ["X-Assistant-Token"] = "nope" })
check("actions need the token too", status == 401 and #calls == 0)

-- ---- open_app -----------------------------------------------------------------------------------------------------
status, body = act("open_app", { bundle_id = "com.apple.Safari", path = "/Applications/Safari.app" })
check("open by bundle id", status == 200 and body.launched == true and calls[1] == "launch bundle com.apple.Safari" and #calls == 1)

calls = {}
status, body = act("open_app", { bundle_id = "com.unknown.App", path = "/Applications/Pathy.app" })
check("falls back to the path", status == 200 and body.launched == true and #calls == 2 and calls[2] == "launch path /Applications/Pathy.app")

calls = {}
status, body = act("open_app", { bundle_id = "com.unknown.App", path = "/Applications/Nothing.app" })
check("reports a launch that did not work", status == 200 and body.ok == true and body.launched == false)

calls = {}
status = act("open_app", {})
check("open_app needs a target", status == 400 and #calls == 0)
status = act("open_app", { bundle_id = "bad id; rm -rf /", path = "/etc/passwd" })
check("odd identifiers and non-app paths are ignored", status == 400 and #calls == 0)
status = act("open_app", { path = "/Applications/../../bin/sh" })
check("paths must end in .app", status == 400 and #calls == 0)
status = act("open_app", { path = "relative/Thing.app" })
check("paths must be absolute", status == 400 and #calls == 0)
status = act("open_app", { bundle_id = 42 })
check("types are checked", status == 400 and #calls == 0)

-- ---- focus_app / quit_app --------------------------------------------------------------------------------------------
calls = {}
status, body = act("focus_app", { bundle_id = "com.apple.Safari" })
check("focus: not running", status == 200 and body.running == false and #calls == 0)
running["com.apple.Safari"] = true
status, body = act("focus_app", { bundle_id = "com.apple.Safari" })
check("focus: running", status == 200 and body.running == true and calls[1] == "unhide com.apple.Safari" and calls[2] == "activate com.apple.Safari")

calls = {}
running["com.apple.Safari"] = nil
status, body = act("quit_app", { bundle_id = "com.apple.Safari" })
check("quit: not running does nothing", status == 200 and body.running == false and #calls == 0)
running["com.apple.Safari"] = true
status, body = act("quit_app", { bundle_id = "com.apple.Safari" })
check("quit: running", status == 200 and body.running == true and calls[1] == "kill com.apple.Safari")
status = act("quit_app", { bundle_id = "a b" })
check("quit needs a valid id", status == 400)

-- ---- nothing else can be asked for ---------------------------------------------------------------------------------------
calls = {}
for _, name in ipairs({ "eval", "execute", "run", "shell", "load", "dofile", "os.execute", "__index", "handle", "" }) do
  status = act(name, { code = "os.execute('touch /tmp/pwned')" })
  check("unknown action '" .. name .. "' is refused", status == 400)
end
status = request("POST", "/action", { ["X-Assistant-Token"] = TOKEN }, cjson.encode({ args = {} }))
check("a request without an action is refused", status == 400)
status = request("POST", "/action", { ["X-Assistant-Token"] = TOKEN }, "not json")
check("unreadable json is refused", status == 400)
status = request("POST", "/action", { ["X-Assistant-Token"] = TOKEN }, "[1,2]")
check("json that is not an object is refused", status == 400)
status = request("POST", "/action", { ["X-Assistant-Token"] = TOKEN }, string.rep("x", M.MAX_BODY + 1))
check("huge bodies are refused", status == 413)
status = act("ping", "not a table")
check("args must be an object", status == 400)
status = request("GET", "/action", { ["X-Assistant-Token"] = TOKEN })
check("GET /action is refused", status == 404)
status = request("POST", "/other", { ["X-Assistant-Token"] = TOKEN }, "{}")
check("other paths are refused", status == 404)
check("nothing ran", #calls == 0)

-- ---- an action that crashes does not leak details ---------------------------------------------------------------------------
local original = hs.application.applicationsForBundleID
hs.application.applicationsForBundleID = function() error("secret /Users/me/private detail") end
status, body = act("focus_app", { bundle_id = "com.apple.Safari" })
check("a failing action gives a plain 500", status == 500 and body.error == "the action failed" and not cjson.encode(body):find("secret"))
hs.application.applicationsForBundleID = original

-- ---- starting the server: localhost only --------------------------------------------------------------------------------------
M.start(root, 9123)
check("the server is created without ssl or bonjour", server.ssl == false and server.bonjour == false)
check("localhost is set before the server starts",
  server.log[1] == "interface localhost" and server.log[2] == "port 9123" and server.log[3] == "callback" and server.log[4] == "start")
status = (function() local _, s = server.callback("GET", "/ping", { ["X-Assistant-Token"] = TOKEN }, ""); return s end)()
check("the callback answers requests", status == 200)
M.start(root, 9124)
check("restarting stops the old server first", server.log[5] == "stop")

-- ---- hotkeys ------------------------------------------------------------------------------------------------------------------
bindings = {}
check("no hotkeys.lua means no hotkeys", M.load_hotkeys(root) == 0 and #bindings == 0)
local n = M.bind_hotkeys({
  { mods = { "cmd", "alt" }, key = "c", app = "Google Chrome" },
  { mods = "cmd", key = "x", app = "Bad" },
  { mods = { "cmd" }, key = 5, app = "Bad" },
  { mods = { "cmd" }, key = "y", app = "" },
  "nonsense",
  { mods = { "ctrl" }, key = "n", app = "Notes" },
})
check("only well-formed hotkeys are bound", n == 2 and #bindings == 2)
calls = {}
local launched_app
hs.application.launchOrFocus = function(a) launched_app = a; return true end
bindings[1].fn()
check("a hotkey opens its app", launched_app == "Google Chrome")
check("bad input is ignored", M.bind_hotkeys(nil) == 0 and M.bind_hotkeys("x") == 0)

local broken_root = root .. "/broken"
os.execute("mkdir -p '" .. broken_root .. "/automation/hammerspoon'")
write(broken_root .. "/automation/hammerspoon/hotkeys.lua", "return {{ mods={'cmd'}, key='k', app='Notes' }}")
bindings = {}
check("hotkeys.lua is loaded", M.load_hotkeys(broken_root) == 1)
write(broken_root .. "/automation/hammerspoon/hotkeys.lua", "this is not lua (")
check("a broken hotkeys.lua is survived", M.load_hotkeys(broken_root) == 0)
write(broken_root .. "/automation/hammerspoon/hotkeys.lua", "error('boom')")
check("an erroring hotkeys.lua is survived", M.load_hotkeys(broken_root) == 0)

if failures > 0 then
  print(failures .. " check(s) failed")
  os.exit(1)
end
print("ok")
