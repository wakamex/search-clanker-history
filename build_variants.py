"""Build one distribution per agent set from the single source tree.

Source files mark agent-specific code with comments that this script deletes for the
packages that do not include that agent:

    # agents: claude codex         (Python/TOML; <!-- agents: claude codex --> in Markdown)
    ...kept only by packages that include claude or codex...
    # end agents

A trailing `# agents: NAME` on a code line applies to that line alone. Besides agent names,
`multi` is true for packages with more than one agent and `single` for packages with one.

Usage: python build_variants.py [OUT_DIR] [PACKAGE ...]   (default OUT_DIR: dist-variants)
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIST = "search-claude-history"
TEMPLATE_IMPORT = "search_claude_history"
AGENTS = ("claude", "codex", "opencode", "hermes")

# Every package ships `sch`, so they are alternatives: install the one for the agents you use.
VARIANTS = {
    "search-clanker-history": {
        "agents": AGENTS,
        "commands": ("sch", "search-clanker-history"),
        "description": "Search Claude Code, Codex, OpenCode and Hermes session history",
    },
    "search-claude-history": {
        "agents": ("claude",),
        "commands": ("sch", "search-claude-history"),
        "description": "Search Claude Code session history",
    },
    "search-codex-history": {
        "agents": ("codex",),
        "commands": ("sch", "search-codex-history"),
        "description": "Search Codex session history",
    },
}

_TAGS = r"(?P<tags>[a-z]+(?: [a-z]+)*)"
BLOCK_START = re.compile(rf"^\s*(?:#|<!--)\s*agents:\s*{_TAGS}\s*(?:-->)?\s*$")
BLOCK_END = re.compile(r"^\s*(?:#|<!--)\s*end agents\s*(?:-->)?\s*$")
LINE_TAG = re.compile(rf"^(?P<code>.*\S)\s+(?:#|<!--)\s*agents:\s*{_TAGS}\s*(?:-->)?\s*$")


def active_tags(agents):
    return set(agents) | {"multi" if len(agents) > 1 else "single"}


def check_tags(tags, where):
    unknown = set(tags.split()) - set(AGENTS) - {"multi", "single"}
    if unknown:
        raise SystemExit(f"{where}: unknown agent tag {sorted(unknown)}")


def strip(text, agents, name):
    """Delete the lines tagged for agents this package does not include."""
    active = active_tags(agents)
    out, skipping, in_block = [], False, False
    for number, line in enumerate(text.split("\n"), 1):
        where = f"{name}:{number}"
        if m := BLOCK_START.match(line):
            if in_block:
                raise SystemExit(f"{where}: agent blocks cannot nest")
            check_tags(m["tags"], where)
            in_block, skipping = True, not (set(m["tags"].split()) & active)
        elif BLOCK_END.match(line):
            if not in_block:
                raise SystemExit(f"{where}: `end agents` without a block")
            in_block = skipping = False
        elif skipping:
            continue
        elif m := LINE_TAG.match(line):
            check_tags(m["tags"], where)
            if set(m["tags"].split()) & active:
                out.append(m["code"])
        else:
            out.append(line)
    if in_block:
        raise SystemExit(f"{name}: unterminated agent block")
    blank_limit = "\n\n" if name.endswith(".md") else "\n\n\n"
    return re.sub(r"\n{3,}" if name.endswith(".md") else r"\n{4,}", blank_limit, "\n".join(out))


def rename(text, dist, import_name):
    return text.replace(TEMPLATE_DIST, dist).replace(TEMPLATE_IMPORT, import_name)


def pyproject(text, dist, import_name, spec):
    scripts = "\n".join(f'{c} = "_{import_name}_entry:main"' for c in spec["commands"])
    text = re.sub(r"\[project\.scripts\]\n(?:.+\n)+", f"[project.scripts]\n{scripts}\n", text)
    text = re.sub(r'(?m)^description = ".*"$', f'description = "{spec["description"]}"', text)
    return rename(text, dist, import_name)


def build(dist, out_root):
    spec = VARIANTS[dist]
    agents = spec["agents"]
    import_name = dist.replace("-", "_")
    out = out_root / dist
    if out.exists():
        shutil.rmtree(out)
    package = out / "src" / import_name
    package.mkdir(parents=True)

    def read(path):
        return (ROOT / path).read_text()

    def write(path, text):
        path.write_text(text)
        if path.suffix == ".py":
            compile(text, str(path), "exec")  # a bad cut fails the build

    source = ROOT / "src" / TEMPLATE_IMPORT
    cli = strip(read(source / "cli.py"), agents, "cli.py")
    cli = cli.replace(f'_DIST_NAME = "{TEMPLATE_DIST}"', f'_DIST_NAME = "{dist}"')
    write(package / "cli.py", cli)
    write(package / "__init__.py", rename(read(source / "__init__.py"), dist, import_name))
    write(package / "__main__.py", rename(read(source / "__main__.py"), dist, import_name))
    entry = rename(read(f"src/_{TEMPLATE_IMPORT}_entry.py"), dist, import_name)
    write(out / "src" / f"_{import_name}_entry.py", entry)

    readme = strip(read("README.md"), agents, "README.md")
    write(out / "README.md", rename(readme, dist, import_name))
    write(out / "pyproject.toml", pyproject(strip(read("pyproject.toml"), agents, "pyproject.toml"), dist, import_name, spec))
    for name in ("LICENSE", ".python-version"):
        shutil.copy(ROOT / name, out / name)
    return out


def main(argv):
    out_root = Path(argv[0]) if argv else ROOT / "dist-variants"
    for dist in argv[1:] or VARIANTS:
        print(build(dist, out_root))


if __name__ == "__main__":
    main(sys.argv[1:])
