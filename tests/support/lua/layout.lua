--- Layout snapshots of the stub frames (SPEC §8.4 layer 2).
--- Resolves anchors into rectangles on a 1920 × 1080 screen at UI scale 1 (origin top
--- left, y down), writes an indented frame-tree snapshot, and runs the automatic checks:
--- every shown region lies inside its parent, text fits its box unless it truncates, sibling
--- rows don't overlap, buttons are at least 20 px tall.

local Layout = {}

Layout.GLYPH = 0.55            -- average glyph width as a share of the font size
Layout.MIN_BUTTON_HEIGHT = 20
local POINTS = { TOPLEFT = { 0, 0 }, TOP = { 0.5, 0 }, TOPRIGHT = { 1, 0 }, LEFT = { 0, 0.5 }, CENTER = { 0.5, 0.5 },
                 RIGHT = { 1, 0.5 }, BOTTOMLEFT = { 0, 1 }, BOTTOM = { 0.5, 1 }, BOTTOMRIGHT = { 1, 1 } }

--- Text as the player sees it: colour codes and links reduced to their visible characters.
---@param text string|nil
---@return string
function Layout.Visible(text)
  text = tostring(text or "")
  text = text:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|cnIQ%d:", ""):gsub("|r", "")
  text = text:gsub("|H.-|h(.-)|h", "%1"):gsub("|T.-|t", "  ")
  return text
end

--- Estimated width of a text in px.
---@param text string|nil
---@param size number  Font size
---@return number
function Layout.TextWidth(text, size)
  local visible = Layout.Visible(text)
  -- Count characters, not bytes: "…" and "×" are one glyph.
  local _, chars = visible:gsub("[^\128-\191]", "")
  return chars * size * Layout.GLYPH
end

--- The region's own size: set, or a font string's text size.
---@param r table
---@return number, number
local function OwnSize(r)
  if r.kind == "FontString" then
    return r.width or Layout.TextWidth(r.text, r.fontSize), r.height or r.fontSize
  end
  return r.width or 0, r.height or 0
end

--- One axis of a rectangle from its anchored edges, as WoW resolves them: two edges fix the
--- size (start and end, or an edge and the centre); one edge places the region's own size.
---@param edges table  [0] = start, [0.5] = centre, [1] = end coordinates that are anchored
---@param size number  The region's own size on this axis
---@return number, number  Start coordinate and size
local function Span(edges, size)
  local s, c, e = edges[0], edges[0.5], edges[1]
  if s and e then return s, e - s end
  if s and c then return s, 2 * (c - s) end
  if c and e then return 2 * c - e, 2 * (e - c) end
  if s then return s, size end
  if e then return e - size, size end
  if c then return c - size / 2, size end
  return 0, size
end

--- Resolve every region's rectangle.
---@param root table  UIParent
---@return table      region -> { x, y, w, h }
function Layout.Resolve(root)
  local rects = { [root] = { x = 0, y = 0, w = root.width, h = root.height } }
  local function Rect(r)
    if rects[r] then return rects[r] end
    local w, h = OwnSize(r)
    local x, y = {}, {}                -- edge constraints: [0] start, [0.5] centre, [1] end
    for _, p in ipairs(r.points) do
      local rel = Rect(p.rel)
      local f, rf = POINTS[p.point], POINTS[p.relPoint]
      x[f[1]] = rel.x + rel.w * rf[1] + p.x
      y[f[2]] = rel.y + rel.h * rf[2] - p.y
    end
    local left, width = Span(x, w)
    local top, height = Span(y, h)
    local rect = { x = left, y = top, w = width, h = height, unanchored = #r.points == 0 }
    rects[r] = rect
    return rect
  end
  local function Walk(r)
    Rect(r)
    for _, child in ipairs(r.children) do Walk(child) end
  end
  Walk(root)
  return rects
end

--- A font string's text as shown: cut with "…" when a non-wrapping box is too narrow. The box
--- is its set width, or the width its anchors give it (WoW truncates both).
---@param r table
---@param width number|nil  The resolved width; default the set width
---@return string|nil
function Layout.ShownText(r, width)
  if r.text == nil then return nil end
  local visible = Layout.Visible(r.text)
  width = r.width or width
  if r.kind ~= "FontString" or not width or r.wrap then return visible end
  if Layout.TextWidth(visible, r.fontSize) <= width then return visible end
  local chars = {}
  for ch in visible:gmatch("[%z\1-\127\194-\244][\128-\191]*") do chars[#chars + 1] = ch end
  while #chars > 0 and Layout.TextWidth(table.concat(chars) .. "…", r.fontSize) > width do chars[#chars] = nil end
  return table.concat(chars) .. "…"
end

--- The indented frame-tree snapshot.
---@param root table  The region to start at (its subtree)
---@param rects table From Layout.Resolve
---@return string
function Layout.Snapshot(root, rects)
  local lines = {}
  local function Walk(r, depth)
    local rect = rects[r]
    local line = string.format("%s%s %s %d,%d %dx%d", string.rep("  ", depth), r.kind, r.name or "-",
                               math.floor(rect.x + 0.5), math.floor(rect.y + 0.5), math.floor(rect.w + 0.5),
                               math.floor(rect.h + 0.5))
    local text = Layout.ShownText(r, rect.w)
    if text and text ~= "" then line = line .. ' "' .. text .. '"' end
    if not r.shown then
      -- Hidden frames are listed; hidden text and textures are noise (a row holds every tab's cells).
      if r.kind ~= "FontString" and r.kind ~= "Texture" then lines[#lines + 1] = line .. " (hidden)" end
      return
    end
    lines[#lines + 1] = line
    for _, child in ipairs(r.children) do Walk(child, depth + 1) end
  end
  Walk(root, 0)
  return table.concat(lines, "\n") .. "\n"
end

--- The automatic layout checks over the shown regions under root.
---@param root table
---@param rects table  From Layout.Resolve
---@return string[]    Problems; empty when the layout passes
function Layout.Check(root, rects)
  local problems = {}
  local function Label(r) return r.kind .. " " .. (r.name or "-") .. (r.text and (' "' .. Layout.Visible(r.text) .. '"') or "") end
  local function Walk(r)
    if not r.shown then return end
    local rect, parent = rects[r], r.parent and rects[r.parent]
    if rect.unanchored and r ~= root and r.kind ~= "Texture" then problems[#problems + 1] = Label(r) .. " has no anchor" end
    if parent and r ~= root and r.kind ~= "Texture" and not rect.unanchored then
      if rect.x < parent.x - 0.5 or rect.y < parent.y - 0.5 or rect.x + rect.w > parent.x + parent.w + 0.5
        or rect.y + rect.h > parent.y + parent.h + 0.5 then
        problems[#problems + 1] = Label(r) .. " lies outside its parent"
      end
    end
    if r.kind == "Button" and rect.h < Layout.MIN_BUTTON_HEIGHT then
      problems[#problems + 1] = Label(r) .. " is under 20 px tall"
    end
    local rows = {}
    for _, child in ipairs(r.children) do
      if child.shown and child.name and child.name:match("Row%d+$") then rows[#rows + 1] = rects[child] end
      Walk(child)
    end
    for i = 1, #rows - 1 do
      for j = i + 1, #rows do
        local a, b = rows[i], rows[j]
        if a.y < b.y + b.h - 0.5 and b.y < a.y + a.h - 0.5 then
          problems[#problems + 1] = Label(r) .. ": rows " .. i .. " and " .. j .. " overlap"
        end
      end
    end
  end
  Walk(root)
  return problems
end

--- Read a whole file, or nil when it doesn't exist.
---@param path string
---@return string|nil
local function ReadFile(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local text = f:read("*a")
  f:close()
  return text
end

--- Write a whole file.
---@param path string
---@param text string
local function WriteFile(path, text)
  local f = assert(io.open(path, "wb"))
  f:write(text)
  f:close()
end

--- Lines of a text, without carriage returns.
---@param text string
---@return string[]
local function Lines(text)
  local out = {}
  for line in (text:gsub("\r", "")):gmatch("([^\n]*)\n") do out[#out + 1] = line end
  return out
end

--- Check a frame tree and compare its snapshot with tests/visual/golden/<name>.txt (SPEC §8.4).
--- The snapshot is also written to tests/visual/out/<name>.txt for `tools/visual.py --html`.
--- With AHP_UPDATE_VISUAL=1 the golden file is rewritten instead; review its diff like code.
---@param name string  Scenario name, e.g. "UI1_today"
---@param root table   The window's top frame
---@return string      The snapshot
function Layout.Golden(name, root)
  local rects = Layout.Resolve(_G.UIParent)
  local problems = Layout.Check(root, rects)
  if #problems > 0 then error("layout checks failed:\n  " .. table.concat(problems, "\n  "), 2) end
  local text = Layout.Snapshot(root, rects)
  if package.config:sub(1, 1) == "\\" then
    os.execute([[mkdir "tests\visual\out" 2>nul]])
  else
    os.execute("mkdir -p tests/visual/out")
  end
  WriteFile("tests/visual/out/" .. name .. ".txt", text)
  local golden = "tests/visual/golden/" .. name .. ".txt"
  if os.getenv("AHP_UPDATE_VISUAL") == "1" then
    WriteFile(golden, text)
    return text
  end
  local expected = ReadFile(golden)
  if expected == nil then error("no golden snapshot " .. golden .. "; run python tools/visual.py --update", 2) end
  local want, got = Lines(expected), Lines(text)
  local n = 1
  while n <= math.max(#want, #got) and want[n] == got[n] do n = n + 1 end
  if n <= math.max(#want, #got) then
    error(string.format("snapshot %s differs at line %d:\n  golden: %s\n  now:    %s\n"
                        .. "(python tools/visual.py --update accepts it; review the diff)",
                        name, n, tostring(want[n]), tostring(got[n])), 2)
  end
  return text
end

return Layout
