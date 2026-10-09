# Packages and releases

`search-clanker-history` is the only codebase. `search-claude-history` and `search-codex-history` are wrapper packages that `build_wrappers.py` generates from it at release time. A wrapper is metadata only: it depends on the same version of `search-clanker-history`, its `sch` and package-named commands start the same entry point, and it declares its default agent as an entry point in the `search_clanker_history.default_agents` group. The core reads that metadata (it imports nothing from it) to find the default scope, so an old release of `search-claude-history` that has no such entry point does not narrow anything. A saved `sch --default-agents` choice, kept in `~/.config/search-history/config.json`, overrides that default, and `--agent` overrides both for one search.

Every package declares `sch`, because `uv tool install X` exposes only the commands that X itself declares. When a wrapper and the core share an environment, both write `bin/sch`, and either script behaves the same because the scope comes from the installed metadata. The packages share that file, so removing one of them from an environment that holds another deletes `sch`, and the remaining package needs a reinstall. That is why the documentation tells users to install one package and change a setting instead of installing a second one.

```bash
uv --no-config run --locked python build_wrappers.py                  # both wrappers into dist-wrappers/
uv --no-config run --locked python build_wrappers.py out search-codex-history   # one wrapper into out/
cd dist-wrappers/search-codex-history && uv --no-config build --no-sources
```

To add a wrapper, add it to `WRAPPERS` in `build_wrappers.py`; the core needs no change. `tests/test_wrappers.py` generates the wrappers and checks that they are metadata only, and that the default scope follows the installed wrappers' metadata, the saved setting, and `--agent`.

## Publishing

A release tag publishes all three projects. `publish.yml` is the standard caller with one addition: after checkout it runs `build_wrappers.py`, then calls the shared `pypi-publish` action for the root package and once per wrapper with `working-directory: dist-wrappers/NAME`. The action verifies the tag against that package's version and the root release notes, builds it, and uploads with `--check-url`, so rerunning the workflow after a partial upload finishes the remaining packages and fails if a file with the same name differs. All three PyPI projects trust the `wakamex/search-clanker-history` repository, `publish.yml`, and the `pypi` environment. The uploads run core first, because the wrappers pin its exact version.

The first release creates three projects, and PyPI allows only one pending publisher per repository, workflow, and environment, so only one of them can have its pending publisher registered at the start. That release resumes with a rerun:
1. Register `search-clanker-history`'s pending publisher and add the new repository name to `search-claude-history`'s existing trusted publisher before tagging.
2. Tag. The core upload creates its project and the claude upload succeeds. The codex upload fails because no publisher exists for it yet.
3. Register the `search-codex-history` pending publisher for `wakamex/search-clanker-history`, `publish.yml`, `pypi`.
4. Re-run the failed job. `--check-url` skips the files already uploaded, so only codex uploads, and the GitHub Release job then runs.
5. Rerun `release.py publish`, which confirms all three versions from the existing tag.

Later releases need none of this. All three projects take the version in the root `pyproject.toml`. If a release is yanked, yank all three projects' files together: an exact `==` pin still selects a yanked core, so yanking only the core leaves wrapper users on it.
