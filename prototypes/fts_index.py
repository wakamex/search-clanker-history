"""Prototype: persistent SQLite FTS5 trigram index over user/assistant text (Claude Code + Codex).

Usage: fts_index.py build|query|stats [args]. Not part of the package.
"""
import concurrent.futures
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "cli", Path(__file__).resolve().parent.parent / "src/search_claude_history/cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

DB = Path(os.environ.get("FTS_DB", Path(tempfile.gettempdir()) / "sch-index.db"))
SOURCES = {
    "claude": (cli._claude_roots, cli.classify_line, cli._keep_claude, cli.parse_session_info),
    "codex": (cli._codex_roots, cli.classify_codex_line, cli._keep_codex, cli._codex_session_info),
}


def extract(args):
    """Read one file from `start`; return (path, end_offset, rows)."""
    source, path, start = args
    _, classify, keep, info = SOURCES[source]
    project, session = info(path)
    rows = []
    with open(path, "rb") as f:
        f.seek(start)
        offset = start
        for line in f:
            end = offset + len(line)
            if not line.endswith(b"\n"):
                break  # partial last line, pick it up next time
            if keep(line, False):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    obj = None
                got = classify(obj) if obj else None
                if got and got[0] != "tool":
                    rows.append((offset, obj.get("timestamp", ""), got[1], got[2]))
            offset = end
    return source, path, project, session, offset, rows


def connect():
    conn = sqlite3.connect(DB)
    conn.executescript("""
    PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF;
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, source, path UNIQUE, project, session,
        size INTEGER, mtime REAL, indexed_to INTEGER);
    CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY, file_id, offset INTEGER, ts, role, text);
    CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, content=msgs, content_rowid=id,
        tokenize='trigram');
    """)
    return conn


def build():
    conn = connect()
    known = {p: (i, to) for i, p, to in conn.execute("SELECT id, path, indexed_to FROM files")}
    work = []
    for source, (roots, *_rest) in SOURCES.items():
        for mtime, path in cli._list_files(roots(), None):
            size = os.path.getsize(path)
            start = known.get(path, (None, 0))[1]
            if size > start:
                work.append((source, path, start))
    t = time.time()
    print(f"{len(work)} files to (re)index", flush=True)
    n = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        for source, path, project, session, end, rows in pool.map(extract, work, chunksize=2):
            fid = known.get(path, (None,))[0]
            if fid is None:
                fid = conn.execute(
                    "INSERT INTO files(source,path,project,session,size,mtime,indexed_to) VALUES(?,?,?,?,0,0,0)",
                    (source, path, project, session)).lastrowid
            for offset, ts, role, text in rows:
                cur = conn.execute("INSERT INTO msgs(file_id,offset,ts,role,text) VALUES(?,?,?,?,?)",
                                   (fid, offset, ts, role, text))
                conn.execute("INSERT INTO fts(rowid,text) VALUES(?,?)", (cur.lastrowid, text))
            conn.execute("UPDATE files SET indexed_to=? WHERE id=?", (end, fid))
            n += len(rows)
    conn.commit()
    print(f"indexed {n} messages in {time.time() - t:.1f}s")


def query(term, limit=0):
    conn = connect()
    t = time.time()
    sql = ("SELECT f.source, f.project, f.session, m.ts, m.role, m.text FROM fts JOIN msgs m ON m.id=fts.rowid "
           "JOIN files f ON f.id=m.file_id WHERE fts MATCH ? ORDER BY m.ts DESC")
    if limit:
        sql += f" LIMIT {limit}"
    rows = conn.execute(sql, ('"' + term.replace('"', '""') + '"',)).fetchall()
    print(f"{len(rows)} hits in {time.time() - t:.3f}s")
    for r in rows[:3]:
        print(" ", r[0], r[1], r[3][:16], r[4], r[5][:80].replace("\n", " "))


def stats():
    conn = connect()
    print("files", conn.execute("SELECT count(*) FROM files").fetchone(),
          "msgs", conn.execute("SELECT count(*), sum(length(text)) FROM msgs").fetchone())
    print("db bytes", DB.stat().st_size)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "build":
        build()
    elif cmd == "query":
        query(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
    else:
        stats()
