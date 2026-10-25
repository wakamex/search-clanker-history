---
name: search-history
description: Search Claude Code, Codex, OpenCode, Hermes, Antigravity, Gemini CLI, Zcode, Grok Build, Devin CLI, DeepSeek Harness, Cursor and Muse Code conversation history with regex patterns and context. Use this skill whenever the user references past work ("a few days ago", "last week", "previously"), asks about prior conversations, wants to find when something was discussed, needs to recall specific commands/decisions/errors from earlier sessions, or mentions timing without specifics. Also use when investigating issues that may have been discussed before, or when the user asks "did we try X?" or "what was that thing we did?".
---

# Search Conversation History

Search across all Claude Code, Codex, OpenCode, Hermes, Antigravity, Gemini CLI, Zcode, Grok Build, Devin CLI, DeepSeek Harness, Cursor and Muse Code conversation history to find past discussions, commands, decisions, and context.

**Keywords**: history, past conversations, previous sessions, last week, few days ago, earlier, before, recall, find when, search logs, conversation search

## Overview

The `sch` command searches through the session history of every supported agent (Claude Code, Codex, OpenCode, Hermes, Antigravity, Gemini CLI, Zcode, Grok Build, Devin CLI, DeepSeek Harness, Cursor, Muse Code) on the machine using regex patterns. It's essential for maintaining context across sessions when users reference work from hours, days, or weeks ago.

Use this proactively when:
- User mentions timeframes without specifics ("last time", "earlier", "before")
- You need to verify what was previously tried or discussed
- User asks about past decisions, errors, or results
- Context from prior sessions would be helpful

## Installation

Before using this skill, check if the tool is available:

This skill writes `sch` for the command. The installed command is named after the package: `search-clanker-history`, or `search-claude-history` / `search-codex-history` for the packages that default to one agent. Many people alias it to `sch`. Check which one exists:

```bash
which sch search-clanker-history search-claude-history search-codex-history
```

**If installed:** Use whichever exists, in place of `sch` in the commands below.

**If not installed:** Use `uvx search-clanker-history` instead. All `sch` commands below can be run as:
```bash
uvx search-clanker-history [OPTIONS] PATTERN
```

This requires [uv](https://docs.astral.sh/uv/) to be installed. If `uv` is not available, ask the user to install it first:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Alternatively, suggest the user install globally:
```bash
uv tool install search-clanker-history
```

## Command Reference

```bash
sch [OPTIONS] PATTERN
```

### Key Options

- `--agent AGENT` — Limit to `claude`, `codex`, `opencode`, `hermes`, `antigravity`, `gemini`, `zcode`, `grok`, `devin`, `dsh`, `cursor`, or `muse` (repeatable; default: all with history)
- `-p PROJECT` — Filter to project (substring match on directory name)
- `-t {user,assistant}` — Filter by message role
- `-A N` — Show N messages after match
- `-B N` — Show N messages before match
- `-C CHARS` — Show CHARS of text on each side of the match (default 200); `-C 0` shows whole messages
- `--file PATH` — Search a chat log instead of agent history: `.jsonl` with `sent`, `sender`, `text`, or `YYYY-MM-DD HH:MM SENDER: TEXT` lines. Messages read as `SENDER: TEXT`, so `^NAME:` picks a sender
- `-n N` — Only the N most recent matches (fast; stops reading older history early)
- `--since TIME` — Only messages at/after TIME (ISO 8601 like `2026-04-01`, or relative: `30m`, `2h`, `1d`, `1w`); local time, the same clock result headers show
- `-P TEXT` — Leave out projects whose name contains TEXT (repeatable), for example scratch directories that crowd out real results
- `-s ID` — Only one session, by any prefix of its id such as the 8 characters a result header shows. To read everything after a message, use `--after`
- `--until TIME` — Only messages at/before TIME (same formats as `--since`)
- `--after PATTERN` — Start at the most recent message matching PATTERN and show everything from it on; the search pattern can be left out. Narrow the starting message with `-p`, `-s`, `--since`, `--until`
- `--json` — One JSON object per match with whole message texts, for scripts
- `--tools` — Include tool_use/tool_result messages (skipped by default)
- `--thinking` — Include the agents' reasoning, shown as `[thinking]`, where it is stored as text (skipped by default; most Codex reasoning is encrypted)
- `--no-color` — Disable colored output

### Pattern Syntax

Patterns use Python regex (via `ripgrep`):
- `.` matches any character
- `.*` matches zero or more characters
- `\w` matches word characters
- `|` for alternation (OR)
- `\` escapes special characters

## Common Use Cases

### 1. Find timing/duration from past runs

**User says:** "How long did the Qwen3.5-27B benchmark take?"

```bash
sch "Qwen3\.5-27B.*time|took|duration|seconds|finished" -p human3090 -A 3
```

### 2. Recall specific errors

**User says:** "What was that CUDA error from yesterday?"

```bash
sch "CUDA.*error|error.*CUDA" -p myproject -t assistant -A 5 -B 2
```

### 3. Find when something was discussed

**User says:** "When did we talk about testing?"

```bash
sch "test.*framework|testing.*approach" -p myproject -A 2 -B 2
```

### 4. Locate past commands/tool usage

**User says:** "What command did we use to deploy?"

```bash
sch "deploy|deployment" -p myproject --tools -A 3
```

### 5. Search across all projects

**User says:** "Did I ever use Redis anywhere?"

```bash
sch "redis|Redis" -A 2
```

### 6. Find conversations in time windows

**User says:** "What did we work on last Tuesday?"

Use `--since`/`--until` to bound the window — relative (`1w`, `3d`, `2h`) or ISO dates:

```bash
sch "benchmark|test|deploy" -p project --since 1w -A 5
sch "error" --since 2026-04-08 --until 2026-04-09   # one specific day
```

Prefer this over piping through `grep "<date>"`: the match timestamp drives the filter, so results stay correctly dated even when the rendered header is truncated.

## Best Practices

### Pattern Construction

- **Escape special regex characters**: Use `\.` for literal dots (e.g., `Qwen3\.5`)
- **Use alternation for variations**: `error|fail|crash` catches all
- **Combine terms flexibly**: `benchmark.*score|score.*benchmark` handles word order
- **Case sensitivity**: Matching is always case-insensitive; `-i` is accepted and changes nothing

### Context Window Tuning

- **Start with `-A 3 -B 2`** for most searches
- **Use `-A 10`** when looking for complete output (like benchmark results)
- **Use `-C 500`** for long error messages or config dumps, and `-C 0` to read whole messages
- **Add `--tools`** to see actual tool executions (file edits, bash commands)

### Filtering for Precision

- **Always use `-p PROJECT`** when scope is known — drastically reduces noise
- **Use `-t user`** to see only what the user said
- **Use `-t assistant`** to see only Claude's responses
- **Chain with `grep`** for secondary filtering: `sch "..." | grep "specific term"`

## Troubleshooting

### No matches found

- Remove escapes and try simpler pattern: `Qwen` instead of `Qwen3\.5-27B.*time`
- Check project name: `-p human` matches `human3090`, `-p my-app` matches `my-app-v2`
- Try without `-p` to search all projects
- Search for keywords instead of exact phrases

### Too many results

- Add `-p PROJECT` to narrow scope
- Use `-t user` or `-t assistant` to filter by role
- Make pattern more specific: `error.*timeout` instead of just `error`
- Pipe to `head -50` to see first 50 lines

### Broken pipe errors

Results are being piped through `less` or `head` which closes early. This is normal and harmless.

## Examples with Explanations

### Example 1: Find model benchmark scores

```bash
sch "DeepSeek-R1.*score|DeepSeek-R1.*percent" -p human3090 -A 3
```

**Why:** Searches for "DeepSeek-R1" followed by "score" or "percent", shows 3 lines after match to capture the actual numbers.

### Example 2: Recall git commands

```bash
sch "git.*commit|git.*push" -p myproject --tools -A 1
```

**Why:** Finds git operations, includes tool results to see actual commands executed, shows 1 line after for output.

### Example 3: Find error context

```bash
sch "ModuleNotFoundError|ImportError" -p myproject -A 5 -B 5
```

**Why:** Searches for Python import errors, shows 5 lines before/after to capture full stack trace and context.

### Example 4: When was feature discussed?

```bash
sch "authentication|auth.*system" -p webapp -A 10 -B 2
```

**Why:** Finds conversations about auth, shows more context after (where decisions often appear).

## Tips for Effective Searching

1. **Start broad, then narrow**: Begin with simple terms, add specificity if too many results
2. **Think about variations**: Search for `test|spec|eval` not just `test`
3. **Remember context matters**: Use `-A` and `-B` generously — Claude's responses often span multiple lines
4. **Tool calls hide content**: Add `--tools` if looking for actual commands/file contents
5. **Session dates in output**: Results show session date — scan for temporal context
6. **Regex is powerful**: Learn basic patterns like `.*`, `\w+`, `[0-9]+` for better searches

## Integration with Claude Workflows

When Claude uses this skill, typical flow:

1. **User mentions past work** → Claude detects temporal reference
2. **Claude constructs search** → Builds appropriate `sch` pattern with context flags
3. **Claude analyzes results** → Extracts relevant information from matches
4. **Claude reports back** → Summarizes findings in context of current conversation

Example:

```
User: "How long did that benchmark take last week?"
Claude: [searches with sch "benchmark.*time|took.*seconds" -A 5]
Claude: "The benchmark from Feb 25 took 5139.25s (about 1.4 hours)."
```
