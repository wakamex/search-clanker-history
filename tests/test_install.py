import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_wrappers", ROOT / "build_wrappers.py")
build_wrappers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_wrappers)


@unittest.skipUnless(shutil.which("uv"), "uv is needed to build and install the packages")
class InstalledPackageTests(unittest.TestCase):
    """Build the real wheels, install them with uv, and run the installed commands."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.wheels = root / "wheels"
        cls.home = root / "home"
        cls.home.mkdir()
        cls.build(ROOT)
        wrappers = root / "wrappers"
        for name in build_wrappers.WRAPPERS:
            cls.build(build_wrappers.build(name, wrappers))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def build(cls, source):
        subprocess.run(["uv", "--no-config", "build", "--no-sources", "-q", "--out-dir", str(cls.wheels)],
                       cwd=source, check=True, capture_output=True)

    def venv(self, name):
        path = Path(self.temp.name) / name
        subprocess.run(["uv", "venv", "-q", str(path)], check=True, capture_output=True)
        return path

    def install(self, venv, *packages, reinstall=False):
        command = ["uv", "pip", "install", "-q", "--python", str(venv / "bin" / "python"),
                   "--no-index", "--find-links", str(self.wheels)]
        subprocess.run(command + (["--reinstall"] if reinstall else []) + list(packages), check=True, capture_output=True)

    def run_command(self, venv, command, *args):
        env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / "config"), NO_COLOR="1")
        return subprocess.run([str(venv / "bin" / command), *args], capture_output=True, text=True, env=env, check=False)

    def test_each_wrapper_installs_sch_with_its_default_agents(self):
        for package, scope in (("search-claude-history", "Claude Code"), ("search-codex-history", "Codex")):
            with self.subTest(package=package):
                venv = self.venv(f"venv-{package}")
                self.install(venv, package)
                for command in ("sch", package):
                    result = self.run_command(venv, command, "--version")
                    self.assertIn(f"({package}); default agents: {scope} (package default)", result.stdout)

    def test_the_core_package_searches_every_agent_by_default(self):
        venv = self.venv("venv-core")
        self.install(venv, "search-clanker-history")
        result = self.run_command(venv, "sch", "--version")
        self.assertIn("(search-clanker-history); default agents: every agent with history", result.stdout)

    def test_removing_a_wrapper_leaves_the_core_command_and_a_reinstall_restores_sch(self):
        venv = self.venv("venv-removal")
        self.install(venv, "search-claude-history")
        subprocess.run(["uv", "pip", "uninstall", "-q", "--python", str(venv / "bin" / "python"), "search-claude-history"],
                       check=True, capture_output=True)
        core = self.run_command(venv, "search-clanker-history", "--version")
        self.assertIn("default agents: every agent with history", core.stdout)
        self.install(venv, "search-clanker-history", reinstall=True)
        self.assertTrue((venv / "bin" / "sch").exists())
        self.assertIn("sch", self.run_command(venv, "sch", "--version").stdout)


if __name__ == "__main__":
    unittest.main()
