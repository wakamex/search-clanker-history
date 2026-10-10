# Packages and releases

`search-clanker-history` is the only codebase. `search-claude-history` and `search-codex-history` are wrapper packages that `build_wrappers.py` generates from it at release time. A wrapper is metadata only: it depends on the same version of `search-clanker-history`, installs one command named after itself that starts the same entry point, and declares its default agent as an entry point in the `search_clanker_history.default_agents` group. The core reads that metadata (it imports nothing from it) to find the default scope, so an old release of `search-claude-history` that has no such entry point does not narrow anything. A saved `--default-agents` choice, kept in `~/.config/search-history/config.json`, overrides that default, and `--agent` overrides both for one search.

No package installs the short command `sch`: each package has exactly one command, and its name is unique, so any mix of the packages can be installed together, as separate tools or in one environment, and removing one package never removes a file another package owns. Users who want `sch` make it an alias. If two of the packages share an environment, the default follows the installed wrappers' metadata, whichever command runs.

```bash
uv --no-config run --locked python build_wrappers.py                  # both wrappers into dist-wrappers/
uv --no-config run --locked python build_wrappers.py out search-codex-history   # one wrapper into out/
cd dist-wrappers/search-codex-history && uv --no-config build --no-sources
```

To add a wrapper, add it to `WRAPPERS` in `build_wrappers.py`; the core needs no change. `tests/test_wrappers.py` generates the wrappers and checks that they are metadata only, and that the default scope follows the installed wrappers' metadata, the saved setting, and `--agent`.

## Publishing

A release tag publishes all three projects. `publish.yml` is the standard caller with one addition: after checkout it runs `build_wrappers.py`, then calls the shared `pypi-publish` action for the root package and once per wrapper with `working-directory: dist-wrappers/NAME`. The action verifies the tag against that package's version and the root release notes, builds it, and uploads with `--check-url`, so rerunning the workflow after a partial upload finishes the remaining packages and fails if a file with the same name differs. All three PyPI projects trust the `wakamex/search-clanker-history` repository, `publish.yml`, and the `pypi` environment. The uploads run core first, because the wrappers pin its exact version.

A rerun of the failed job after a partial upload rebuilds every package, and a rebuilt file that differs from the uploaded one fails the job, so `build-system` pins `hatchling` to an exact version (in `pyproject.toml` and in the wrapper template in `build_wrappers.py`). With the same inputs two builds are byte-identical, so a rerun can come hours or days later. Bump the pin on purpose, and not between a run and its rerun.

All three projects take the version in the root `pyproject.toml`. If a release is yanked, yank all three projects' files together: an exact `==` pin still selects a yanked core, so yanking only the core leaves wrapper users on it.
