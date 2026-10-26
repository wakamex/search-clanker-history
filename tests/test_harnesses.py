"""Check sch's history paths against a vendored copy of the agent spec sheet.

tests/harnesses.toml is harnesses.toml from https://github.com/wakamex/agent-spec-sheet at the
commit below, copied unchanged. To pick up a change, copy the new file and update the commit.
"""

import importlib.util
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]
HARNESSES = Path(__file__).with_name("harnesses.toml")
HARNESSES_COMMIT = "55725a0"

spec = importlib.util.spec_from_file_location("sch_cli", ROOT / "src" / "search_clanker_history" / "cli.py")
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)

# sch agent name -> spec sheet entry
ENTRIES = {
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "hermes": "hermes",
    "antigravity": "agy",
    "gemini": "gemini",
    "zcode": "zcode",
    "grok": "grok",
    "devin": "devin",
    "dsh": "dsh",
    "cursor": "cursor",
    "muse": "muse",
}

# The paths each agent's reader opens: a directory it walks, or a file pattern it reads.
READ_PATHS = {
    "claude": lambda: cli._claude_roots(),
    "codex": lambda: cli._codex_roots(),
    "opencode": lambda: [cli._opencode_db()],
    "hermes": lambda: [cli._hermes_db()],
    "antigravity": lambda: [cli._antigravity_dir() / "brain/<id>/.system_generated/logs" / cli._AGY_TRANSCRIPT],
    "gemini": lambda: [cli._gemini_dir() / "tmp"],
    "zcode": lambda: [cli._zcode_db()],
    "grok": lambda: [cli._grok_dir() / "sessions"],
    "devin": lambda: [cli._devin_db()],
    "dsh": lambda: [cli._dsh_dir() / "sessions"],
    "cursor": lambda: [cli._cursor_dir() / "chats"],
    "muse": lambda: [cli._muse_dir() / "sessions"],
}


# Session locations the spec sheet lists that sch deliberately does not read.
SKIPPED = {
    "claude": {"projects/<project>/<session>/subagents/agent-*.jsonl"},  # subagent runs repeat their parent's work
    "antigravity": {
        "brain/<id>/.system_generated/logs/transcript.jsonl",  # a subset of transcript_full.jsonl
        "conversations/<id>.db",  # the same steps as undocumented protobuf
    },
    "devin": {"session_locks/<name>.lock"},  # marks a live session; holds no messages
    "muse": {
        "sessions/YYYY/MM/DD/<session id>/subagent/<id>/session.jsonl",  # subagent runs repeat their parent's work
        "session-index.db",  # a search index of the session files
    },
}

# Every variable the spec sheet names as moving a home that sch reads.
HOME_VARIABLES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME", "HERMES_HOME", "GEMINI_CLI_HOME", "GROK_HOME", "DSH_HOME")


def expand_home(home, env):
    """The spec sheet's home value, with ${VAR:-default} and ~ resolved against env."""
    home = re.sub(r"\$\{(\w+):-([^}]*)\}", lambda m: env.get(m.group(1)) or m.group(2), home)
    return Path(env["HOME"] + home[1:] if home.startswith("~") else home)


@unittest.skipIf(tomllib is None, "tomllib needs Python 3.11")
class SpecSheetTests(unittest.TestCase):
    def setUp(self):
        self.spec = tomllib.loads(HARNESSES.read_text())

    def test_readers_list_exactly_the_agents_sch_searches(self):
        listed = {key for key, entry in self.spec.items() if "sch" in entry.get("readers", [])}
        self.assertEqual(listed, set(ENTRIES.values()))
        self.assertEqual(set(cli.SOURCES), set(ENTRIES))

    def test_read_paths_are_the_spec_sheet_locations_with_and_without_the_home_variable(self):
        with tempfile.TemporaryDirectory() as temporary:
            for moved in (False, True):
                env = {"HOME": temporary}
                if moved:
                    env.update({name: os.path.join(temporary, "moved", name) for name in HOME_VARIABLES})
                with mock.patch.dict(os.environ, env, clear=True):
                    for agent, key in ENTRIES.items():
                        entry = self.spec[key]
                        home = expand_home(entry["home"], env)
                        read = [path.relative_to(home).as_posix() for path in READ_PATHS[agent]()]
                        covered = {s for s in entry["sessions"] for r in read if s == r or s.startswith(r + "/")}
                        with self.subTest(agent=agent, moved=moved):
                            for relative in read:
                                self.assertTrue(
                                    any(s == relative or s.startswith(relative + "/") for s in entry["sessions"]),
                                    f"sch reads {relative}, which is not among {entry['sessions']}",
                                )
                            skipped = SKIPPED.get(agent, set())
                            self.assertLessEqual(skipped, set(entry["sessions"]), "a skipped location left the spec sheet")
                            self.assertEqual(set(entry["sessions"]) - skipped - covered, set(), "locations sch does not read")

if __name__ == "__main__":
    unittest.main()
