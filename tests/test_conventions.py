"""Repository conventions from CLAUDE.md and SPEC §8.1.

Checks docstrings, size limits, source/test mirroring and that SCENARIOS.md
agrees with the tests. Each test collects every violation and reports them all
at once, so one run shows everything to fix.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_SRC = ROOT / "ahsync"
PY_TESTS = ROOT / "tests" / "ahsync"
LUA_SRC = ROOT / "addon" / "AHProfit"
LUA_TESTS = ROOT / "tests" / "addon"
SCENARIOS_MD = ROOT / "docs" / "SCENARIOS.md"
PENDING = ROOT / "tests" / "pending_scenarios.txt"

MAX_FUNCTION_LINES = 200
MAX_FILE_LINES = 1000

PY_MIRROR_EXEMPT = {"__init__.py", "__main__.py"}
LUA_MIRROR_EXEMPT = {Path("UI/Demo.lua")}
LUA_DATA_DIR = Path("Data")          # data files: exempt from mirroring and size (CLAUDE.md)
LUA_GENERATED_DIR = Path("generated")  # tests/addon/generated: Lua form of tests/scenarios (SPEC §5)

ID = r"[A-Z]{1,3}\d+"
SCENARIO_ROW = re.compile(rf"^\|\s*({ID})\s*\|")
PY_TEST_ID = re.compile(rf"^test_({ID})_")
LUA_TEST_ID = re.compile(rf"""\bit\s*\(\s*["']({ID})\s""")


# ---------------------------------------------------------------- helpers

def python_sources() -> list[Path]:
    """List AHSync source modules subject to the conventions.

    Returns:
        Paths of every .py file under ahsync/, excluding web/static, sorted.
    """
    static = PY_SRC / "web" / "static"
    return sorted(p for p in PY_SRC.rglob("*.py") if static not in p.parents)


def lua_sources() -> list[Path]:
    """List addon source files subject to the conventions.

    Returns:
        Paths of every .lua file under addon/AHProfit/, excluding Data/, sorted.
    """
    data = LUA_SRC / LUA_DATA_DIR
    return sorted(p for p in LUA_SRC.rglob("*.lua") if data not in p.parents)


def rel(path: Path) -> str:
    """Format a path relative to the repository root for messages.

    Args:
        path: An absolute path inside the repository.

    Returns:
        The path relative to the root, with forward slashes.
    """
    return path.relative_to(ROOT).as_posix()


def strip_lua_strings_and_comments(line: str) -> str:
    """Remove string literals and comments from one line of Lua.

    Good enough for counting block keywords; long strings spanning lines are
    not supported, and the addon doesn't use them in code.

    Args:
        line: One source line.

    Returns:
        The line with quoted strings replaced by "" and any comment removed.
    """
    line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
    line = re.sub(r"'(?:\\.|[^'\\])*'", "''", line)
    return line.split("--", 1)[0]


LUA_NAMED_FUNCTION = re.compile(
    r"^\s*(?:local\s+function\s+([\w.:]+)"          # local function Name(
    r"|function\s+([\w.:]+)"                        # function A.B( / A:B(
    r"|(?:local\s+)?([\w.]+)\s*=\s*function\b)"     # Name = function(
)
LUA_OPENERS = re.compile(r"\b(function|if|do)\b")
LUA_CLOSER = re.compile(r"\bend\b")


def lua_function_length(lines: list[str], start: int) -> int:
    """Count the lines of the Lua function that starts at a given line.

    Args:
        lines: All lines of the file.
        start: 0-based index of the line holding the `function` keyword.

    Returns:
        Number of lines from start through the matching `end`, inclusive;
        the rest of the file if no matching `end` is found.
    """
    depth = 0
    for i in range(start, len(lines)):
        code = strip_lua_strings_and_comments(lines[i])
        depth += len(LUA_OPENERS.findall(code)) - len(LUA_CLOSER.findall(code))
        if depth <= 0:
            return i - start + 1
    return len(lines) - start


def listed_scenario_ids() -> list[str]:
    """Read every scenario and invariant ID from SCENARIOS.md, in file order.

    Returns:
        IDs from the first column of each table row, duplicates included.
    """
    ids = []
    for line in SCENARIOS_MD.read_text(encoding="utf-8").splitlines():
        match = SCENARIO_ROW.match(line)
        if match:
            ids.append(match.group(1))
    return ids


def expand_pending(text: str) -> tuple[list[str], list[str]]:
    """Parse tests/pending_scenarios.txt.

    Each non-comment token is an ID ("P12") or a same-prefix range ("V1-V18").
    Text after '#' is a comment.

    Args:
        text: The file's contents.

    Returns:
        (pending IDs in file order, error messages for malformed tokens).
    """
    ids, errors = [], []
    for raw in text.splitlines():
        for token in raw.split("#", 1)[0].split():
            single = re.fullmatch(ID, token)
            ranged = re.fullmatch(r"([A-Z]{1,3})(\d+)-([A-Z]{1,3})(\d+)", token)
            if single:
                ids.append(token)
            elif ranged and ranged.group(1) == ranged.group(3) and int(ranged.group(2)) <= int(ranged.group(4)):
                prefix = ranged.group(1)
                ids.extend(f"{prefix}{n}" for n in range(int(ranged.group(2)), int(ranged.group(4)) + 1))
            else:
                errors.append(f"pending_scenarios.txt: bad token {token!r}")
    return ids, errors


def find_tested_ids() -> dict[str, list[str]]:
    """Find every scenario ID that has a test, and where.

    Returns:
        ID -> places it is tested ("tests/x.py::test_P12_…", "tests/addon/…_spec.lua",
        "tests/scenarios/x.json").
    """
    found: dict[str, list[str]] = {}
    for path in sorted((ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                match = PY_TEST_ID.match(node.name)
                if match:
                    found.setdefault(match.group(1), []).append(f"{rel(path)}::{node.name}")
    for path in sorted(LUA_TESTS.rglob("*_spec.lua")) if LUA_TESTS.exists() else []:
        for match in LUA_TEST_ID.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(match.group(1), []).append(rel(path))
    for path in sorted((ROOT / "tests" / "scenarios").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for scenario in data if isinstance(data, list) else [data]:
            if "id" in scenario:
                found.setdefault(scenario["id"], []).append(rel(path))
    return found


def python_docstring_problems(path: Path) -> list[str]:
    """Check one Python module's functions and classes against CLAUDE.md.

    Every function, method and class needs a docstring. A function with
    parameters (besides self/cls) needs an "Args:" section, and one with a
    non-None return annotation needs "Returns:". Public functions need type
    hints on every parameter and the return value.

    Args:
        path: A source module under ahsync/.

    Returns:
        One message per problem, "file:line name: problem".
    """
    problems = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        where = f"{rel(path)}:{node.lineno} {node.name}"
        doc = ast.get_docstring(node)
        if not doc:
            problems.append(f"{where}: missing docstring")
            continue
        if isinstance(node, ast.ClassDef):
            continue
        params = [a for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                  if a.arg not in ("self", "cls")]
        if node.args.vararg:
            params.append(node.args.vararg)
        if node.args.kwarg:
            params.append(node.args.kwarg)
        if params and "Args:" not in doc:
            problems.append(f"{where}: docstring has no Args: section")
        returns_value = node.returns is not None and not (
            isinstance(node.returns, ast.Constant) and node.returns.value is None)
        if returns_value and "Returns:" not in doc and "Yields:" not in doc:
            problems.append(f"{where}: docstring has no Returns: section")
        if not node.name.startswith("_"):
            unhinted = [a.arg for a in params if a.annotation is None]
            if unhinted:
                problems.append(f"{where}: no type hint for {', '.join(unhinted)}")
            if node.returns is None:
                problems.append(f"{where}: no return type hint")
    return problems


# ---------------------------------------------------------------- docstrings

def test_python_functions_have_docstrings_and_hints():
    problems = [p for path in python_sources() for p in python_docstring_problems(path)]
    assert not problems, "\n".join(problems)


def test_lua_named_functions_have_doc_blocks():
    problems = []
    for path in lua_sources():
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            match = LUA_NAMED_FUNCTION.match(strip_lua_strings_and_comments(line))
            if not match:
                continue
            above = next((lines[j].strip() for j in range(i - 1, -1, -1) if lines[j].strip()), "")
            if not above.startswith("---"):
                name = next(g for g in match.groups() if g)
                problems.append(f"{rel(path)}:{i + 1} {name}: no --- doc block above")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------- size limits

def test_files_within_size_limit():
    too_long = []
    for path in python_sources() + lua_sources():
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > MAX_FILE_LINES:
            too_long.append(f"{rel(path)}: {count} lines (max {MAX_FILE_LINES}); split it by responsibility")
    assert not too_long, "\n".join(too_long)


def test_functions_within_size_limit():
    too_long = []
    for path in python_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = node.end_lineno - node.lineno + 1
                if length > MAX_FUNCTION_LINES:
                    too_long.append(f"{rel(path)}:{node.lineno} {node.name}: {length} lines")
    for path in lua_sources():
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if re.search(r"\bfunction\b", strip_lua_strings_and_comments(line)):
                length = lua_function_length(lines, i)
                if length > MAX_FUNCTION_LINES:
                    too_long.append(f"{rel(path)}:{i + 1}: function of {length} lines")
    assert not too_long, "\n".join(too_long) + f"\n(max {MAX_FUNCTION_LINES}; refactor first)"


# ---------------------------------------------------------------- mirroring

def test_python_sources_and_tests_mirror():
    problems = []
    for path in python_sources():
        if path.name in PY_MIRROR_EXEMPT:
            continue
        test = PY_TESTS / path.parent.relative_to(PY_SRC) / f"test_{path.name}"
        if not test.exists():
            problems.append(f"{rel(path)}: missing {rel(test)}")
    for test in sorted(PY_TESTS.rglob("test_*.py")) if PY_TESTS.exists() else []:
        source = PY_SRC / test.parent.relative_to(PY_TESTS) / test.name.removeprefix("test_")
        if not source.exists():
            problems.append(f"{rel(test)}: no source {rel(source)}")
    assert not problems, "\n".join(problems)


def test_lua_sources_and_tests_mirror():
    problems = []
    for path in lua_sources():
        relative = path.relative_to(LUA_SRC)
        if relative in LUA_MIRROR_EXEMPT:
            continue
        spec = LUA_TESTS / relative.parent / f"{path.stem}_spec.lua"
        if not spec.exists():
            problems.append(f"{rel(path)}: missing {rel(spec)}")
    for spec in sorted(LUA_TESTS.rglob("*_spec.lua")) if LUA_TESTS.exists() else []:
        relative = spec.relative_to(LUA_TESTS)
        if relative.parts[0] == LUA_GENERATED_DIR.name:
            continue
        source = LUA_SRC / relative.parent / (spec.stem.removesuffix("_spec") + ".lua")
        if not source.exists():
            problems.append(f"{rel(spec)}: no source {rel(source)}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------- scenarios

def test_scenario_ids_are_unique():
    ids = listed_scenario_ids()
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"listed more than once in SCENARIOS.md: {', '.join(duplicates)}"


def test_every_listed_scenario_has_a_test_or_is_pending():
    listed = set(listed_scenario_ids())
    pending, errors = expand_pending(PENDING.read_text(encoding="utf-8"))
    tested = find_tested_ids()
    problems = list(errors)
    problems += [f"{i}: listed in SCENARIOS.md but has no test (add one, or add it to pending_scenarios.txt)"
                 for i in sorted(listed - tested.keys() - set(pending))]
    problems += [f"{i}: has a test ({tested[i][0]}) but isn't listed in SCENARIOS.md"
                 for i in sorted(tested.keys() - listed)]
    problems += [f"{i}: has a test ({tested[i][0]}); remove it from pending_scenarios.txt"
                 for i in sorted(set(pending) & tested.keys())]
    problems += [f"{i}: in pending_scenarios.txt but not listed in SCENARIOS.md"
                 for i in sorted(set(pending) - listed)]
    problems += [f"{i}: appears more than once in pending_scenarios.txt"
                 for i in sorted({i for i in pending if pending.count(i) > 1})]
    assert not problems, "\n".join(problems)
