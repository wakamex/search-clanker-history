import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_wrappers", ROOT / "build_wrappers.py")
build_wrappers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_wrappers)

CORE_SRC = ROOT / "src"
SID = "01a10334-5465-7210-a943-b674c6eea5b6"


def write_histories(home):
    claude = home / ".claude" / "projects" / "-code-demo"
    claude.mkdir(parents=True)
    (claude / "0123abcd-0000-0000-0000-000000000000.jsonl").write_text(json.dumps({
        "type": "user", "timestamp": "2026-01-02T03:04:05Z",
        "message": {"role": "user", "content": "xyzzy from claude"},
    }) + "\n")
    codex = home / ".codex" / "sessions" / "2026" / "01" / "02"
    codex.mkdir(parents=True)
    (codex / f"rollout-2026-01-02T03-04-04-{SID}.jsonl").write_text("\n".join(json.dumps(x) for x in (
        {"timestamp": "2026-01-02T03:04:04Z", "type": "session_meta", "payload": {"id": SID, "cwd": "/code/demo"}},
        {"timestamp": "2026-01-02T03:04:05Z", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "xyzzy from codex"}]}},
    )) + "\n")


class WrapperBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name) / "wrappers"
        for name in build_wrappers.WRAPPERS:
            build_wrappers.build(name, cls.out)
        cls.version = build_wrappers.core_version()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_wrapper_metadata_pins_the_core_package_and_installs_sch(self):
        pyproject = (self.out / "search-codex-history" / "pyproject.toml").read_text()
        self.assertIn('name = "search-codex-history"', pyproject)
        self.assertIn(f'dependencies = ["search-clanker-history=={self.version}"]', pyproject)
        self.assertIn(f'ripgrep = ["search-clanker-history[ripgrep]=={self.version}"]', pyproject)
        self.assertIn('search-codex-history = "_search_clanker_history_entry:main"', pyproject)
        self.assertNotIn("sch =", pyproject)
        for name in build_wrappers.WRAPPERS:
            self.assertTrue((self.out / name / "LICENSE").is_file())

    def test_wrappers_are_metadata_only_and_start_the_core_entry_point(self):
        for name, spec in build_wrappers.WRAPPERS.items():
            files = sorted(p.name for p in (self.out / name).rglob("*") if p.is_file())
            self.assertEqual(files, ["LICENSE", "README.md", "pyproject.toml"])
            pyproject = (self.out / name / "pyproject.toml").read_text()
            self.assertIn(f'{name} = "_search_clanker_history_entry:main"', pyproject)
            self.assertNotIn("sch =", pyproject)
            self.assertIn(f'{spec["agent"]} = "search_clanker_history"', pyproject)
            self.assertIn("bypass-selection = true", pyproject)

    def run_core(self, *args, home, config, wrappers=(), env_extra=None):
        """Run the core package with the given wrapper packages 'installed' (metadata only).

        A name ending in "!" is a distribution of that name with no default-agent entry point, like an
        old release of search-claude-history.
        """
        site = Path(home).parent / f"site-{'-'.join(wrappers) or 'none'}".replace("!", "-old")
        site.mkdir(exist_ok=True)
        for name in wrappers:
            plain = name.rstrip("!")
            info = site / f"{plain.replace('-', '_')}-{self.version}.dist-info"
            info.mkdir(exist_ok=True)
            (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {plain}\nVersion: {self.version}\n")
            if not name.endswith("!"):
                agent = build_wrappers.WRAPPERS[plain]["agent"]
                (info / "entry_points.txt").write_text(f"[search_clanker_history.default_agents]\n{agent} = search_clanker_history\n")
        env = dict(os.environ, NO_COLOR="1", HOME=str(home), XDG_CONFIG_HOME=str(config),
                   PYTHONPATH=os.pathsep.join([str(CORE_SRC), str(site)]))
        for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME", "XDG_DATA_HOME"):
            env.pop(key, None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-S", "-m", "search_clanker_history", *args],
                              capture_output=True, text=True, env=env, check=False)

    def test_each_wrapper_sets_the_default_until_the_user_changes_it(self):
        with tempfile.TemporaryDirectory() as parent:
            home, config = Path(parent) / "home", Path(parent) / "config"
            home.mkdir()
            write_histories(home)
            run = lambda *a, wrappers=(): self.run_core(*a, home=home, config=config, wrappers=wrappers)  # noqa: E731
            claude, codex = ("search-claude-history",), ("search-codex-history",)

            plain_claude = run("xyzzy", wrappers=claude)
            plain_codex = run("xyzzy", wrappers=codex)
            override = run("xyzzy", "--agent", "codex", wrappers=claude)
            version = run("--version", wrappers=codex)
            shown = run("--default-agents", wrappers=claude)
            widened = run("--default-agents", "claude", "codex", wrappers=claude)
            both = run("xyzzy", wrappers=claude)
            both_from_codex = run("xyzzy", wrappers=codex)
            reset = run("--default-agents", "reset", wrappers=claude)
            narrow_again = run("xyzzy", wrappers=claude)
            both_wrappers = run("xyzzy", wrappers=claude + codex)

        self.assertIn("xyzzy from claude", plain_claude.stdout)
        self.assertNotIn("xyzzy from codex", plain_claude.stdout)
        self.assertIn("xyzzy from codex", plain_codex.stdout)
        self.assertNotIn("xyzzy from claude", plain_codex.stdout)
        self.assertIn("xyzzy from codex", override.stdout)
        self.assertNotIn("xyzzy from claude", override.stdout)
        self.assertIn("(search-codex-history); default agents: Codex (package default)", version.stdout)
        self.assertIn("default agents: claude (package default)", shown.stdout)
        self.assertIn("default agents: claude, codex (config file)", widened.stdout)
        for result in (both, both_from_codex):
            self.assertIn("xyzzy from claude", result.stdout)
            self.assertIn("xyzzy from codex", result.stdout)
        self.assertIn("default agents: claude (package default)", reset.stdout)
        self.assertNotIn("xyzzy from codex", narrow_again.stdout)
        self.assertIn("2 match(es)", both_wrappers.stdout)

    def scenario(self, test):
        with tempfile.TemporaryDirectory() as parent:
            home, config = Path(parent) / "home", Path(parent) / "config"
            home.mkdir()
            write_histories(home)
            test(home, config, lambda *a, **kw: self.run_core(*a, home=home, config=config, **kw))

    def test_default_agents_cannot_be_combined_with_a_search_or_abbreviated(self):
        def check(home, config, run):
            with_pattern = run("xyzzy", "--default-agents", "codex")
            with_agent = run("--agent", "codex", "--default-agents")
            abbreviated = run("--default", "codex")
            nothing_saved = run("--default-agents")
            duplicates = run("--default-agents", "codex", "codex")
            self.assertEqual(with_pattern.returncode, 2)
            self.assertIn("cannot be combined with a search", with_pattern.stderr)
            self.assertEqual(with_agent.returncode, 2)
            self.assertNotEqual(abbreviated.returncode, 0)
            self.assertIn("built-in default", nothing_saved.stdout)
            self.assertIn("settings file:", nothing_saved.stdout)
            self.assertIn("default agents: codex (config file)", duplicates.stdout)
        self.scenario(check)

    def test_bad_saved_settings_are_reported_but_never_stop_a_search(self):
        def check(home, config, run):
            (config / "search-history").mkdir(parents=True)
            path = config / "search-history" / "config.json"
            for text, warning in (
                ("{not json", "ignoring"),
                ('{"agents": "codex"}', "expected a non-empty list"),
                ('{"agents": []}', "expected a non-empty list"),
                ('["codex"]', "expected a JSON object"),
            ):
                path.write_text(text)
                result = run("xyzzy")
                self.assertIn(warning, result.stderr, text)
                self.assertIn("2 match(es)", result.stdout, text)
            path.write_text('{"agents": ["Claude", "codex"]}')
            unknown = run("xyzzy")
            self.assertIn("unknown agents", unknown.stderr)
            self.assertIn("Claude", unknown.stderr)
            self.assertIn("xyzzy from codex", unknown.stdout)
            self.assertNotIn("xyzzy from claude", unknown.stdout)
        self.scenario(check)

    def test_saving_keeps_other_settings_and_reports_an_unwritable_folder(self):
        def check(home, config, run):
            (config / "search-history").mkdir(parents=True)
            path = config / "search-history" / "config.json"
            path.write_text('{"future": 1}')
            run("--default-agents", "codex")
            self.assertEqual(json.loads(path.read_text()), {"future": 1, "agents": ["codex"]})
            run("--default-agents", "reset")
            self.assertEqual(json.loads(path.read_text()), {"future": 1})
            path.unlink()
            run("--default-agents", "claude")
            run("--default-agents", "reset")
            self.assertFalse(path.exists())
            folder = config / "search-history"
            folder.chmod(0o500)
            try:
                blocked = run("--default-agents", "codex")
            finally:
                folder.chmod(0o700)
            if os.geteuid() != 0:
                self.assertEqual(blocked.returncode, 1)
                self.assertIn("cannot save the default agents", blocked.stderr)
        self.scenario(check)

    def test_an_empty_xdg_config_home_means_the_default_location(self):
        def check(home, config, run):
            result = run("--default-agents", "codex", env_extra={"XDG_CONFIG_HOME": ""})
            self.assertIn(str(home / ".config" / "search-history" / "config.json"), result.stdout)
            self.assertTrue((home / ".config" / "search-history" / "config.json").is_file())
        self.scenario(check)

    def test_an_old_release_without_the_entry_point_does_not_narrow_the_default(self):
        def check(home, config, run):
            result = run("xyzzy", wrappers=("search-claude-history!",))
            self.assertIn("2 match(es)", result.stdout)
        self.scenario(check)

    def test_a_narrowed_default_says_what_it_did_not_search(self):
        def check(home, config, run):
            no_match = run("zzzznothing", wrappers=("search-claude-history",))
            self.assertIn("No matches found.", no_match.stdout)
            self.assertIn("not searched: Codex", no_match.stderr)
            self.assertIn("default agents come from the package default", no_match.stderr)
            explicit = run("zzzznothing", "--agent", "claude", wrappers=("search-claude-history",))
            self.assertNotIn("not searched", explicit.stderr)
            run("--default-agents", "hermes", wrappers=("search-claude-history",))
            missing = run("xyzzy", wrappers=("search-claude-history",))
            self.assertEqual(missing.returncode, 1)
            self.assertIn("no hermes history found", missing.stderr)
            self.assertIn("not searched: Claude Code, Codex", missing.stderr)
        self.scenario(check)

    def test_core_package_searches_every_agent_with_history_by_default(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as config:
            home, config = Path(home), Path(config)
            write_histories(home)
            env = dict(os.environ, NO_COLOR="1", HOME=str(home), XDG_CONFIG_HOME=str(config), PYTHONPATH=str(CORE_SRC))
            for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME", "XDG_DATA_HOME"):
                env.pop(key, None)

            def run(*args):
                return subprocess.run([sys.executable, "-S", "-m", "search_clanker_history", *args],
                                      capture_output=True, text=True, env=env, check=False)

            everything = run("xyzzy")
            only_codex = run("--default-agents", "codex")
            codex_search = run("xyzzy")
            missing = run("--default-agents", "hermes")
            hermes_search = run("xyzzy")
            bad = run("--default-agents", "all", "claude")
            (config / "search-history" / "config.json").write_text("{not json")
            broken = run("xyzzy")

        self.assertIn("2 match(es)", everything.stdout)
        self.assertIn("default agents: codex (config file)", only_codex.stdout)
        self.assertIn("xyzzy from codex", codex_search.stdout)
        self.assertNotIn("xyzzy from claude", codex_search.stdout)
        self.assertIn("default agents: hermes (config file)", missing.stdout)
        self.assertEqual(hermes_search.returncode, 1)
        self.assertIn("no hermes history found", hermes_search.stderr)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("2 match(es)", broken.stdout)


if __name__ == "__main__":
    unittest.main()
