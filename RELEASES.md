# Packages and releases

`search-clanker-history` is the only codebase. `search-claude-history` and `search-codex-history` are wrapper packages that `build_wrappers.py` generates from it at release time. A wrapper contains no code: its metadata depends on the same version of `search-clanker-history`, and its `sch` and package-named commands start the same entry point. `search-clanker-history` knows which wrapper packages are installed (`_WRAPPER_AGENTS` in `cli.py`) and uses their agents as its default scope. A saved `sch --default-agents` choice, kept in `~/.config/search-history/config.json`, overrides that default, and `--agent` overrides both for one search. Installing two wrappers into one environment searches both agents.

The wrappers do not depend on the order of installation: when a wrapper and the core are installed together, both write `bin/sch`, and either script behaves the same because the scope comes from the installed wrapper metadata.

```bash
uv --no-config run --locked python build_wrappers.py                  # both wrappers into dist-wrappers/
uv --no-config run --locked python build_wrappers.py out search-codex-history   # one wrapper into out/
cd dist-wrappers/search-codex-history && uv --no-config build --no-sources
```

To add a wrapper, add it to `WRAPPERS` in `build_wrappers.py` and to `_WRAPPER_AGENTS` in `cli.py`. `tests/test_wrappers.py` generates the wrappers and checks their metadata, that they contain no code, and that the default scope follows the installed wrapper.

## Publishing

A release tag publishes all three projects. `publish.yml` is the standard caller with one addition: after checkout it runs `build_wrappers.py`, then calls the shared `pypi-publish` action for the root package and once per wrapper with `working-directory: dist-wrappers/NAME`. The action verifies the tag against that package's version and the root release notes, builds it, and uploads with `--check-url`, so rerunning the workflow after a partial upload finishes the remaining packages and fails if a file with the same name differs. All three PyPI projects trust the `wakamex/search-clanker-history` repository, `publish.yml`, and the `pypi` environment. The uploads run core first, because the wrappers pin its exact version.

The first release creates three projects, and PyPI allows only one pending publisher per repository, workflow, and environment, so only one of them can have its pending publisher registered at the start. That release resumes with a rerun:
1. Register `search-clanker-history`'s pending publisher and add the new repository name to `search-claude-history`'s existing trusted publisher before tagging.
2. Tag. The core upload creates its project and the claude upload succeeds. The codex upload fails because no publisher exists for it yet.
3. Register the `search-codex-history` pending publisher for `wakamex/search-clanker-history`, `publish.yml`, `pypi`.
4. Re-run the failed job. `--check-url` skips the files already uploaded, so only codex uploads, and the GitHub Release job then runs.
5. Rerun `release.py publish`, which confirms all three versions from the existing tag.

Later releases need none of this. All three projects take the version in the root `pyproject.toml`.
