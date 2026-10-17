--- Minimal Lua 5.1 test runner for the addon (SPEC §8.1).
--- Usage, from the repository root: lua5.1 tests/support/lua/runner.lua <dir-or-spec-file>...
--- Finds every *_spec.lua under the given directories, runs each `it` block
--- in a protected call, prints failures with their traceback, and exits
--- non-zero if any test failed. Tests are named "<ID> <name>" (for example
--- it("P12 long band competitor expires", ...)) so test_conventions.py can
--- match them to SCENARIOS.md.

if _VERSION ~= "Lua 5.1" then
  -- WoW runs 5.1; 5.3+ differ in integer division and # (CLAUDE.md).
  io.stderr:write("runner.lua needs Lua 5.1, got " .. tostring(_VERSION) .. "\n")
  os.exit(2)
end

package.path = "./?.lua;./tests/support/lua/?.lua;" .. package.path

local IS_WINDOWS = package.config:sub(1, 1) == "\\"

--- Sentinel the generated scenario fixtures use for JSON null (tools/gen_lua_fixtures.py).
JSON_NULL = setmetatable({}, { __tostring = function() return "JSON_NULL" end })

local results = { passed = 0, failed = 0, failures = {} }
local currentFile, currentGroup = nil, nil

--- List the spec files under a path, sorted so runs are deterministic.
---@param path string  A directory or a single *_spec.lua file, relative to the root
---@return string[]    Spec file paths
local function FindSpecs(path)
  if path:match("_spec%.lua$") then return { path } end
  local command
  if IS_WINDOWS then
    command = 'dir /s /b "' .. path:gsub("/", "\\") .. '\\*_spec.lua" 2>nul'
  else
    command = "find '" .. path .. "' -name '*_spec.lua' 2>/dev/null"
  end
  local files = {}
  local pipe = io.popen(command)
  for line in pipe:lines() do
    files[#files + 1] = line
  end
  pipe:close()
  table.sort(files)
  return files
end

--- Group related tests under a heading; groups may nest.
---@param name string  Group name, shown in failure messages
---@param body function  Defines the group's tests
function describe(name, body)
  local outer = currentGroup
  currentGroup = outer and (outer .. " / " .. name) or name
  body()
  currentGroup = outer
end

--- Run one test in a protected call and record the result.
---@param name string  "<ID> <name in plain English>"
---@param body function  The test; it fails by raising an error
function it(name, body)
  local ok, err = xpcall(body, debug.traceback)
  if ok then
    results.passed = results.passed + 1
  else
    results.failed = results.failed + 1
    local where = currentGroup and (currentGroup .. " / " .. name) or name
    results.failures[#results.failures + 1] = currentFile .. ": " .. where .. "\n" .. tostring(err)
  end
end

--- Describe a value for a failure message.
---@param value any  Any Lua value
---@return string    A short readable form; strings are quoted
local function Show(value)
  if type(value) == "string" then return string.format("%q", value) end
  return tostring(value)
end

--- Compare two values deeply: tables by their keys and values, everything else with ==.
---@param a any  First value
---@param b any  Second value
---@param path string  Location within the outer value, for the message
---@return string|nil  nil when equal, else where and how they differ
local function Diff(a, b, path)
  if type(a) ~= "table" or type(b) ~= "table" then
    if a == b then return nil end
    return path .. ": " .. Show(a) .. " ~= " .. Show(b)
  end
  for k, v in pairs(a) do
    local d = Diff(v, b[k], path .. "[" .. Show(k) .. "]")
    if d then return d end
  end
  for k, v in pairs(b) do
    if a[k] == nil then return path .. "[" .. Show(k) .. "]: nil ~= " .. Show(v) end
  end
  return nil
end

--- Fail the current test unless actual deeply equals expected.
---@param actual any    Value the code produced
---@param expected any  Value the scenario expects
---@param why string|nil  The scenario's explanation, printed on failure
function assert_eq(actual, expected, why)
  local d = Diff(actual, expected, "value")
  if d then error(d .. (why and ("\nwhy: " .. why) or ""), 2) end
end

--- Run every spec found under the command-line paths and report.
---@param paths string[]  Directories or spec files
---@return number         Process exit code: 0 when all passed
local function Main(paths)
  if #paths == 0 then paths = { "tests/addon" } end
  for _, path in ipairs(paths) do
    for _, file in ipairs(FindSpecs(path)) do
      currentFile = file
      local chunk, loadErr = loadfile(file)
      if not chunk then
        it("load", function() error(loadErr) end)
      else
        local ok, err = xpcall(chunk, debug.traceback)
        if not ok then it("load", function() error(err) end) end
      end
    end
  end
  for _, failure in ipairs(results.failures) do
    io.write("FAIL ", failure, "\n\n")
  end
  io.write(string.format("Lua: %d passed, %d failed\n", results.passed, results.failed))
  return results.failed == 0 and 0 or 1
end

os.exit(Main({ ... }))
