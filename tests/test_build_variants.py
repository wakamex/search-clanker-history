import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_variants", ROOT / "build_variants.py")
build_variants = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_variants)


class StripTests(unittest.TestCase):
    def test_blocks_and_line_tags_follow_the_included_agents(self):
        text = "\n".join([
            "keep",
            "# agents: codex",
            "codex only",
            "# end agents",
            "import x  # agents: claude codex",
            "<!-- agents: multi -->",
            "several",
            "<!-- end agents -->",
        ])
        both = build_variants.strip(text, ("claude", "codex"), "t.py").split("\n")
        self.assertEqual(both, ["keep", "codex only", "import x", "several"])
        claude = build_variants.strip(text, ("claude",), "t.py").split("\n")
        self.assertEqual(claude, ["keep", "import x"])

    def test_malformed_tags_fail_the_build(self):
        for text in ("# agents: codex\nx", "# end agents", "# agents: nope\nx\n# end agents"):
            with self.subTest(text=text), self.assertRaises(SystemExit):
                build_variants.strip(text, ("claude",), "t.py")


class BuiltPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name) / "variants"
        for dist in build_variants.VARIANTS:
            build_variants.build(dist, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def source(self, dist):
        package = dist.replace("-", "_")
        return (self.out / dist / "src" / package / "cli.py").read_text()

    def test_single_agent_packages_contain_no_other_agent_code(self):
        claude = self.source("search-claude-history").lower()
        codex = self.source("search-codex-history").lower()
        for word in ("codex", "opencode", "hermes", "sqlite"):
            self.assertNotIn(word, claude)
        for word in ("opencode", "hermes", "sqlite", "_claude_roots", "classify_line("):
            self.assertNotIn(word, codex)

    def test_package_metadata_is_rewritten(self):
        pyproject = (self.out / "search-codex-history" / "pyproject.toml").read_text()
        self.assertIn('name = "search-codex-history"', pyproject)
        self.assertIn('sch = "_search_codex_history_entry:main"', pyproject)
        self.assertIn('search-codex-history = "_search_codex_history_entry:main"', pyproject)
        self.assertIn('search_codex_history = "search_codex_history.cli:SOURCES"', pyproject)
        self.assertIn("github.com/wakamex/search-clanker-history", pyproject)
        readme = (self.out / "search-codex-history" / "README.md").read_text()
        self.assertNotIn("Claude Code](", readme)
        self.assertNotIn("--agent", readme)

    def run_package(self, dist, *args, home):
        env = dict(os.environ, NO_COLOR="1", HOME=str(home), PYTHONPATH=str(self.out / dist / "src"))
        for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME", "XDG_DATA_HOME"):
            env.pop(name, None)
        return subprocess.run(
            [sys.executable, "-S", "-m", dist.replace("-", "_"), *args],
            capture_output=True, text=True, env=env, check=False,
        )

    def test_each_package_searches_only_its_agent(self):
        with tempfile.TemporaryDirectory() as home:
            home = Path(home)
            claude = home / ".claude" / "projects" / "-code-demo"
            claude.mkdir(parents=True)
            (claude / "0123abcd-0000-0000-0000-000000000000.jsonl").write_text(json.dumps({
                "type": "user", "timestamp": "2026-01-02T03:04:05Z",
                "message": {"role": "user", "content": "plugh from claude"},
            }) + "\n")
            codex = home / ".codex" / "sessions" / "2026" / "01" / "02"
            codex.mkdir(parents=True)
            sid = "01a10334-5465-7210-a943-b674c6eea5b6"
            (codex / f"rollout-2026-01-02T03-04-04-{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in (
                {"timestamp": "2026-01-02T03:04:04Z", "type": "session_meta", "payload": {"id": sid, "cwd": "/code/demo"}},
                {"timestamp": "2026-01-02T03:04:05Z", "type": "response_item", "payload": {
                    "type": "message", "role": "user", "content": [{"type": "input_text", "text": "plugh from codex"}]}},
            )) + "\n")

            only_claude = self.run_package("search-claude-history", "plugh", home=home)
            only_codex = self.run_package("search-codex-history", "plugh", home=home)
            everything = self.run_package("search-clanker-history", "plugh", home=home)
            wrong_agent = self.run_package("search-claude-history", "plugh", "--agent", "codex", home=home)

        self.assertIn("plugh from claude", only_claude.stdout)
        self.assertNotIn("plugh from codex", only_claude.stdout)
        self.assertIn("plugh from codex", only_codex.stdout)
        self.assertNotIn("plugh from claude", only_codex.stdout)
        self.assertIn("plugh from claude", everything.stdout)
        self.assertIn("plugh from codex", everything.stdout)
        self.assertNotEqual(wrong_agent.returncode, 0)

    def install_metadata(self, site, dist, import_name):
        """Fake the dist-info an installed package leaves behind, including its agent entry point."""
        info = Path(site) / f"{import_name}-0.1.3.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: 0.1.3\n")
        (info / "entry_points.txt").write_text(
            f"[search_history.agents]\n{import_name} = {import_name}.cli:SOURCES\n")

    def test_sch_searches_every_installed_package_whichever_one_runs(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as site:
            home = Path(home)
            claude = home / ".claude" / "projects" / "-code-demo"
            claude.mkdir(parents=True)
            (claude / "0123abcd-0000-0000-0000-000000000000.jsonl").write_text(json.dumps({
                "type": "user", "timestamp": "2026-01-02T03:04:05Z",
                "message": {"role": "user", "content": "xyzzy from claude"},
            }) + "\n")
            codex = home / ".codex" / "sessions" / "2026" / "01" / "02"
            codex.mkdir(parents=True)
            sid = "01a10334-5465-7210-a943-b674c6eea5b6"
            (codex / f"rollout-2026-01-02T03-04-04-{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in (
                {"timestamp": "2026-01-02T03:04:04Z", "type": "session_meta", "payload": {"id": sid, "cwd": "/code/demo"}},
                {"timestamp": "2026-01-02T03:04:05Z", "type": "response_item", "payload": {
                    "type": "message", "role": "user", "content": [{"type": "input_text", "text": "xyzzy from codex"}]}},
            )) + "\n")
            self.install_metadata(site, "search-claude-history", "search_claude_history")
            self.install_metadata(site, "search-codex-history", "search_codex_history")
            path = os.pathsep.join([str(self.out / d / "src") for d in ("search-claude-history", "search-codex-history")] + [site])
            env = dict(os.environ, NO_COLOR="1", HOME=str(home), PYTHONPATH=path)
            for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME", "XDG_DATA_HOME"):
                env.pop(name, None)

            def run(module, *args):
                return subprocess.run([sys.executable, "-S", "-m", module, "xyzzy", *args],
                                      capture_output=True, text=True, env=env, check=False)

            from_claude, from_codex = run("search_claude_history"), run("search_codex_history")
            only_codex = run("search_claude_history", "--agent", "codex")
            help_text = subprocess.run([sys.executable, "-S", "-m", "search_claude_history", "--help"],
                                       capture_output=True, text=True, env=env, check=False).stdout

        for result in (from_claude, from_codex):
            self.assertIn("xyzzy from claude", result.stdout)
            self.assertIn("xyzzy from codex", result.stdout)
            self.assertIn("2 match(es)", result.stdout)
        self.assertIn("xyzzy from codex", only_codex.stdout)
        self.assertNotIn("xyzzy from claude", only_codex.stdout)
        self.assertIn("Claude Code, Codex", help_text)

    def test_broken_sibling_package_is_skipped_with_a_warning(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as site:
            self.install_metadata(site, "search-ghost-history", "search_ghost_history")
            env = dict(os.environ, HOME=home, PYTHONPATH=os.pathsep.join(
                [str(self.out / "search-claude-history" / "src"), site]))
            result = subprocess.run([sys.executable, "-S", "-m", "search_claude_history", "--help"],
                                    capture_output=True, text=True, env=env, check=False)

        self.assertEqual(result.returncode, 0)
        self.assertIn("cannot load agents from search_ghost_history.cli:SOURCES", result.stderr)

if __name__ == "__main__":
    unittest.main()
