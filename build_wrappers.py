"""Generate the wrapper packages that install search-clanker-history with a default agent scope.

search-clanker-history is the only codebase. A wrapper is metadata only: it depends on the same
version of search-clanker-history, installs a command named after itself that starts the same entry point, and declares
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
    "search-claude-history": {
        "agent": "claude", "label": "Claude Code", "other": "codex",
        "keywords": ["claude-code", "claude", "anthropic", "session-history", "search", "ai-agents", "cli"],
    },
    "search-codex-history": {
        "agent": "codex", "label": "Codex", "other": "claude",
        "keywords": ["codex", "openai", "session-history", "search", "ai-agents", "cli"],
    },
}

PYPROJECT = '''[build-system]
requires = ["hatchling==1.32.4"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "{version}"
description = "Search your {label} session history from the command line"
readme = "README.md"
keywords = [{keywords}]
requires-python = ">=3.10"
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

[project.entry-points."search_clanker_history.default_agents"]
{agent} = "search_clanker_history"

[tool.hatch.build.targets.wheel]
bypass-selection = true
'''

README = '''# {name}

Search your {label} session history from the command line: find the conversation where you solved something, a command you ran, or an error you hit, with regular expressions, time filters, and surrounding context. It searches {label} by default and can search your other coding agents too, without reinstalling anything (see below).

```bash
uv tool install {name}
{name} "pattern"
```

`{name}` is a build of [{core}]({repository}), which also searches other coding agents, so it installs {core} at the same version and defaults to {label}. The documentation lives there.

To search other agents as well, change the default once, with no reinstall:

```bash
{name} --default-agents {agent} {other}     # for example, add another agent
{name} --default-agents all               # every agent that has history
{name} --default-agents reset             # back to {label} only
```

`{name} --agent AGENT` overrides the default for a single search. The choice is saved in `~/.config/search-history/config.json` (under `$XDG_CONFIG_HOME` when that is set, and in `%APPDATA%\\search-history\\config.json` on Windows), and `{name} --default-agents` prints the exact path. The settings file is shared by every package, so a default you save applies to all of them.

Already have an older `search-claude-history`? `uv tool upgrade {name}` keeps your behavior, but the short command `sch` is gone; use `{name}`, or make `sch` an alias for it. `{name} --default-agents all` adds the other agents.
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
    values["keywords"] = ", ".join(f'"{word}"' for word in spec["keywords"])
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
