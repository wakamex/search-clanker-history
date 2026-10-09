# Building the packages

The repository holds one source tree and builds one distribution per agent set. `build_variants.py` lists the packages and their command names. It copies the source, deletes every block marked for an agent the package does not include, rewrites names and metadata, and fails if a marker is malformed or a cut leaves invalid Python.

```bash
uv --no-config run --locked python build_variants.py            # all packages into dist-variants/
uv --no-config run --locked python build_variants.py out search-codex-history   # one package into out/
cd dist-variants/search-codex-history && uv --no-config build --no-sources
```

Mark agent-specific code with `# agents: claude codex` before and `# end agents` after a block (`<!-- agents: ... -->` in Markdown), or with a trailing `# agents: codex` on one line. A block is kept when the package includes any listed agent. The tags `multi` and `single` select text that depends on whether a package has several agents or one.

To add an agent, tag its code, add it to `AGENTS`, and add or extend a package in `VARIANTS`. `tests/test_build_variants.py` builds every package and checks that single-agent packages contain no other agent's code and search only their own history.

Each package registers its agents under the `search_history.agents` entry-point group, so `sch` searches the agents of every package installed in the same environment, whichever package's `sch` runs. `tests/test_build_variants.py` covers both install orders with generated metadata.

## Publishing

A release tag publishes all three packages. `publish.yml` is the standard caller with one addition: after checkout it runs `build_variants.py`, then calls the shared `pypi-publish` action once per generated package with `working-directory: dist-variants/NAME`. The action verifies the tag against that package's version and the root release notes, builds it, and uploads with `--check-url`, so rerunning the workflow after a partial upload finishes the remaining packages and fails if a file with the same name differs. All three PyPI projects trust the `wakamex/search-clanker-history` repository, `publish.yml`, and the `pypi` environment. The uploads run in alphabetical order, and codex is last.

The first release creates two projects, and PyPI allows only one pending publisher per repository, workflow, and environment, so `search-codex-history`'s pending publisher can be registered only after `search-clanker-history` exists. That release resumes with a rerun:
1. Add `search-claude-history`'s trusted publisher for this repository before tagging.
2. Tag. The clanker upload creates its project and the claude upload succeeds. The codex upload fails because no publisher exists for it yet.
3. Register the `search-codex-history` pending publisher for `wakamex/search-clanker-history`, `publish.yml`, `pypi`.
4. Re-run the failed job. `--check-url` skips the files already uploaded, so only codex uploads, and the GitHub Release job then runs.
5. Rerun `release.py publish`, which confirms all three versions from the existing tag.

Later releases need none of this.

All packages take the version in the root `pyproject.toml`, which is only the development template and is never published. Preview the upload without sending anything:

```bash
uv --no-config run --locked python build_variants.py dist-variants
for package in dist-variants/*/; do (cd "$package" && uv --no-config build --no-sources); done
for package in dist-variants/*/; do uv publish --dry-run --trusted-publishing never --check-url https://pypi.org/simple/ "$package"dist/*; done
```
