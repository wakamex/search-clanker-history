# Performance ideas, ranked

Searches over this host's history (about 15 GB: 1.7 GB of Claude Code, 13 GB of Codex rollouts, small OpenCode and Hermes databases) were slow for common patterns. The search for `deadline` took 13.4 seconds and gained nothing from `--since`.

Measuring showed the time was not in ripgrep. ripgrep scans all 15 GB in about 3 seconds. The cost was Python handling about 1 GB of matching lines, mostly Codex and Claude tool results: the old code buffered all of ripgrep's output, decoded it, and parsed every line as JSON even though tool entries are hidden by default. Context lines (`-A`/`-B`) re-read each file from the start for every hit.

The scores below rate each idea out of 100 by expected benefit for how people use `sch` (recent history, common words, occasional context) against its complexity and risk.

| Score | Idea | Status |
| --- | --- | --- |
| 95 | Stream ripgrep output as bytes and drop tool-result and non-message lines with a byte check before decoding or parsing | Done |
| 90 | Give ripgrep only files modified since `--since`, so untouched files are never scanned | Done |
| 80 | Add `-n N`: scan files newest first in batches, order matches newest first within a file, and stop once N are found | Done |
| 70 | Fetch context lines by seeking to the match's byte offset (forward for `-A`, backward in 64 KB chunks for `-B`) instead of re-reading the file from the start | Done |
| 45 | Skip the project filter's non-matching files before scanning (Claude: from the path; Codex: from the cached working directory) | Done |
| 40 | Keep a persistent full-text index of all history | Not done: it needs invalidation as sessions grow, doubles the disk footprint, and the remaining cost is bounded below by ripgrep's 3 second scan |
| 30 | Skip lines above a size limit in ripgrep with `--max-columns` | Not done: it would silently drop very long user messages and cut the remaining 1 GB transfer only for tool-heavy queries |
| 25 | Remember per-file results between runs keyed by size and mtime | Not done: complexity close to the index with a smaller gain |
| 15 | Parallelize the SQLite agents | Not done: both databases are a few megabytes |
| 10 | Avoid scanning `archived_sessions` for Codex | Not done: they are 3% of the data |

## Measured result

Timings on this host, seconds, same query before and after:

| Command | Before | After |
| --- | --- | --- |
| `sch deadline` | 13.4 | 5.4 |
| `sch deadline --since 1d` | 12.1 | 0.7 |
| `sch deadline -n 20` | not available | 0.9 |
| `sch deadline -n 20 -A2 -B2` | not available | 1.0 |
| `sch deadline -A2 -B2 --since 7d` | about 13 | 1.3 |
| `sch frobnicate` (no matches) | 3.0 | 3.2 |

A search with no matches is bound by the ripgrep scan. Output for `deadline --since 5d` with context was compared with an unfiltered reference that reads context line by line: 55 Claude Code and 61 Codex hits, identical text, roles, timestamps, and context.

## Python bindings for ripgrep

Two PyPI packages were tried on the same search.

| Package | Finding |
| --- | --- |
| `ripgrep-python` (native binding) | Searched `~/.claude/projects` for `deadline` in 1.08 seconds against 0.07 seconds for the `rg` binary. It returns every match as one string in a list, takes a single path, and gives no byte offsets or per-line hook, so the streaming prefilter and seek-based context cannot use it. Not adopted. |
| `ripgrep` (bundles the `rg` binary) | Installs the real ripgrep 15.1.0 into the environment's `bin`. Wheels exist for Linux x86_64 (glibc 2.39 or newer) and macOS arm64 only. Adopted as the optional `ripgrep` extra; `sch` finds `rg` on PATH first and then beside the Python interpreter. |

## Prototype: persistent full-text index

`prototypes/fts_index.py` indexes user and assistant text from Claude Code and Codex in an SQLite [FTS5](https://www.sqlite.org/fts5.html) table with the `trigram` tokenizer, which matches case-insensitive substrings. Each file records how far it has been indexed, so a rebuild reads only appended bytes. Each message row stores its file and byte offset, so `-A`/`-B` context can reuse the existing seek-based reader.

Measured on the 15 GB of history on this host:

| Measure | Result |
| --- | --- |
| Full build, 1,664 files, 318,284 messages | 32 seconds on 32 cores |
| Index size | 520 MB, which includes the 124 MB of message text; 3.5% of the history it covers |
| Update after new messages arrived | 0.2 seconds (stat every file, read only appended bytes) |
| `deadline` | 0.009 seconds, 778 hits, against 5.4 seconds by scanning |
| `sleep 45` | 0.002 seconds, 6 hits |
| `cheapest existing counterfactual` | 0.026 seconds, 204 hits |
| `deadline`, newest 20 | 0.006 seconds |

The hit counts equal the scanning search's counts for all three queries.

Limits found while building it:

- Patterns need at least 3 characters, and only literal text is indexed. Regular expressions, shorter patterns, and `--tools` would use the scanning path, or the index could pre-filter candidates using a required literal extracted from the regex.
- `detail=none` makes the index smaller but rejects multi-word phrases, so full detail is required.
- A file shorter than its recorded position (rewritten or truncated) must be reindexed from the start, and a Codex rollout moved from `sessions` to `archived_sessions` must be matched by session id or its messages are indexed twice.
- The trigram tokenizer needs SQLite 3.34 or newer built with FTS5. Older builds fall back to scanning.

Fit: a 500 times speedup for the common queries at a small disk cost, and the index can be rebuilt from the history at any time. The scan path stays as the fallback and as the reference for correctness.

## DuckDB and Polars

[DuckDB](https://duckdb.org/) 1.5.6 and [Polars](https://pola.rs/) 1.44.2 were tried in two roles on the same history.

Scanning the raw JSONL files, reading each line as one string column and filtering with a case-insensitive regular expression for `deadline`:

| Scope | ripgrep | DuckDB | Polars |
| --- | --- | --- | --- |
| Claude Code only (1.7 GB) | 0.33 s, 821 lines | 0.56 s, 824 lines | 0.61 s, 825 lines |
| All history (15 GB) | 2.8 s, 17,381 lines | 6.2 s, 17,338 lines | 13.0 s, 17,381 lines |

Neither beats ripgrep as a scanner. DuckDB needed `max_line_size` capped at 8 MB with `ignore_errors` and fewer threads to avoid a 16 GB buffer allocation, which drops the 43 lines longer than that. Polars returned the right count but took 4.6 times as long.

As the store for an index, DuckDB holds only the user and assistant text (318,284 messages, 124 MB of text) and scans it as a column, with no trigram index:

| Query over the indexed messages | DuckDB column scan | SQLite FTS5 trigram | SQLite table scan |
| --- | --- | --- | --- |
| `deadline`, literal | 0.088 s | 0.009 s | 0.28 s (`LIKE`) |
| `deadline\|timeout`, regular expression | 0.107 s | not supported | 4.3 s (Python callback) |
| `sleep \d+`, regular expression | 0.037 s | not supported | 4.4 s |
| `ok`, two characters | 0.081 s | not supported | 0.63 s |
| no match | 0.072 s | 0.002 s | not measured |

Hit counts match the scanning search (778 for `deadline`). The DuckDB file is 191 MB against 520 MB for the FTS5 index. Its regex dialect (RE2) is the same family as ripgrep's, so patterns behave as they do today.

Tradeoffs: DuckDB covers every query except `--tools` in about 0.1 seconds, including regular expressions and short patterns that the trigram index cannot answer, and it is smaller on disk. FTS5 is faster for plain literals, needs no new dependency, and ships with Python. DuckDB adds a wheel of tens of megabytes and permits only one writer, so two `sch` runs refreshing the index at once need a lock or a retry.

## Decision: no persistent index for now

Searches stay on the scanning path. A persistent index (SQLite FTS5 or DuckDB) would cut a common search from 5.4 seconds to roughly 0.25 seconds end to end, but it adds a second copy of every message, staleness and locking cases, and a first-run build. Revisit it if unbounded searches over the whole history become routine. The prototype in `prototypes/fts_index.py` and the measurements above are the starting point; the DuckDB variant answers regular expressions and short patterns that the FTS5 variant cannot.

## Anchored line prefix for Codex

About 87% of the history is Codex rollouts, and most of their bytes are tool output. Codex message lines begin `{"timestamp":...,"type":"response_item","payload":{"type":"message"`, so for Codex searches without `--tools`, `sch` asks ripgrep for the user pattern only on lines that start that way. An anchored regex like this stays on ripgrep's fast path, unlike a lookahead (`rg -P` took 5.9 seconds). ripgrep then returns about 1.5 MB instead of 1 GB for `deadline` on Codex, and Python no longer reads the tool lines.

| Command | Before | After |
| --- | --- | --- |
| `sch deadline` (all agents) | 5.6 s | 3.0 s |
| ripgrep alone, Codex only | 4.45 s | 2.8 s |

The anchored regex returned every message line the parser accepts (0 missing) plus 18 lines it drops afterwards, such as developer messages. Output for `deadline --since 5d` with context still matches the unfiltered reference (61 Claude Code and 61 Codex hits). The prefix tolerates whitespace around the JSON separators and an optional `ordinal` field. A pattern that only Python's `re` accepts, such as a lookahead, makes ripgrep exit with a usage error; `sch` then falls back to the pure-Python scanner. That fallback is correct but slow on this history (about 69 seconds for 15 GB), so it is a safety net and not a fast path.

Claude Code lines keep the byte check in Python. Their key order varies, so no single anchored prefix covers them, and they are 12% of the history.
