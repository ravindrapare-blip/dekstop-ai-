-- Local assistant, Hammerspoon side.
--
-- A small server that listens on localhost only and does a fixed list of named things:
--   ping, open_app, focus_app, quit_app
-- Every request must carry the shared token from <root>/data/.token. Nothing that arrives is ever
-- evaluated as code, and there is no shell or file access here: the Python backend decides what is
-- allowed and sends only the app's bundle id and path.
--
-- Loaded by the block that scripts/setup_hammerspoon.sh adds to ~/.hammerspoon/init.lua, which sets
-- ASSISTANT_ROOT (the project folder) and ASSISTANT_PORT first.

local M = {}

M.VERSION = "1"
M.MAX_BODY = 4096

-- ---- small helpers ---------------------------------------------------------------------------------------

local function read_token(root)
  local file = io.open(root .. "/data/.token", "r")
  if not file then return nil end
  local token = file:read("*l")
  file:close()
  if token and #token >= 16 then return token end
  return nil
end

-- Compares without stopping at the first difference.
local function same(a, b)
  if type(a) ~= "string" or type(b) ~= "string" or #a ~= #b then return false end
  local difference = 0
  for i = 1, #a do difference = difference | (a:byte(i) ~ b:byte(i)) end
  return difference == 0
end

local function header(headers, wanted)
  wanted = wanted:lower()
  for name, value in pairs(headers or {}) do
    if type(name) == "string" and name:lower() == wanted then return value end
  end
  return nil
end

local function valid_bundle_id(value)
  return type(value) == "string" and #value <= 255 and value:match("^[%w][%w%._%-]*$") ~= nil
end

local function valid_app_path(value)
  return type(value) == "string" and #value <= 1024 and value:sub(1, 1) == "/"
    and value:match("%.app/?$") ~= nil and not value:find("%c")
end

local function running_app(bundle_id)
  if not valid_bundle_id(bundle_id) then return nil end
  local apps = hs.application.applicationsForBundleID(bundle_id)
  return apps and apps[1] or nil
end

-- ---- the actions ---------------------------------------------------------------------------------------------

local actions = {}

function actions.ping()
  return { ok = true, version = M.VERSION }
end

-- Starts the app, or brings it to the front if it is already running.
function actions.open_app(args)
  local launched = false
  if valid_bundle_id(args.bundle_id) then
    launched = hs.application.launchOrFocusByBundleID(args.bundle_id) and true or false
  end
  if not launched and valid_app_path(args.path) then
    launched = hs.application.launchOrFocus(args.path) and true or false
  end
  if not valid_bundle_id(args.bundle_id) and not valid_app_path(args.path) then
    return { ok = false, error = "need a bundle id or an app path" }
  end
  return { ok = true, launched = launched }
end

-- Brings a running app to the front. Does not start apps that are not running.
function actions.focus_app(args)
  if not valid_bundle_id(args.bundle_id) then return { ok = false, error = "need a bundle id" } end
  local app = running_app(args.bundle_id)
  if not app then return { ok = true, running = false } end
  app:unhide()
  app:activate()
  return { ok = true, running = true }
end

-- Asks a running app to quit, like Cmd+Q. It may still ask about unsaved work.
function actions.quit_app(args)
  if not valid_bundle_id(args.bundle_id) then return { ok = false, error = "need a bundle id" } end
  local app = running_app(args.bundle_id)
  if not app then return { ok = true, running = false } end
  app:kill()
  return { ok = true, running = true }
end

-- ---- the request handler (the part that is easy to test) -------------------------------------------------------

local function respond(status, body)
  return hs.json.encode(body), status, { ["Content-Type"] = "application/json" }
end

-- Returns body, status code, headers: what hs.httpserver expects.
function M.handle(method, path, headers, body, root)
  local expected = read_token(root)
  if not expected then return respond(503, { ok = false, error = "token file missing" }) end
  if not same(header(headers, "X-Assistant-Token"), expected) then
    return respond(401, { ok = false, error = "bad token" })
  end

  if method == "GET" and path == "/ping" then
    return respond(200, actions.ping())
  end
  if method ~= "POST" or path ~= "/action" then
    return respond(404, { ok = false, error = "not found" })
  end
  if type(body) ~= "string" or #body > M.MAX_BODY then
    return respond(413, { ok = false, error = "request too large" })
  end

  local parsed, request = pcall(hs.json.decode, body)
  if not parsed or type(request) ~= "table" then
    return respond(400, { ok = false, error = "unreadable request" })
  end
  local name = request.action
  local handler = type(name) == "string" and actions[name] or nil
  if not handler then return respond(400, { ok = false, error = "unknown action" }) end

  local args = request.args
  if args == nil then args = {} end
  if type(args) ~= "table" then return respond(400, { ok = false, error = "args must be an object" }) end

  local worked, result = pcall(handler, args)
  if not worked then
    print("assistant: action " .. name .. " failed: " .. tostring(result))   -- Hammerspoon console only
    return respond(500, { ok = false, error = "the action failed" })
  end
  return respond(result.ok and 200 or 400, result)
end

-- ---- optional hotkeys to open apps ---------------------------------------------------------------------------------

-- hotkeys.lua (in this folder, yours to create) returns a list like:
--   return { { mods = {"cmd", "alt"}, key = "c", app = "Google Chrome" } }
function M.bind_hotkeys(list)
  local bound = 0
  if type(list) ~= "table" then return bound end
  for _, item in ipairs(list) do
    if type(item) == "table" and type(item.mods) == "table" and type(item.key) == "string"
      and type(item.app) == "string" and #item.app > 0 then
      local app = item.app
      hs.hotkey.bind(item.mods, item.key, function() hs.application.launchOrFocus(app) end)
      bound = bound + 1
    end
  end
  return bound
end

function M.load_hotkeys(root)
  local chunk = loadfile(root .. "/automation/hammerspoon/hotkeys.lua")
  if not chunk then return 0 end
  local ok, list = pcall(chunk)
  if not ok then
    print("assistant: hotkeys.lua has an error: " .. tostring(list))
    return 0
  end
  return M.bind_hotkeys(list)
end

-- ---- starting ----------------------------------------------------------------------------------------------------------

function M.start(root, port)
  if M.server then M.server:stop() end
  local server = hs.httpserver.new(false, false)
  server:setInterface("localhost")              -- hs.httpserver listens on every interface unless told not to
  server:setPort(port or 8767)
  server:setCallback(function(method, path, headers, body)
    return M.handle(method, path, headers, body, root)
  end)
  server:start()
  M.server = server
  M.load_hotkeys(root)
  print("assistant: listening on localhost:" .. tostring(port or 8767))
  return server
end

if type(ASSISTANT_ROOT) == "string" and not ASSISTANT_NO_AUTOSTART then
  M.start(ASSISTANT_ROOT, tonumber(ASSISTANT_PORT) or 8767)
end

return M
