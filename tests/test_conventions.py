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
APP_SRC = ROOT / "ahsync_app"                # the app shell (SPEC §3.7.1), held to the same conventions
APP_TESTS = ROOT / "tests" / "ahsync_app"
CLOUD_SRC = ROOT / "ahcloud"                # the cloud job (SPEC §2.4), held to the same conventions (M30)
INFRA_SRC = ROOT / "infra"                  # the CDK app (M30)
PY_TREES = ((PY_SRC, PY_TESTS), (APP_SRC, APP_TESTS), (CLOUD_SRC, ROOT / "tests" / "ahcloud"),
            (INFRA_SRC, ROOT / "tests" / "infra"))   # (source package, its mirrored tests)
TOOLS = ROOT / "tools"
CONVENTION_TOOLS = ("build.py", "promote.py", "record_blizzard.py", "build_lambda.py")   # tests in tests/tools/ (M26-M30)
TOOL_TESTS = ROOT / "tests" / "tools"
LUA_SRC = ROOT / "addon" / "AHProfit"
LUA_TESTS = ROOT / "tests" / "addon"
VISUAL_TESTS = ROOT / "tests" / "visual"     # UI scenarios (SPEC §8.4); exempt from mirroring
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
        Paths of every .py file under ahsync/ (excluding web/static) and ahsync_app/, then
        tools/build.py and tools/promote.py where they exist; sorted within each group.
    """
    static = PY_SRC / "web" / "static"
    ahsync = sorted(p for p in PY_SRC.rglob("*.py") if static not in p.parents)
    app = sorted(APP_SRC.rglob("*.py")) if APP_SRC.exists() else []
    cloud = sorted(p for root in (CLOUD_SRC, INFRA_SRC) if root.exists() for p in root.rglob("*.py"))
    tools = [TOOLS / name for name in CONVENTION_TOOLS if (TOOLS / name).exists()]
    return ahsync + app + cloud + tools


def mirrored_test(path: Path) -> Path:
    """The test file a source module must have (CLAUDE.md "Tests mirror source one-to-one").

    Args:
        path: A module from python_sources().

    Returns:
        tests/ahsync/<sub>/test_<name>.py, tests/ahsync_app/<sub>/test_<name>.py or tests/tools/test_<name>.py.
    """
    for source_root, test_root in PY_TREES:
        if source_root in path.parents:
            return test_root / path.parent.relative_to(source_root) / f"test_{path.name}"
    return TOOL_TESTS / f"test_{path.name}"


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
    visual = sorted(VISUAL_TESTS.rglob("*_spec.lua")) if VISUAL_TESTS.exists() else []
    for path in (sorted(LUA_TESTS.rglob("*_spec.lua")) if LUA_TESTS.exists() else []) + visual:
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

ANALYSIS_MAY_IMPORT = ("ahsync.engine", "ahsync.history.reads", "ahsync.intel.contract")


def test_AR4_analysis_imports_only_the_engine_and_the_two_contracts():
    import sys
    problems = []
    for path in sorted((PY_SRC / "analysis").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                stdlib = name.split(".")[0] in sys.stdlib_module_names or name == "__future__"
                allowed = any(name == m or name.startswith(m + ".") for m in ANALYSIS_MAY_IMPORT)
                if not (stdlib or allowed):
                    problems.append(f"{rel(path)}:{node.lineno} imports {name} (SPEC §5.0 invariant 1)")
    assert not problems, "\n".join(problems)


def imports_of(path: Path) -> list[tuple[int, str]]:
    """The absolute imports of a module.

    Args:
        path: A .py file.

    Returns:
        (line, module name) per imported module, in order.
    """
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


CLOUD_MAY_IMPORT = ("ahcloud", "ahsync.collect.blizzard", "ahsync.collect.client", "ahsync.collect.observations",
                    "ahsync.collect.local_scans",
                    "ahsync.collect.snapshots", "ahsync.collect.provider", "ahsync.analysis", "ahsync.intel.contract",
                    "ahsync.db", "ahsync.history", "ahsync.engine", "ahsync.clock")


def test_AR9_the_cloud_is_a_thin_wrapper_over_the_same_pipeline():
    import sys
    problems = []
    for path in sorted(CLOUD_SRC.rglob("*.py")):
        for line, name in imports_of(path):
            top = name.split(".")[0]
            if top == "boto3" and path.name != "aws.py":
                problems.append(f"{rel(path)}:{line} imports boto3 outside ahcloud/aws.py")
            elif top in sys.stdlib_module_names or name == "__future__" or top == "boto3":
                continue
            elif not any(name == m or name.startswith(m + ".") for m in CLOUD_MAY_IMPORT):
                problems.append(f"{rel(path)}:{line} imports {name}")
    for path in sorted(INFRA_SRC.rglob("*.py")):
        problems += [f"{rel(path)}:{line} imports {name}" for line, name in imports_of(path)
                     if name.split(".")[0] in ("ahsync", "ahcloud", "ahsync_app")]
    for root in (PY_SRC, APP_SRC):
        for path in sorted(root.rglob("*.py")):
            problems += [f"{rel(path)}:{line} imports {name}" for line, name in imports_of(path)
                         if name.split(".")[0] in ("ahcloud", "infra", "aws_cdk", "boto3")]
    assert not problems, "\n".join(problems)


def test_python_sources_and_tests_mirror():
    problems = []
    for path in python_sources():
        if path.name in PY_MIRROR_EXEMPT:
            continue
        test = mirrored_test(path)
        if not test.exists():
            problems.append(f"{rel(path)}: missing {rel(test)}")
    for source_root, test_root in PY_TREES:
        for test in sorted(test_root.rglob("test_*.py")) if test_root.exists() else []:
            source = source_root / test.parent.relative_to(test_root) / test.name.removeprefix("test_")
            if not source.exists():
                problems.append(f"{rel(test)}: no source {rel(source)}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------- the platform layer (SPEC §3.7.2, DR16)

OS_TOKENS = re.compile(r"\b(sys\.platform|os\.name|winreg|plistlib|launchctl|platform\.system)\b")
OS_SCANNED = ("ahsync", "ahsync_app", "tools")
OS_LAYER = "ahsync_app/platform/"
# Places that predate the layer or cannot import it, by file; an entry for a file without a token is stale.
OS_ALLOWLIST = ("ahsync/__main__.py",      # InstanceLock: msvcrt or fcntl; ahsync/ must not import ahsync_app/
                "tools/deploy.py",         # the detached start of AHSync
                "tools/bench.py",          # memory units of the peak-memory reading
                "tools/build.py")          # which packaging step runs (M26)


def os_token_problems(root: Path, allowlist: tuple[str, ...]) -> list[str]:
    """Find OS branches outside the platform layer and its named exceptions (DR16).

    Args:
        root: A repository root holding ahsync/, ahsync_app/ and tools/.
        allowlist: Repository-relative files, with forward slashes, allowed to hold a token.

    Returns:
        "file:line: token …" for each token outside ahsync_app/platform/ and the allowlist, then
        "stale allowlist entry: file" for each allowed file that holds none (or no longer exists).
    """
    problems, seen = [], set()
    for top in OS_SCANNED:
        for path in sorted((root / top).rglob("*.py")) if (root / top).exists() else []:
            name = path.relative_to(root).as_posix()
            if name.startswith(OS_LAYER):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in OS_TOKENS.finditer(line):
                    seen.add(name)
                    if name not in allowlist:
                        problems.append(f"{name}:{number}: {match.group(1)} outside {OS_LAYER} (SPEC §3.7.2)")
    problems += [f"stale allowlist entry: {name}" for name in allowlist if name not in seen]
    return problems


def test_DR16_os_branches_stay_in_the_platform_layer():
    problems = os_token_problems(ROOT, OS_ALLOWLIST)
    assert not problems, "\n".join(problems)


def test_DR16_planted_violations_and_a_stale_entry_fail_and_an_allowlisted_place_passes(tmp_path):
    def write(name: str, text: str) -> None:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    write("ahsync/app/setup.py", "import sys\n\nWIN = sys.platform == 'win32'\n")
    write("ahsync_app/shell.py", '"""The shell."""\nimport winreg\n')
    write("ahsync_app/platform/windows.py", "import winreg\n")
    write("tools/build.py", "import sys\nSTEP = 'iss' if sys.platform == 'win32' else 'dmg'\n")
    write("tools/bench.py", "print('no OS branch here')\n")
    assert os_token_problems(tmp_path, ("tools/build.py", "tools/bench.py")) == [
        "ahsync/app/setup.py:3: sys.platform outside ahsync_app/platform/ (SPEC §3.7.2)",
        "ahsync_app/shell.py:2: winreg outside ahsync_app/platform/ (SPEC §3.7.2)",
        "stale allowlist entry: tools/bench.py",
    ]


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
