# Decisions: searching more than Claude Code history

Goal: make `sch` search the session history of the other agent harnesses that AOP ([/code/aop](/code/aop)) supports, where the on-disk format is known. AOP lists Codex, Claude Code, Cursor Agent, Devin CLI, OpenCode, Antigravity, Grok Build, Hermes, DeepSeek Harness, and Zcode. AOP delegates session storage to each harness and never parses transcripts, so the formats below come from inspecting the histories on this host.

## Result

`sch` now searches Claude Code, Codex, OpenCode, and Hermes. `--agent` limits a search to one or more of them; without it, every agent that has history on the machine is searched. Each result header names the agent.

## Coverage by harness

| Harness | Decision | Reason |
| --- | --- | --- |
| Claude Code | Kept as is | Existing behavior, same output apart from the agent name in the header |
| Codex | Added | JSONL rollout files under `sessions/` and `archived_sessions/`; 300+ sessions on this host exercise the parser |
| OpenCode | Added | SQLite `opencode.db` with `session`, `message`, and `part` tables; text and tool parts are searched |
| Hermes | Added | SQLite `state.db` with `sessions` and `messages` tables |
| Grok Build | Deferred | `chat_history.jsonl` exists, but the two sessions on this host contain only system and user entries, so the assistant and tool shapes are unverified |
| Devin CLI | Deferred | `sessions.db` is empty on this host and transcripts are exported as ATIF files; no sample to verify against |
| Cursor Agent | Deferred | Chats are `store.db` files; the content encoding was not inspected |
| Zcode | Deferred | Model I/O rollout files and a SQLite store exist, but no format description or sample message flow was checked |
| Antigravity | Deferred | Conversations are protobuf (`.pb`) files; no decoder is available |
| DeepSeek Harness | Deferred | No session files found under `~/.dsh` |

Deferred harnesses can be added as one adapter each once a real session with assistant replies is available to test against.

## Design choices

1. One source tree builds three packages with `build_variants.py`: `search-clanker-history` (every agent), `search-claude-history` (Claude Code only), and `search-codex-history` (Codex only). The build deletes the code, README text, and extras of the agents a package does not include, so the Claude Code wheel contains no Codex, OpenCode, Hermes, or SQLite code and the packages do not depend on each other. Agent-specific lines carry `# agents: NAME` markers (`<!-- agents: NAME -->` in Markdown); `multi` and `single` select text that depends on how many agents a package has. A malformed marker or a cut that leaves invalid Python fails the build.
   - Commands: every package ships `sch` plus its long name (`search-clanker-history`, `search-claude-history`, `search-codex-history`), so the packages mirror one tool. Because they are self-contained, any of them can be the one whose `sch` runs, and `sch --version` names it, for example `sch 0.1.3 (search-codex-history)`.
   - `sch` searches what is installed. Each package registers its agents under the `search_history.agents` entry-point group, and at startup `sch` adds the agents of every other installed package built from this source (its own agents win on a name clash, and a package whose agents fail to load is skipped with a warning). Installing `search-claude-history` and `search-codex-history` into one environment searches both, in either install order; installing `search-clanker-history` covers everything. No package depends on another, and the cost is an entry-point scan of about 10 ms at startup.
   - Package and import names, the `_DIST_NAME` constant, the README, and the metadata are rewritten per package. Repository URLs stay as they are in the template.
   - The repository's own `pyproject.toml` stays the development template (all agents, name `search-claude-history`). Releases are built from `dist-variants/`; the publish workflow is not changed here. Building and registering the extra PyPI projects, and renaming the main package, are left for the maintainer.
2. Everything stays in `cli.py`. The console entry point loads that file by path to avoid shadowing by similarly named scripts, so relative imports between modules would fail. One file is also what the marker-based build needs.
3. All agents are normalized into one hit record (agent, project, session, time, type, role, text, context). The display, role filter, `--tools`, `--since`/`--until`, and `-A`/`-B` code runs once for every agent.
4. JSONL histories (Claude Code, Codex) share the existing ripgrep, hyperscan, or mmap engine, now taking a list of roots. SQLite histories find candidate sessions with a case-insensitive regular expression function inside the database, then match the rendered message text in Python so that JSON field names never produce matches.
5. The default is to search every agent with history. This matches the prior behavior of searching everything, and `--agent` is the narrowing control. Naming an agent whose history is missing is an error; searching with no history anywhere is also an error.
6. Project names for Codex, OpenCode, and Hermes use the session working directory with `/` replaced by `-`, which is how Claude Code names project directories. Existing `-p` patterns therefore work across agents. Hermes sessions without a working directory show their source, such as `cli`.
7. Codex developer messages and injected user messages (the AGENTS.md block and the environment context) are skipped. They are present in every session and would match most searches without being anything the user or agent said.
8. Codex tool calls and outputs, OpenCode tool parts, and Hermes tool calls and results are classified as tool entries and hidden unless `--tools` is given, as for Claude Code.
9. History locations follow each harness's own environment variables: `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `XDG_DATA_HOME` for OpenCode, and `HERMES_HOME`. AOP's `AOP_*_SOURCE_HOME` variables are not used because they configure AOP, not the harness.
10. SQLite files are opened read-only. A database that cannot be read prints a warning and the search continues with the other agents.
11. Reasoning content is not searched for any agent, matching the existing behavior of skipping Claude thinking blocks.
12. Performance work is recorded in [performance.md](performance.md). Results are ordered oldest file first by default; `-n N` returns the N newest matches, still printed oldest first. Byte-level prefilters assume Claude and Codex write their type markers as `"type":"..."`, and tolerate one space after the colon.
13. No persistent index or cache. The measurements and the revisit trigger are in performance.md.
14. No version bump, tag, or release. Publishing is a separate decision.

## Verification

The unittest suite covers Claude Code, Codex (project from working directory, developer message and tool filtering, context lines), OpenCode, and Hermes with synthetic histories in a temporary home, and the missing-agent error. It passes. Manual runs against this host's real histories returned matches from all four agents.
