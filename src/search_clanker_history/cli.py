"""Search the session history of coding agents for keywords or patterns in messages.

Which agents a plain search covers depends on the installed wrapper packages and the saved default; see RELEASES.md.
Matching uses rg when available, falling back to pure Python otherwise.

Usage:
    sch "git-log-list"
    sch "git-log-list" --project scripts
    sch "git-log-list" --type user
    sch "git-log-list" -B 2 -A 2
    sch "make_key" --tools
    sch "harness behavior" --thinking
"""

import argparse
import concurrent.futures
import functools
import itertools
import json
import mmap
import multiprocessing
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _env_path(name, default):
    """A path from an environment variable; an unset or empty variable means the default."""
    return Path(os.environ.get(name) or default)


def _claude_roots():
    return [_env_path("CLAUDE_CONFIG_DIR", Path.home() / ".claude") / "projects"]


def _codex_roots():
    home = _env_path("CODEX_HOME", Path.home() / ".codex")
    return [home / "sessions", home / "archived_sessions"]


def _opencode_db():
    return _env_path("XDG_DATA_HOME", Path.home() / ".local" / "share") / "opencode" / "opencode.db"


def _hermes_db():
    return _env_path("HERMES_HOME", Path.home() / ".hermes") / "state.db"


def _antigravity_dir():
    return Path.home() / ".gemini" / "antigravity-cli" / "conversations"


# Message kinds hidden unless asked for: --tools shows "tool", --thinking shows "thinking".
_OPTIONAL_KINDS = frozenset({"tool", "thinking"})


def _visible(kind, extra):
    return kind not in _OPTIONAL_KINDS or kind in extra


def _in_session(sid, session):
    """Whether session id sid is the one --session names, by any prefix such as the 8 characters headers show."""
    return session is None or (sid or "").lower().startswith(session.lower())


_DIST_NAME = "search-clanker-history"


def _get_version():
    try:
        from importlib.metadata import version
        return version(_DIST_NAME)
    except Exception:
        pass
    # Fallback: read version from pyproject.toml + git hash
    base = "0.0.0"
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            tomllib = None
    if tomllib:
        toml_path = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        try:
            with open(toml_path, "rb") as f:
                base = tomllib.load(f).get("project", {}).get("version", base)
        except OSError:
            pass
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).parent,
        )
        if out.returncode == 0:
            desc = out.stdout.strip()
            # On a release tag (v0.1.0 or 0.1.0) → clean version
            if desc in (base, f"v{base}"):
                return base
            # Ahead of tag or no tags → append short hash
            short = desc.rsplit("-", 1)[-1].lstrip("g") if "-" in desc else desc
            return f"{base}+{short}"
    except Exception:
        pass
    return base


# Whether to emit ANSI escapes — resolved at startup
_use_color = True


def _init_color(force_no_color=False):
    """Set up color support. Call once at startup."""
    global _use_color

    if force_no_color or os.environ.get("NO_COLOR"):
        _use_color = False
        return

    if not sys.stdout.isatty():
        _use_color = False
        return

    # Enable ANSI/VT processing on Windows 10+
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # STD_OUTPUT_HANDLE = -11, ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x4
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x4)
        except Exception:
            _use_color = False


def _sgr(code):
    """Return an ANSI SGR sequence if color is enabled, else empty string."""
    return f"\033[{code}m" if _use_color else ""


def DIM():
    return _sgr("2")


def BOLD_RED():
    return _sgr("1;31")


def CYAN():
    return _sgr("36")


def GREEN():
    return _sgr("32")


def YELLOW():
    return _sgr("33")


def RESET():
    return _sgr("0")


def extract_text(message_obj):
    """Extract readable text from a JSONL line's message content."""
    content = message_obj.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                name = block.get("name", "")
                inp = block.get("input", {})
                if name in ("Bash", "Read", "Write", "Edit", "Glob", "Grep"):
                    detail = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                    if len(detail) > 120:
                        detail = detail[:120] + "..."
                    parts.append(f"[{name}: {detail}]")
                else:
                    parts.append(f"[{name}]")
            elif btype == "tool_result":
                result = block.get("content", "")
                if isinstance(result, str):
                    parts.append(result)
                elif isinstance(result, list):
                    for r in result:
                        if r.get("type") == "text":
                            parts.append(r.get("text", ""))
        return "\n".join(parts)
    return ""


def classify_line(obj):
    """Return (type, role, text) for a parsed JSONL line, or None to skip."""
    ltype = obj.get("type")
    if ltype in ("file-history-snapshot", "progress"):
        return None

    msg = obj.get("message", {})
    role = msg.get("role") or ltype

    # User lines with plain string content
    content = msg.get("content", "")
    if isinstance(content, str) and content and ltype == "user":
        return ("user", "user", content)

    if isinstance(content, list):
        if all(b.get("type") == "thinking" for b in content):
            text = "\n".join(b.get("thinking") or "" for b in content).strip()
            return ("thinking", role, text) if text else None

        content_types = {b.get("type") for b in content}
        is_tool = bool(content_types & {"tool_use", "tool_result"})
        has_text = "text" in content_types
        if is_tool and not has_text:
            return ("tool", role, extract_text(msg))
        elif has_text or is_tool:
            text = extract_text(msg)
            return (role if role in ("user", "assistant") else ltype, role, text)

    return None


def _compile_ci(pattern):
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


def highlight_pattern(text, regex):
    """Highlight the regex's matches in text with bold red."""
    return regex.sub(lambda m: f"{BOLD_RED()}{m.group(0)}{RESET()}" if m.group(0) else "", text)


def truncate_around_match(text, regex, context_chars):
    """Truncate text to show context_chars around the regex's first match."""
    if context_chars <= 0:
        return text
    m = regex.search(text)
    if not m:
        return text[:context_chars * 2] + ("..." if len(text) > context_chars * 2 else "")
    start = max(0, m.start() - context_chars)
    end = min(len(text), m.end() + context_chars)
    result = ""
    if start > 0:
        result += "..."
    result += text[start:end]
    if end < len(text):
        result += "..."
    return result


def parse_session_info(filepath):
    """Extract project name and session ID from filepath."""
    parts = Path(filepath).parts
    project = ""
    session_id = ""
    for i, p in enumerate(parts):
        if p == "projects" and i + 1 < len(parts):
            project = parts[i + 1]
            if i + 2 < len(parts):
                session_id = Path(parts[i + 2]).stem
            break
    return project, session_id


_REL_TIME_RE = re.compile(r"^(\d+)\s*([mhdw])$")


def _parse_time_arg(s):
    """Parse --since/--until value: relative (30m/2h/1d/1w) or ISO 8601. Returns aware UTC datetime."""
    s = s.strip()
    m = _REL_TIME_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {
            "m": timedelta(minutes=n),
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
            "w": timedelta(weeks=n),
        }[unit]
        return datetime.now(timezone.utc) - delta
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"invalid time {s!r}: use ISO 8601 (e.g. 2026-04-01) or relative (30m, 2h, 1d, 1w)"
        ) from e
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret naive input as local time
    return dt.astimezone(timezone.utc)


def _parse_iso_ts(ts):
    """Parse a JSONL timestamp string to aware UTC datetime, or None."""
    if not ts:
        return None
    iso = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc)
    except ValueError:
        return None


def _list_files(roots, since=None):
    """Return (mtime, path) for session JSONL files, oldest first, excluding subagents/.

    If `since` (aware UTC datetime) is given, skip files whose mtime predates it:
    every line in such files was written before `since`, so none can match.
    """
    since_ts = since.timestamp() if since else None
    files = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune subagents directories
            dirnames[:] = [d for d in dirnames if d != "subagents"]
            for fname in filenames:
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    continue
                if since_ts is None or mtime >= since_ts:
                    files.append((mtime, fpath))
    files.sort()
    return files


def _mmap_extract_lines(mm, size, match_positions):
    """Given (start, end) byte positions in an mmap, return (line_offset, line_bytes) for each."""
    results = []
    for start, end in match_positions:
        line_start = mm.rfind(b"\n", 0, start) + 1
        line_end = mm.find(b"\n", end)
        if line_end == -1:
            line_end = size
        results.append((line_start, bytes(mm[line_start:line_end])))
    return results


def _search_file_hs(args):
    """Search a single file using hyperscan + mmap. Runs in a worker process."""
    fpath, pattern_str = args
    import hyperscan as hs

    db = hs.Database()
    try:
        db.compile(
            expressions=[pattern_str.encode("utf-8", errors="replace")],
            flags=[hs.HS_FLAG_CASELESS | hs.HS_FLAG_SOM_LEFTMOST],
        )
    except hs.error:
        db.compile(
            expressions=[re.escape(pattern_str).encode("utf-8", errors="replace")],
            flags=[hs.HS_FLAG_CASELESS | hs.HS_FLAG_SOM_LEFTMOST],
        )

    positions = []

    def on_match(id, start, end, flags, context):
        positions.append((start, end))

    matches = []
    try:
        with open(fpath, "rb") as f:
            size = f.seek(0, 2)
            if size == 0:
                return matches
            f.seek(0)
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                db.scan(bytes(mm), match_event_handler=on_match)
                for offset, line in _mmap_extract_lines(mm, size, positions):
                    matches.append((fpath, offset, line))
    except OSError:
        pass
    return matches


def _search_file_re(args):
    """Search a single file using mmap + re. Runs in a worker process."""
    fpath, pattern_str = args
    try:
        regex_b = re.compile(pattern_str.encode("utf-8", errors="replace"), re.IGNORECASE)
    except re.error:
        regex_b = re.compile(re.escape(pattern_str).encode("utf-8", errors="replace"), re.IGNORECASE)

    matches = []
    try:
        with open(fpath, "rb") as f:
            size = f.seek(0, 2)
            if size == 0:
                return matches
            f.seek(0)
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                positions = [(m.start(), m.end()) for m in regex_b.finditer(mm)]
                for offset, line in _mmap_extract_lines(mm, size, positions):
                    matches.append((fpath, offset, line))
    except OSError:
        pass
    return matches


def _has_hyperscan():
    try:
        import hyperscan  # noqa: F401
        return True
    except ImportError:
        return False


def _scan_python(pattern, paths):
    """Python fallback: hyperscan if installed, else mmap+re. Both multiprocessed."""
    use_hs = _has_hyperscan()
    worker_fn = _search_file_hs if use_hs else _search_file_re
    if not use_hs:
        print(
            f"{DIM()}(tip: pip install hyperscan for ~4x faster searches){RESET()}",
            file=sys.stderr,
        )

    work = [(p, pattern) for p in paths]
    workers = min(os.cpu_count() or 1, len(paths))
    # Workers must unpickle worker_fn by module name; only forked workers inherit the entry point's
    # path-loaded module, so other start methods search serially.
    if workers <= 1 or multiprocessing.get_start_method() != "fork":
        for w in work:
            yield from worker_fn(w)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            for file_matches in pool.map(worker_fn, work, chunksize=4):
                yield from file_matches


def _find_rg():
    """Path to rg: PATH first, then the binary the optional `ripgrep` package installs beside Python."""
    found = shutil.which("rg")
    if found:
        return found
    bundled = Path(sys.executable).parent / ("rg.exe" if sys.platform == "win32" else "rg")
    return str(bundled) if bundled.is_file() else None


# rg output with -H -b: path:byte_offset:line. Paths never contain ':' in practice.
_RG_LINE_RE = re.compile(rb"^(.+?):(\d+):")


class _RgRejectedPattern(Exception):
    """rg exited with a usage error and no output, e.g. for a regex only Python's re accepts."""


def _scan_rg(pattern, paths, keep=None, line_prefix=None):
    """Stream (path, line_offset, line_bytes) from rg without buffering its output.

    `line_prefix` is a regex for the start of the lines worth reading; rg then skips every
    other line itself instead of shipping it to Python. `keep(raw_output_line)` drops
    further lines before any other work.
    """
    cmd = [_find_rg(), "--no-heading", "-H", "-b", "-i"]
    try:
        re.compile(pattern)
        if line_prefix:
            pattern = f"{line_prefix}.*(?:{pattern})"
    except re.error:
        cmd.append("-F")
    cmd += ["-e", pattern, "--", *paths]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    found = False
    try:
        for raw in proc.stdout:
            found = True
            if keep is not None and not keep(raw):
                continue
            m = _RG_LINE_RE.match(raw, 0, 4096)
            if m:
                yield os.fsdecode(m.group(1)), int(m.group(2)), raw[m.end():].rstrip(b"\r\n")
        if proc.wait() == 2 and not found:
            raise _RgRejectedPattern
    finally:
        proc.kill()
        proc.stdout.close()
        proc.wait()


def _scan(pattern, paths, keep=None, line_prefix=None):
    """Yield (path, line_offset, line_bytes) for matching lines. Prefers rg, then hyperscan, then mmap+re."""
    if _find_rg():
        try:
            yield from _scan_rg(pattern, paths, keep, line_prefix)
            return
        except (OSError, _RgRejectedPattern):
            pass
    yield from _scan_python(pattern, paths)


# --- Codex rollout files --------------------------------------------------

_CODEX_INJECTED_PREFIXES = ("# AGENTS.md instructions", "<environment_context>")


def _codex_content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("text")
        )
    return ""


def classify_codex_line(obj):
    """Return (type, role, text) for a parsed Codex rollout line, or None to skip."""
    if obj.get("type") != "response_item":
        return None
    p = obj.get("payload", {})
    ptype = p.get("type")
    if ptype == "message":
        role = p.get("role")
        if role not in ("user", "assistant"):
            return None
        text = _codex_content_text(p.get("content"))
        if not text or (role == "user" and text.startswith(_CODEX_INJECTED_PREFIXES)):
            return None
        return (role, role, text)
    if ptype == "reasoning":
        parts = [*(p.get("summary") or []), *(p.get("content") or [])]
        text = "\n".join(b.get("text", "") for b in parts if isinstance(b, dict)).strip()
        return ("thinking", "assistant", text) if text else None
    if ptype in ("function_call", "custom_tool_call"):
        detail = p.get("arguments") or p.get("input") or ""
        if not isinstance(detail, str):
            detail = json.dumps(detail)
        if len(detail) > 120:
            detail = detail[:120] + "..."
        return ("tool", "assistant", f"[{p.get('name', '')}: {detail}]")
    if ptype in ("function_call_output", "custom_tool_call_output"):
        out = p.get("output", "")
        return ("tool", "tool", out if isinstance(out, str) else _codex_content_text(out) or json.dumps(out))
    return None


_codex_cwd_cache = {}


def _codex_session_info(filepath):
    """Project (cwd with / replaced by -, like Claude's directory names) and session id."""
    project = _codex_cwd_cache.get(filepath)
    if project is None:
        project = ""
        try:
            with open(filepath, "r", errors="replace") as f:
                meta = json.loads(f.readline())
            project = (meta.get("payload", {}).get("cwd") or "").replace("/", "-")
        except (OSError, json.JSONDecodeError):
            pass
        _codex_cwd_cache[filepath] = project
    return project, Path(filepath).stem[-36:]


# --- Hits -------------------------------------------------------------------


_EPOCH = datetime.fromtimestamp(0, timezone.utc)


class Hit:
    """One matching message, normalized across agents.

    context(before, after, extra) returns (before_entries, after_entries), each a list of
    (type, role, text) tuples. Tool and thinking entries count toward before and after only
    when their kind is in the set extra.
    """

    def __init__(self, source, project, session, dt, kind, role, text, context):
        self.source = source
        self.project = project
        self.session = session
        self.dt = dt
        self.kind = kind
        self.role = role
        self.text = text
        self.context = context

    @property
    def timestamp(self):
        return self.dt.astimezone().strftime("%Y-%m-%d %H:%M") if self.dt else ""  # local time, as --since reads it


# Lines read on each side of a match while looking for context messages, so a long run of
# tool traffic cannot turn one -A or -B into a read of the whole session.
_CONTEXT_SCAN_LINES = 1000


def _lines_after(f, offset):
    """The lines following the line that starts at `offset`, in file order."""
    f.seek(offset)
    f.readline()
    return iter(f.readline, b"")


def _lines_before(f, offset):
    """The lines preceding `offset`, nearest first, read backward in chunks."""
    pos, head = offset, b""
    while pos > 0:
        step = min(1 << 16, pos)
        pos -= step
        f.seek(pos)
        lines = (f.read(step) + head).split(b"\n")
        head = lines[0]  # may be the tail of a line that starts earlier
        yield from reversed(lines[1:])
    yield head


def _context_entries(lines, n, classify, keep, extra):
    """The first n entries among `lines` that the search would show."""
    found = []
    if n <= 0:
        return found
    for raw in itertools.islice(lines, _CONTEXT_SCAN_LINES):
        if not keep(raw, extra):
            continue
        try:
            entry = classify(json.loads(raw))
        except json.JSONDecodeError:
            continue
        if entry and _visible(entry[0], extra):
            found.append(entry)
            if len(found) == n:
                break
    return found


def _jsonl_context(filepath, offset, before, after, classify, keep, extra):
    try:
        with open(filepath, "rb") as f:
            earlier = _context_entries(_lines_before(f, offset), before, classify, keep, extra)
            later = _context_entries(_lines_after(f, offset), after, classify, keep, extra)
    except OSError:
        return [], []
    return earlier[::-1], later


# Cheap byte-level checks that drop lines classify would discard as tool traffic, so the
# (often huge) tool results are never decoded or parsed unless --tools asks for them.
# `extra` is the set of optional kinds the search shows.
def _type_markers(*names):
    """Byte forms of `"type":"name"` members, compact and spaced, for each name."""
    return tuple(f'"type":{sp}"{n}"'.encode() for n in names for sp in ("", " "))


_TEXT, _TOOL = _type_markers("text"), _type_markers("tool_result", "tool_use")
_MESSAGE, _REASONING = _type_markers("message"), _type_markers("reasoning")


def _has_any(line, markers):
    return any(m in line for m in markers)


def _keep_claude(line, extra):
    return "tool" in extra or _has_any(line, _TEXT) or not _has_any(line, _TOOL)


# Codex message lines start `{"timestamp":...,("ordinal":N,)"type":"response_item","payload":{"type":"message"`;
# unlike a lookahead, this anchored regex keeps rg on its fast path. It matches a superset of the lines
# classify_codex_line accepts (it also matches developer messages).
_CODEX_MESSAGE_PREFIX = (
    r'^\{\s*"timestamp"\s*:\s*"[^"]*"\s*,\s*(?:"ordinal"\s*:\s*[0-9]+\s*,\s*)?'
    r'"type"\s*:\s*"response_item"\s*,\s*"payload"\s*:\s*\{\s*"type"\s*:\s*"message"'
)
_CODEX_MESSAGE_OR_REASONING_PREFIX = _CODEX_MESSAGE_PREFIX[:-len('"message"')] + '"(?:message|reasoning)"'


def _keep_codex(line, extra):
    if "tool" in extra:
        return True
    return _has_any(line, _MESSAGE) or ("thinking" in extra and _has_any(line, _REASONING))


_BATCH_FILES_UNBOUNDED = 256
_BATCH_FILES_NEWEST_FIRST = 32


def _jsonl_hits(
    source, roots, pattern, since, classify, session_info, project_filter, keep, newest_first, extra,
    line_prefix=None, session=None,
):
    """Yield hits oldest file first (lines ascending), or newest first with lines descending.

    Files are scanned in batches so a caller that stops early skips the older files.
    """
    files = _list_files(roots, since)
    if project_filter:
        needle = project_filter.lower()
        files = [f for f in files if needle in session_info(f[1])[0].lower()]
    if session:
        files = [f for f in files if _in_session(session_info(f[1])[1], session)]
    if newest_first:
        files.reverse()
    size = _BATCH_FILES_NEWEST_FIRST if newest_first else _BATCH_FILES_UNBOUNDED
    for i in range(0, len(files), size):
        rank = {path: n for n, (_, path) in enumerate(files[i:i + size])}
        found = {}
        prefix = None if "tool" in extra else line_prefix(extra) if callable(line_prefix) else line_prefix
        for path, offset, line in _scan(pattern, list(rank), lambda raw: keep(raw, extra), prefix):
            if (path, offset) not in found and keep(line, extra):
                found[(path, offset)] = line
        order = sorted(found, key=lambda k: (rank[k[0]], -k[1] if newest_first else k[1]))
        for path, offset in order:
            try:
                obj = json.loads(found[(path, offset)])
            except json.JSONDecodeError:
                continue
            info = classify(obj)
            if info is None:
                continue
            project, session = session_info(path)
            kind, role, text = info
            yield Hit(
                source, project, session, _parse_iso_ts(obj.get("timestamp", "")), kind, role, text,
                lambda b, a, x, f=path, o=offset: _jsonl_context(f, o, b, a, classify, keep, x),
            )


def claude_hits(pattern, project_filter, since, newest_first=False, extra=frozenset(), session=None):
    yield from _jsonl_hits(
        "claude", _claude_roots(), pattern, since, classify_line, parse_session_info,
        project_filter, _keep_claude, newest_first, extra, session=session,
    )


def codex_hits(pattern, project_filter, since, newest_first=False, extra=frozenset(), session=None):
    yield from _jsonl_hits(
        "codex", _codex_roots(), pattern, since, classify_codex_line, _codex_session_info,
        project_filter, _keep_codex, newest_first, extra,
        line_prefix=lambda extra: _CODEX_MESSAGE_OR_REASONING_PREFIX if "thinking" in extra else _CODEX_MESSAGE_PREFIX,
        session=session,
    )


# --- Chat log files ----------------------------------------------------------

# A text chat log line: `YYYY-MM-DD HH:MM SENDER: TEXT`, with line breaks inside a message written as ⏎.
_CHAT_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) ([^:\n]+): ?(.*)$")


def _chat_time(value):
    """Aware datetime in the file's own offset; times without one are local time."""
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.astimezone()


def _chat_messages(path):
    """[(line, dt, sender, text)] for a chat log.

    `.jsonl` files hold one object per line with `sent`, `sender` and `text`. Other files hold
    one message per line as `YYYY-MM-DD HH:MM SENDER: TEXT`; a line without that prefix
    continues the message before it.
    """
    messages = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if path.suffix == ".jsonl":
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("text"), str):
                    messages.append((n, _chat_time(obj.get("sent")), str(obj.get("sender") or "?"), obj["text"]))
                continue
            m = _CHAT_LINE_RE.match(line)
            if m:
                messages.append((n, _chat_time(m.group(1)), m.group(2), m.group(3).replace("\u23ce", "\n")))
            elif messages and line:
                n0, dt, sender, text = messages[-1]
                messages[-1] = (n0, dt, sender, f"{text}\n{line}")
    return messages


def file_hits(path, pattern, project_filter, newest_first=False):
    """Hits from one chat log. The sender is part of the searched text, as `SENDER: TEXT`."""
    if project_filter and project_filter.lower() not in path.name.lower():
        return
    regex = _compile_ci(pattern)
    entries = [(n, dt, f"{sender}: {text}") for n, dt, sender, text in _chat_messages(path)]
    hits = []
    for i, (n, dt, text) in enumerate(entries):
        if regex.search(text):

            def context(b, a, extra, i=i):
                earlier = [("chat", None, e[2]) for e in entries[max(0, i - b):i]] if b > 0 else []
                return earlier, [("chat", None, e[2]) for e in entries[i + 1:i + 1 + max(a, 0)]]

            hits.append(Hit("file", path.name, str(n), dt, "chat", None, text, context))
    if newest_first:
        hits.reverse()
    yield from hits


# --- SQLite-backed agents (OpenCode, Hermes) ----------------------------------


def _open_db(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.create_function(
        "regexp", 2,
        lambda pat, val: bool(val) and _compile_ci(pat).search(val) is not None,
        deterministic=True,
    )
    return conn


def _db_hits(source, db_path, pattern, project_filter, candidate_sql, load_session, newest_first, session=None):
    """Shared driver: find candidate sessions, normalize each, yield matching entries.

    candidate_sql selects session ids whose raw rows match `?`, or is a function of the
    connection that returns that SQL. load_session(conn, sid) returns (project, [(dt, type, role, text)]).
    """
    if not db_path.exists():
        return
    try:
        conn = _open_db(db_path)
        sql = candidate_sql(conn) if callable(candidate_sql) else candidate_sql
        sessions = [r[0] for r in conn.execute(sql, (pattern,) * sql.count("?"))]
    except sqlite3.Error as e:
        print(f"warning: cannot read {source} history at {db_path}: {e}", file=sys.stderr)
        return
    regex = _compile_ci(pattern)
    hits = []
    with conn:
        for sid in sessions:
            if not _in_session(sid, session):
                continue
            project, entries = load_session(conn, sid)
            if project_filter and project_filter.lower() not in project.lower():
                continue
            hits.extend(_entry_hits(source, project, sid, entries, regex))
    hits.sort(key=lambda h: h.dt or _EPOCH, reverse=newest_first)
    yield from hits


def _entry_hits(source, project, sid, entries, regex):
    """Hits for the entries [(dt, type, role, text)] of one loaded session that match regex."""
    for i, (dt, kind, role, text) in enumerate(entries):
        if not regex.search(text):
            continue

        def context(b, a, extra, i=i):
            earlier = [e[1:] for e in entries[:i] if _visible(e[1], extra)]
            later = [e[1:] for e in entries[i + 1:] if _visible(e[1], extra)]
            return earlier[-b:] if b > 0 else [], later[:max(a, 0)]

        yield Hit(source, project, sid, dt, kind, role, text, context)


def _from_epoch(value, divisor=1):
    try:
        return datetime.fromtimestamp(float(value) / divisor, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _tool_text(name, detail, output=""):
    if not isinstance(detail, str):
        detail = json.dumps(detail)
    if len(detail) > 120:
        detail = detail[:120] + "..."
    text = f"[{name}: {detail}]"
    return f"{text}\n{output}" if output else text


def _opencode_load(conn, sid):
    row = conn.execute("SELECT directory FROM session WHERE id = ?", (sid,)).fetchone()
    project = (row[0] if row else "").replace("/", "-")
    entries = []
    rows = conn.execute(
        "SELECT m.data, p.data, p.time_created FROM part p JOIN message m ON m.id = p.message_id "
        "WHERE p.session_id = ? ORDER BY p.time_created, p.id",
        (sid,),
    )
    for mdata, pdata, created in rows:
        try:
            role = json.loads(mdata).get("role")
            part = json.loads(pdata)
        except json.JSONDecodeError:
            continue
        dt = _from_epoch(created, 1000)
        if part.get("type") == "text" and role in ("user", "assistant") and part.get("text"):
            entries.append((dt, role, role, part["text"]))
        elif part.get("type") == "reasoning" and part.get("text"):
            entries.append((dt, "thinking", "assistant", part["text"]))
        elif part.get("type") == "tool":
            state = part.get("state", {})
            output = state.get("output") or ""
            entries.append((dt, "tool", "assistant", _tool_text(
                part.get("tool", ""), state.get("input", ""), output if isinstance(output, str) else json.dumps(output),
            )))
    return project, entries


def opencode_hits(pattern, project_filter, since, newest_first=False, extra=frozenset(), session=None):
    yield from _db_hits(
        "opencode", _opencode_db(), pattern, project_filter,
        "SELECT DISTINCT session_id FROM part WHERE data REGEXP ?", _opencode_load, newest_first, session,
    )


def _hermes_reasoning_sql(conn):
    """SQL for a message's reasoning text; older databases have no reasoning column."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    return "reasoning" if "reasoning" in columns else "NULL"


def _hermes_load(conn, sid):
    row = conn.execute("SELECT cwd, source FROM sessions WHERE id = ?", (sid,)).fetchone()
    project = ((row[0] or "").replace("/", "-") or row[1] or "") if row else ""
    entries = []
    rows = conn.execute(
        f"SELECT role, content, tool_calls, tool_name, timestamp, {_hermes_reasoning_sql(conn)} FROM messages "
        "WHERE session_id = ? ORDER BY timestamp, id",
        (sid,),
    )
    for role, content, tool_calls, tool_name, ts, reasoning in rows:
        dt = _from_epoch(ts)
        if role == "tool":
            entries.append((dt, "tool", "tool", f"[{tool_name or 'tool'}]\n{content or ''}"))
            continue
        if role not in ("user", "assistant"):
            continue
        if reasoning:
            entries.append((dt, "thinking", role, reasoning))
        if content:
            entries.append((dt, role, role, content))
        if tool_calls:
            try:
                for call in json.loads(tool_calls):
                    fn = call.get("function", {})
                    entries.append((dt, "tool", "assistant", _tool_text(fn.get("name", ""), fn.get("arguments", ""))))
            except (json.JSONDecodeError, AttributeError):
                pass
    return project, entries


def hermes_hits(pattern, project_filter, since, newest_first=False, extra=frozenset(), session=None):
    yield from _db_hits(
        "hermes", _hermes_db(), pattern, project_filter,
        lambda conn: "SELECT DISTINCT session_id FROM messages WHERE content REGEXP ? OR tool_calls REGEXP ?"
        + (f" OR {_hermes_reasoning_sql(conn)} REGEXP ?" if "thinking" in extra else ""),
        _hermes_load, newest_first, session,
    )


# --- Antigravity CLI ---------------------------------------------------------

# agy keeps one SQLite database per conversation. Each row of `steps` is one step of the
# conversation, its `step_payload` a protobuf message with no published schema. The field
# numbers below were read from this host's agy conversations.
_AGY_USER, _AGY_REPLY = 14, 15


def _pb_fields(b):
    """Yield (field, wire type, value) for a protobuf message; ValueError if b is not one."""
    i = 0
    while i < len(b):
        key, i = _pb_varint(b, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _pb_varint(b, i)
        elif wire == 2:
            n, i = _pb_varint(b, i)
            value, i = b[i:i + n], i + n
        elif wire in (1, 5):
            n = 8 if wire == 1 else 4
            value, i = b[i:i + n], i + n
        else:
            raise ValueError(f"unsupported wire type {wire}")
        if field == 0 or i > len(b):
            raise ValueError("truncated message")
        yield field, wire, value


def _pb_varint(b, i):
    value = shift = 0
    while True:
        if i >= len(b):
            raise ValueError("truncated varint")
        c = b[i]
        i += 1
        value |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return value, i


def _pb_values(b, field):
    try:
        return [v for f, _, v in _pb_fields(b) if f == field]
    except ValueError:
        return []


def _pb_get(b, *path):
    """The first value at a path of field numbers, or None."""
    for field in path:
        values = _pb_values(b, field) if isinstance(b, bytes) else []
        if not values:
            return None
        b = values[0]
    return b


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _pb_text(b):
    """b as text when it decodes as UTF-8 without control characters other than whitespace."""
    try:
        text = b.decode("utf-8")
    except (AttributeError, UnicodeDecodeError):
        return None
    return None if _CONTROL_CHARS.search(text) else text


def _pb_strings(b, skip=(), depth=0):
    """Every string in a message, in field order, treating a field as text when it reads as text."""
    out = []
    try:
        for field, wire, value in _pb_fields(b):
            if wire != 2 or not value or field in skip:
                continue
            text = _pb_text(value)
            if text is not None:
                out.append(text)
            elif depth < 12:
                out.extend(_pb_strings(value, depth=depth + 1))
    except ValueError:
        pass
    return out


def _agy_entries(step_type, payload, extra):
    """[(dt, type, role, text)] for one agy step, leaving out tool steps unless extra has "tool"."""
    if step_type not in (_AGY_USER, _AGY_REPLY) and "tool" not in extra:
        return []  # tool output is most of the history and costly to decode
    stamp = _pb_get(payload, 5, 1)
    seconds = _pb_get(stamp, 1) if stamp else None
    dt = _from_epoch(seconds) if isinstance(seconds, int) else None
    if step_type == _AGY_USER:
        text = _pb_text(_pb_get(payload, 19, 2) or b"")
        return [(dt, "user", "user", text)] if text else []
    if step_type == _AGY_REPLY:
        reply = _pb_get(payload, 20) or b""
        entries = []
        for field, kind in ((3, "thinking"), (1, "assistant")):
            text = _pb_text(_pb_get(reply, field) or b"")
            if text:
                entries.append((dt, kind, "assistant", text))
        for call in _pb_values(reply, 7):
            name = _pb_text(_pb_get(call, 2) or b"") or ""
            entries.append((dt, "tool", "assistant", _tool_text(name, _pb_text(_pb_get(call, 3) or b"") or "")))
        return entries
    # Tool results, notices, and errors: their text, without the step metadata in field 5.
    text = "\n".join(t for t in _pb_strings(payload, skip=(5,)) if t.strip())
    return [(dt, "tool", "tool", text)] if text else []


def _agy_project(conn):
    """The conversation's workspace with / replaced by -, as Claude Code names project directories."""
    row = conn.execute("SELECT data FROM trajectory_metadata_blob").fetchone()
    uri = _pb_text(_pb_get(row[0], 7) or b"") if row else None
    return (uri or "").removeprefix("file://").replace("/", "-")


_REGEX_SPECIAL = frozenset(".^$*+?{}[]\\|()")


def antigravity_hits(pattern, project_filter, since, newest_first=False, extra=frozenset(), session=None):
    root = _antigravity_dir()
    since_ts = since.timestamp() if since else None
    files = []
    for path in root.glob("*.db"):
        if not _in_session(path.stem, session):
            continue
        try:
            # The write-ahead log holds the newest steps, so it dates the conversation too.
            mtime = max(p.stat().st_mtime for p in (path, path.with_name(path.name + "-wal")) if p.exists())
        except OSError:
            continue
        if since_ts is None or mtime >= since_ts:
            files.append((mtime, path))
    files.sort(reverse=newest_first)
    regex = _compile_ci(pattern)
    literal = pattern.lower().encode() if pattern.isascii() and not _REGEX_SPECIAL & set(pattern) else None

    def mentions(raw):
        if literal is not None:
            return literal in raw.lower()  # several times faster than a case-insensitive regex
        return regex.search(raw.decode("utf-8", "ignore")) is not None

    for _, path in files:
        try:
            conn = _open_db(path)
            with conn:
                project = _agy_project(conn)
                if project_filter and project_filter.lower() not in project.lower():
                    continue
                steps = conn.execute("SELECT step_type, step_payload FROM steps ORDER BY idx").fetchall()
        except sqlite3.Error as e:
            print(f"warning: cannot read Antigravity history at {path}: {e}", file=sys.stderr)
            continue
        # Most conversations do not mention the pattern anywhere; skip them before decoding.
        if not mentions(b"\0".join(payload for _, payload in steps if payload)):
            continue
        entries = [e for step_type, payload in steps if payload for e in _agy_entries(step_type, payload, extra)]
        hits = list(_entry_hits("antigravity", project, path.stem, entries, regex))
        yield from reversed(hits) if newest_first else hits


# Agents this package searches: name -> (hits function, is there history here, label).
SOURCES = {
    "claude": (claude_hits, lambda: any(r.exists() for r in _claude_roots()), "Claude Code"),
    "codex": (codex_hits, lambda: any(r.exists() for r in _codex_roots()), "Codex"),
    "opencode": (opencode_hits, lambda: _opencode_db().exists(), "OpenCode"),
    "hermes": (hermes_hits, lambda: _hermes_db().exists(), "Hermes"),
    "antigravity": (antigravity_hits, lambda: any(_antigravity_dir().glob("*.db")), "Antigravity"),
}


def _config_path():
    if sys.platform == "win32":
        base = _env_path("APPDATA", Path.home() / "AppData" / "Roaming")
    else:
        base = _env_path("XDG_CONFIG_HOME", Path.home() / ".config")
    return base / "search-history" / "config.json"


def _warn(message):
    print(f"warning: {message}", file=sys.stderr)


def _read_config():
    """The saved settings as a dict; a missing, unreadable, or malformed file reads as empty, with a warning."""
    path = _config_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        _warn(f"ignoring {path}: {e}")
        return {}
    if not isinstance(data, dict):
        _warn(f"ignoring {path}: expected a JSON object")
        return {}
    return data


@functools.lru_cache(maxsize=None)
def _read_config_agents():
    """The agents the user chose with --default-agents (["all"] for every agent), or None."""
    agents = _read_config().get("agents")
    if agents is None:
        return None
    path = _config_path()
    if not (isinstance(agents, list) and agents and all(isinstance(a, str) for a in agents)):
        _warn(f"ignoring the agents setting in {path}: expected a non-empty list of agent names")
        return None
    unknown = [a for a in agents if a != "all" and a not in SOURCES]
    if unknown:
        _warn(f"ignoring unknown agents in {path}: {', '.join(unknown)} (known: {', '.join(sorted(SOURCES))})")
        agents = [a for a in agents if a not in unknown]
    return agents or None


def _write_config_agents(agents):
    """Save the default agents (None removes the setting), keeping any other saved settings."""
    path = _config_path()
    data = _read_config()
    if agents is None:
        data.pop("agents", None)
    else:
        data["agents"] = agents
    try:
        if data:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(json.dumps(data, indent=2) + "\n")
            os.replace(temporary, path)
        else:
            path.unlink(missing_ok=True)
    except OSError as e:
        print(f"error: cannot save the default agents in {path}: {e}", file=sys.stderr)
        sys.exit(1)
    _read_config_agents.cache_clear()


# Wrapper packages (see build_wrappers.py) contain no code. Each declares the agent it defaults to as an
# entry point in this group, so which agents a plain `sch` searches comes from the installed metadata,
# whichever script of theirs or ours runs. Nothing is imported from the entry point.
_DEFAULT_AGENTS_GROUP = "search_clanker_history.default_agents"


@functools.lru_cache(maxsize=None)
def _wrapper_packages():
    """[(distribution name, agent)] for every installed package that declares a default agent."""
    from importlib.metadata import distributions
    found = []
    for dist in distributions():
        for entry in dist.entry_points:
            if entry.group == _DEFAULT_AGENTS_GROUP:
                found.append((dist.metadata["Name"], entry.name))
    return sorted(found)


def _default_agents():
    """The agents searched when --agent is not given, and where that choice comes from.

    Returns (agent names, or None for every agent with history, source description).
    """
    configured = _read_config_agents()
    if configured is not None:
        return (None if configured == ["all"] else configured), "config file"
    package_default = [agent for _, agent in _wrapper_packages() if agent in SOURCES]
    if package_default:
        return package_default, "package default"
    return None, "built-in default"


def _describe_default_agents():
    names, source = _default_agents()
    return f"{', '.join(SOURCES[n][2] for n in names) if names else 'every agent with history'} ({source})"


def _show_default_agents():
    names, source = _default_agents()
    print(f"default agents: {', '.join(names) if names else 'every agent with history'} ({source})")
    print(f"settings file: {_config_path()}")


def _set_default_agents(values):
    """Handle `--default-agents VALUES`: show, set, or reset (remove) the saved default."""
    values = list(dict.fromkeys(values))
    if not values:
        _show_default_agents()
    elif values == ["reset"]:
        _write_config_agents(None)
        _show_default_agents()
    elif values == ["all"] or "all" not in values and "reset" not in values:
        _write_config_agents(values)
        _show_default_agents()
    else:
        print("error: use 'all' or 'reset' alone, or a list of agents", file=sys.stderr)
        sys.exit(2)


class _VersionAction(argparse.Action):
    """--version, built only when asked: the version, the installed package names, and the default scope."""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, help="Show the version and default agents")

    def __call__(self, parser, namespace, values, option_string=None):
        packages = ", ".join(name for name, _ in _wrapper_packages()) or _DIST_NAME
        print(f"{parser.prog} {_get_version()} ({packages}); default agents: {_describe_default_agents()}")
        parser.exit()


def _label(kind, role):
    """The tag shown before a message: its role, or "thinking" for reasoning."""
    return "thinking" if kind == "thinking" else role


def format_role(role):
    """The role tag and its trailing space; chat log messages carry their sender in the text."""
    if role is None:
        return ""
    return f"{_role_tag(role)} "


def _role_tag(role):
    if role == "user":
        return f"{GREEN()}[user]{RESET()}"
    elif role == "assistant":
        return f"{CYAN()}[assistant]{RESET()}"
    else:
        return f"{YELLOW()}[{role}]{RESET()}"


def main():
    parser = argparse.ArgumentParser(
        description=f"Search {', '.join(v[2] for v in SOURCES.values())} session history",
        allow_abbrev=False,  # --default-agents saves a setting, so it is reachable only by its full name
    )
    parser.add_argument("-V", "--version", action=_VersionAction)
    parser.add_argument("pattern", nargs="?", help="Search pattern (regex, case-insensitive)")
    parser.add_argument(
        "-i", "--ignore-case", action="store_true",
        help="Accepted for grep users; matching is always case-insensitive",
    )
    parser.add_argument(
        "--file", action="append", type=Path, metavar="PATH",
        help="Search a chat log instead of agent history (repeatable): a .jsonl file of objects with "
        "sent, sender and text, or a text file of 'YYYY-MM-DD HH:MM SENDER: TEXT' lines. "
        "Each message is searched as 'SENDER: TEXT', so ^NAME: matches one sender",
    )
    parser.add_argument(
        "--agent", action="append", choices=sorted(SOURCES), metavar="AGENT",
        help=f"Agent history to search: {', '.join(sorted(SOURCES))} "
        "(repeatable; overrides the default agents for this search)",
    )
    parser.add_argument(
        "--default-agents", nargs="*", choices=[*sorted(SOURCES), "all", "reset"], metavar="AGENT",
        help="Show the agents searched by default, or save a new default: a list of agents, "
        "'all' for every agent with history, or 'reset' for the package's own default",
    )
    parser.add_argument("-p", "--project", help="Filter to project (substring match on dir name)")
    parser.add_argument(
        "-s", "--session", metavar="ID",
        help="Only the session whose id starts with ID, such as the 8 characters a result header shows",
    )
    parser.add_argument(
        "-t", "--type",
        choices=["user", "assistant"],
        help="Filter by message role",
    )
    parser.add_argument("-A", type=int, default=0, metavar="N", help="Show N messages after match")
    parser.add_argument("-B", type=int, default=0, metavar="N", help="Show N messages before match")
    parser.add_argument(
        "--tools", action="store_true",
        help="Include tool_use/tool_result messages (skipped by default)",
    )
    parser.add_argument(
        "--thinking", action="store_true",
        help="Include the agents' reasoning where their history keeps it as text (skipped by default)",
    )
    parser.add_argument(
        "-C", "--context", type=int, default=200, metavar="CHARS",
        help="Chars of text shown on each side of the match (default 200); 0 shows whole messages",
    )
    parser.add_argument(
        "-n", "--limit", type=int, metavar="N",
        help="Only the N most recent matching messages (still printed oldest first); "
        "stops reading older history as soon as N are found",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable colored output",
    )
    parser.add_argument(
        "--since", type=_parse_time_arg, metavar="TIME",
        help="Only messages at/after TIME (ISO 8601 or relative: 30m, 2h, 1d, 1w)",
    )
    parser.add_argument(
        "--until", type=_parse_time_arg, metavar="TIME",
        help="Only messages at/before TIME (ISO 8601 or relative)",
    )
    args = parser.parse_args()

    if args.default_agents is not None:
        if args.pattern is not None or args.agent:
            parser.error("--default-agents changes a setting and cannot be combined with a search or --agent")
        _set_default_agents(args.default_agents)
        return
    if not args.pattern:
        parser.error("the following arguments are required: pattern")

    _init_color(force_no_color=args.no_color)

    if args.file:
        if args.agent:
            parser.error("--file searches only the named files and cannot be combined with --agent")
        if args.type or args.tools or args.thinking or args.session:
            parser.error("-t, -s, --tools and --thinking apply to agent history; match a sender in a chat log with ^NAME:")
        unreadable = [str(f) for f in args.file if not f.is_file()]
        if unreadable:
            print(f"Error: no such file: {', '.join(unreadable)}", file=sys.stderr)
            sys.exit(1)
    sources = {
        str(f): lambda newest_first, f=f: file_hits(f, args.pattern, args.project, newest_first)
        for f in dict.fromkeys(args.file or [])
    }

    scoped, source = (None, None) if args.agent or args.file else _default_agents()
    if args.file:
        names, missing = list(sources), []
    elif args.agent:
        names = list(dict.fromkeys(args.agent))
        missing = [n for n in names if not SOURCES[n][1]()]
    else:
        names = [n for n in (scoped if scoped is not None else SOURCES) if n in SOURCES and SOURCES[n][1]()]
        missing = [] if names else (scoped or ["agent"])
    unsearched = [SOURCES[n][2] for n in SOURCES if scoped is not None and n not in names and SOURCES[n][1]()]
    hint = (f"not searched: {', '.join(unsearched)}; the default agents come from the {source}, "
            f"use --agent or `{parser.prog} --default-agents all`") if unsearched else None
    if missing:
        print(f"Error: no {', '.join(missing)} history found", file=sys.stderr)
        if hint:
            print(hint, file=sys.stderr)
        sys.exit(1)

    extra = frozenset(kind for kind, on in (("tool", args.tools), ("thinking", args.thinking)) if on)

    def accepted(hit):
        if not _visible(hit.kind, extra):
            return False
        if args.type and hit.role != args.type:
            return False
        if args.since or args.until:
            if hit.dt is None:
                return False  # no timestamp — can't confirm it falls in window
            if args.since and hit.dt < args.since:
                return False
            if args.until and hit.dt > args.until:
                return False
        return True

    def stream(name, newest_first):
        if name in sources:
            hits = sources[name](newest_first)
        else:
            hits = SOURCES[name][0](
                args.pattern, args.project, args.since, newest_first=newest_first, extra=extra,
                session=args.session,
            )
        return (h for h in hits if accepted(h))

    if args.limit:
        recent = []
        for name in names:
            recent.extend(itertools.islice(stream(name, True), args.limit))
        recent.sort(key=lambda h: h.dt or _EPOCH)
        hits = recent[-args.limit:]
    else:
        hits = itertools.chain.from_iterable(stream(name, False) for name in names)

    printed_count = 0
    regex = _compile_ci(args.pattern)
    # Without color the matching message looks like its context messages, so mark it.
    marker = ">" if args.A > 0 or args.B > 0 else " "

    for hit in hits:
        before_lines, after_lines = [], []
        if args.B > 0 or args.A > 0:
            before_lines, after_lines = hit.context(args.B, args.A, extra)

        short_session = hit.session[:8] if hit.session else "?"
        if hit.source == "file":
            header = f"{DIM()}── {hit.timestamp} | {hit.project}:{hit.session} ──{RESET()}"
        else:
            header = (
                f"{DIM()}── {hit.timestamp} | {hit.source} | {hit.project} "
                f"| session:{short_session} ──{RESET()}"
            )
        if printed_count > 0:
            print()
        print(header)

        for k, r, t in before_lines:
            t_short = truncate_around_match(t, regex, args.context).replace("\n", " ")
            print(f"  {DIM()}{format_role(_label(k, r))}{t_short}{RESET()}")

        text_short = truncate_around_match(hit.text, regex, args.context).replace("\n", " ")
        print(f"{marker} {format_role(_label(hit.kind, hit.role))}{highlight_pattern(text_short, regex)}")

        for k, r, t in after_lines:
            t_short = truncate_around_match(t, regex, args.context).replace("\n", " ")
            print(f"  {DIM()}{format_role(_label(k, r))}{t_short}{RESET()}")

        printed_count += 1

    if printed_count == 0:
        print("No matches found.")
        if hint:
            print(hint, file=sys.stderr)
    else:
        print(f"\n{DIM()}{printed_count} match(es){RESET()}")


if __name__ == "__main__":
    main()
