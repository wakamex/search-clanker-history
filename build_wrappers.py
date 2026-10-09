"""Generate the wrapper packages that install search-clanker-history with a default agent scope.

search-clanker-history is the only codebase. A wrapper is metadata only: it depends on the same
version of search-clanker-history, installs commands that start the same entry point, and declares
its default agent as an entry point in the search_clanker_history.default_agents group. The tool
reads that metadata to find its default scope, which a saved --default-agents choice overrides.

Usage: python build_wrappers.py [OUT_DIR] [PACKAGE ...]   (default OUT_DIR: dist-wrappers)
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE = "search-clanker-history"
REPOSITORY = "https://github.com/wakamex/search-clanker-history"

WRAPPERS = {
    "search-claude-history": {"agent": "claude", "label": "Claude Code", "other": "codex"},
    "search-codex-history": {"agent": "codex", "label": "Codex", "other": "claude"},
}

PYPROJECT = '''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "{version}"
description = "Search {label} session history with search-clanker-history"
readme = "README.md"
requires-python = ">=3.9"
license = "MIT"
dependencies = ["{core}=={version}"]

[project.urls]
Homepage = "{repository}"
Source = "{repository}"

[project.optional-dependencies]
hyperscan = ["{core}[hyperscan]=={version}"]
ripgrep = ["{core}[ripgrep]=={version}"]

[project.scripts]
{name} = "_search_clanker_history_entry:main"
sch = "_search_clanker_history_entry:main"

[project.entry-points."search_clanker_history.default_agents"]
{agent} = "search_clanker_history"

[tool.hatch.build.targets.wheel]
bypass-selection = true
'''

README = '''# {name}

This package installs [{core}]({repository}) and makes `sch` search {label} by default. The code is the same as in `{core}`, which is also where the documentation lives.

```bash
uv tool install {name}
sch "pattern"
```

To search other agents as well, change the default once, with no reinstall:

```bash
sch --default-agents {agent} {other}     # for example, add another agent
sch --default-agents all               # every agent that has history
sch --default-agents reset             # back to {label} only
```

`sch --agent AGENT` overrides the default for a single search. The choice is saved in `~/.config/search-history/config.json` (under `$XDG_CONFIG_HOME` when that is set), and `sch --default-agents` prints the exact path.

Already have an older `search-claude-history`? `uv tool upgrade {name}` keeps your behavior, and `sch --default-agents all` adds the other agents.
'''


def core_version():
    match = re.search(r'(?m)^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text())
    if not match:
        raise SystemExit("no version in pyproject.toml")
    return match[1]


def build(name, out_root):
    spec = WRAPPERS[name]
    values = {
        "name": name, "version": core_version(), "core": CORE,
        "repository": REPOSITORY, **spec,
    }
    out = out_root / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "pyproject.toml").write_text(PYPROJECT.format(**values))
    (out / "README.md").write_text(README.format(**values))
    shutil.copy(ROOT / "LICENSE", out / "LICENSE")
    return out


def main(argv):
    out_root = Path(argv[0]) if argv else ROOT / "dist-wrappers"
    for name in argv[1:] or WRAPPERS:
        print(build(name, out_root))


if __name__ == "__main__":
    main(sys.argv[1:])
