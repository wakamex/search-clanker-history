# search-clanker-history

Search across your coding-agent session history for keywords or patterns. It covers:

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- [Codex](https://github.com/openai/codex)
- [OpenCode](https://github.com/anomalyco/opencode)
- [Hermes](https://github.com/NousResearch/hermes-agent)
- [Antigravity CLI](https://antigravity.google/) (`agy`)

In the examples, `sch` stands for the command, `search-clanker-history` (see Packages and commands below for a one-line alias).

```
$ sch "git-log-list"
── 2026-02-17 05:35 | claude | -code-rb | session:720d82ef ──
  [assistant] There it is — 114 commits, numbered and dated. Usage: `./scripts/git-log-list.sh`...

── 2026-02-23 20:15 | codex | -code-scripts | session:379bb145 ──
  [user] do you remember the motivation behind git-log-list.sh? search our session history

2 match(es)
```

Works out of the box, but fastest with [ripgrep](https://github.com/BurntSushi/ripgrep) installed, either on PATH or through the `ripgrep` extra.

## Install

```bash
# Run directly (no install needed)
uvx search-clanker-history "pattern"

# Or install globally
uv tool install search-clanker-history
```

Optional extras for faster searches:

```bash
# hyperscan matches ripgrep speed without ripgrep installed
uv tool install "search-clanker-history[hyperscan]"

# Or bundle ripgrep itself (Linux x86_64 and macOS arm64 wheels), for machines without rg
uv tool install "search-clanker-history[ripgrep]"
```

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). No system Python needed.

### Packages and commands

`search-clanker-history` is the tool, and its command is `search-clanker-history`. `search-claude-history` and `search-codex-history` install the same tool with a different default, each under its own command: `search-claude-history` searches only Claude Code unless you say otherwise, and `search-codex-history` only Codex. Every command name is unique to its package, so any mix of the three can be installed side by side. The examples below write `sch` for the command; make it a shell alias for the one you installed, for example `alias sch=search-clanker-history`.

Change what a plain search covers at any time, with no reinstall:

```bash
sch --default-agents                 # show the current default and where it is saved
sch --default-agents claude codex    # search Claude Code and Codex
sch --default-agents all             # search every agent that has history
sch --default-agents reset           # back to the package's own default
```

The choice is saved in `~/.config/search-history/config.json` (under `$XDG_CONFIG_HOME` when that is set, and in `%APPDATA%\search-history\config.json` on Windows) and shared by every package. `--agent` overrides it for a single search. When the default hides history from other agents, the command says so after a search that finds nothing, and `--version` shows which package is running and where the default comes from.

Already have `search-claude-history` from before? `uv tool upgrade search-claude-history` keeps searching only Claude Code, and `search-claude-history --default-agents all` adds the other agents. The short command `sch` is no longer installed, so use the long name or an alias. The `search_claude_history` Python module no longer contains code, since the code now lives in `search_clanker_history`.

## Usage

```
sch <pattern> [options]
```

| Flag | Description |
|------|-------------|
| `--agent AGENT` | Search one agent's history: `claude`, `codex`, `opencode`, `hermes`, or `antigravity`. Repeat the flag to search several. Overrides the default agents for this search |
| `--file PATH` | Search a chat log instead of agent history (repeatable). See Chat logs below |
| `--default-agents [AGENT ...]` | Show or save the agents searched by default (see Packages) |
| `-p`, `--project` | Filter to a project (substring match on dir name) |
| `-s`, `--session ID` | Only the session whose id starts with ID, such as the 8 characters a result header shows |
| `-t`, `--type` | Filter by role: `user` or `assistant` |
| `-A N` | Show N messages after each match. Tool messages count only with `--tools`, and reasoning only with `--thinking`, and with `-A` or `-B` the matching message is marked `>` |
| `-B N` | Show N messages before each match |
| `-C`, `--context` | Chars of text shown on each side of the match (default 200); `0` shows whole messages |
| `-i`, `--ignore-case` | Accepted for grep users; matching is always case-insensitive |
| `--since TIME` | Only messages at/after TIME (ISO 8601 or relative: `30m`, `2h`, `1d`, `1w`). A time without an offset is local time, the same clock result headers show |
| `--until TIME` | Only messages at/before TIME (same formats as `--since`) |
| `-n`, `--limit N` | Only the N most recent matches (printed oldest first). Scans newest files first and stops as soon as N are found |
| `--tools` | Include tool_use/tool_result messages (hidden by default) |
| `--thinking` | Include the agents' reasoning, shown as `[thinking]`, where their history keeps it as text (hidden by default) |
| `--no-color` | Disable colored output |

### Examples

```bash
sch "database migration"                  # search everything
sch "TypeError" --type assistant          # only assistant messages
sch "deployment" -p myproject             # filter to a project
sch "refactor" -B 1 -A 1                 # show surrounding messages
sch "SELECT.*FROM" --tools               # search tool calls too
sch "retry" --thinking                   # search the agents' reasoning too
sch "deploy" --agent codex                # only Codex sessions
sch "deploy" -n 5 -B 1                    # the 5 most recent matches, fast
sch "deploy" --since 1w                   # only the last week
sch "error" --since 2026-04-01 --until 2026-04-10
```

## Supported agents

| Agent | History location | Override | Project shown |
|-------|------------------|----------|---------------|
| Claude Code | `~/.claude/projects/**/*.jsonl` | `CLAUDE_CONFIG_DIR` | Claude's project directory name |
| Codex | `~/.codex/sessions` and `~/.codex/archived_sessions` rollout files | `CODEX_HOME` | Session working directory, with `/` replaced by `-` |
| OpenCode | `~/.local/share/opencode/opencode.db` (SQLite) | `XDG_DATA_HOME` | Session directory, with `/` replaced by `-` |
| Hermes | `~/.hermes/state.db` (SQLite) | `HERMES_HOME` | Session working directory, or its source such as `cli` |
| Antigravity CLI | `~/.gemini/antigravity-cli/conversations/*.db`, one SQLite database per conversation | none | Conversation workspace, with `/` replaced by `-` |

Every agent is reduced to user messages, assistant messages, and tool calls and results. Tool entries are hidden unless `--tools` is given, and reasoning unless `--thinking` is given. Codex developer messages and the AGENTS.md and environment context that Codex injects as user messages are not searched.

Databases are opened read-only.

Reasoning is searchable only where the history keeps it as text. Claude Code thinking blocks are text unless the model returned them redacted. Codex stores reasoning encrypted and keeps a readable summary only for some models, so most Codex reasoning cannot be searched. OpenCode reasoning parts, the Hermes `reasoning` column, and Antigravity thoughts are text.

Antigravity stores each step of a conversation as a [Protocol Buffers](https://protobuf.dev/) message with no published schema. The tool reads the user prompts, replies, thoughts, and tool calls from the fields that hold them in current `agy` releases, and treats every other step, such as tool output and notices, as a tool entry. A search that includes Antigravity tool output with `--tools` decodes every step of each matching conversation, which takes several seconds on a large history.

## Chat logs

`--file PATH` searches a chat log, such as an exported group chat, instead of agent history. Two formats are read:

- A `.jsonl` file with one JSON object per line, holding `sent` (ISO 8601 time), `sender`, and `text`. Other keys are ignored.
- Any other file, with one message per line as `YYYY-MM-DD HH:MM SENDER: TEXT`. Line breaks inside a message may be written as `⏎`, and a line without the date prefix continues the message before it.

Each message is searched and printed as `SENDER: TEXT`, so `^NAME:` matches the messages of one sender. `-C`, `-A`, `-B`, `-n`, `--since`, and `--until` work as for agent history, and the header names the file and the message's line, for example `── 2026-03-01 09:00 | chat.txt:1 ──`. Times without an offset are read as local time.

```sh
sch "^Ann Lee:.*deadline" --file chat.txt -B 1 -A 1
```

## Performance

Searches use the fastest available engine:

| Engine | 0.45 GB | Requires |
|--------|---------|----------|
| [ripgrep](https://github.com/BurntSushi/ripgrep) | 0.13s | `rg` on PATH |
| [hyperscan](https://pypi.org/project/hyperscan/) | 0.15s | install with `[hyperscan]` extra |
| stdlib (mmap + multiprocessing) | 0.64s | nothing |

The tool uses whichever engine is available, in the order above. 

Tool output is the bulk of agent history, so ripgrep's matching lines are streamed and tool-only lines are dropped by a byte check before they are decoded or parsed. `--since` skips files last modified before it, `-n` stops reading older files once enough matches are found, and `-A`/`-B` read only the lines around each match. On 15 GB of history a common word takes about 3 seconds on a quiet machine, `--since 1d` takes under a second, and `-n 20` takes about a second. Searching 13 GB of Codex history for one word, the median of 5 alternating pairs was 4.8 seconds for the search and 16.7 seconds for `grep -rli`, which only lists file names; `rg -li` took 4.0 seconds and also lists only file names. See [performance.md](https://github.com/wakamex/search-clanker-history/blob/main/performance.md) for the measurements and the ideas considered.

The SQLite histories are filtered inside the database and then matched in Python.

## Claude Code Skill

This tool can also be installed as a [Claude Code skill](https://docs.anthropic.com/en/docs/claude-code/skills) to help Claude automatically search conversation history when you reference past work.

### Install as Skill

```bash
mkdir -p ~/.claude/skills/search-history
curl -o ~/.claude/skills/search-history/SKILL.md https://raw.githubusercontent.com/wakamex/search-clanker-history/main/skill/SKILL.md
```

Or manually download [`SKILL.md`](https://github.com/wakamex/search-clanker-history/blob/main/skill/SKILL.md) into `~/.claude/skills/search-history/`.

**Note:** The skill works with or without installing the tool. If the command is not installed, Claude will use `uvx search-clanker-history` (requires [uv](https://docs.astral.sh/uv/)).

### How It Works

Once installed, Claude will automatically use the skill when you:
- Reference past work ("a few days ago", "last week", "previously")
- Ask about prior conversations or decisions
- Want to find when something was discussed
- Need to recall specific commands or errors from earlier sessions

The skill teaches Claude how to construct effective `sch` queries with appropriate patterns, context flags, and filtering options.
