import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from importlib.metadata import version
from unittest import mock
from pathlib import Path


def record(role, text, timestamp):
    return {
        "type": role,
        "sessionId": "0123abcd-0000-0000-0000-000000000000",
        "timestamp": timestamp,
        "message": {"role": role, "content": text},
    }



class CliTests(unittest.TestCase):
    def run_cli(self, *args, config_dir=None, home=None, tz="UTC"):
        env = dict(os.environ, NO_COLOR="1", TZ=tz)
        for name in ("CODEX_HOME", "HERMES_HOME", "XDG_DATA_HOME", "CLAUDE_CONFIG_DIR"):
            env.pop(name, None)
        if home is not None:
            env["HOME"] = str(home)
        if config_dir is not None:
            env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        return subprocess.run(
            args, capture_output=True, text=True, env=env, check=False
        )

    def test_entry_points_report_the_package_version(self):
        expected = version("search-clanker-history")
        for args in (
            ("search-clanker-history", "--version"),
            (sys.executable, "-m", "search_clanker_history", "--version"),
        ):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_search_finds_matching_messages_in_synthetic_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            project = config_dir / "projects" / "-code-demo"
            project.mkdir(parents=True)
            session = project / "0123abcd-0000-0000-0000-000000000000.jsonl"
            session.write_text(
                "\n".join(
                    json.dumps(item)
                    for item in (
                        record("user", "where is the frobnicate helper", "2026-01-02T03:04:05Z"),
                        record("assistant", "it lives in utils", "2026-01-02T03:04:06Z"),
                    )
                )
                + "\n"
            )

            found = self.run_cli("search-clanker-history", "frobnicate", config_dir=config_dir, home=temporary)
            missing = self.run_cli("search-clanker-history", "no-such-text", config_dir=config_dir, home=temporary)

        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("where is the frobnicate helper", found.stdout)
        self.assertIn("-code-demo", found.stdout)
        self.assertNotIn("it lives in utils", found.stdout)
        self.assertNotIn("where is the frobnicate helper", missing.stdout)

    def test_codex_rollouts_are_searched_with_cwd_project_and_tool_filtering(self):
        with tempfile.TemporaryDirectory() as temporary:
            day = Path(temporary) / ".codex" / "sessions" / "2026" / "01" / "02"
            day.mkdir(parents=True)
            sid = "01a10334-5465-7210-a943-b674c6eea5b6"
            lines = [
                {"timestamp": "2026-01-02T03:04:04Z", "type": "session_meta",
                 "payload": {"id": sid, "cwd": "/code/demo"}},
                {"timestamp": "2026-01-02T03:04:05Z", "type": "response_item",
                 "payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": "find the quuxify helper"}]}},
                {"timestamp": "2026-01-02T03:04:06Z", "type": "response_item",
                 "payload": {"type": "message", "role": "developer",
                             "content": [{"type": "input_text", "text": "quuxify developer note"}]}},
                {"timestamp": "2026-01-02T03:04:07Z", "type": "response_item",
                 "payload": {"type": "function_call", "name": "shell",
                             "arguments": "{\"cmd\": \"grep quuxify\"}", "call_id": "c1"}},
                {"timestamp": "2026-01-02T03:04:08Z", "type": "response_item",
                 "payload": {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "quuxify is in utils"}]}},
            ]
            (day / f"rollout-2026-01-02T03-04-04-{sid}.jsonl").write_text(
                "\n".join(json.dumps(item) for item in lines) + "\n"
            )

            plain = self.run_cli("search-clanker-history", "quuxify", "--agent", "codex", "-A", "1", home=temporary)
            tools = self.run_cli("search-clanker-history", "quuxify", "--tools", home=temporary)
            by_project = self.run_cli("search-clanker-history", "quuxify", "-p", "nothing", home=temporary)

        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn("| codex | -code-demo | session:01a10334", plain.stdout)
        self.assertIn("find the quuxify helper", plain.stdout)
        self.assertIn("quuxify is in utils", plain.stdout)
        self.assertNotIn("developer note", plain.stdout)
        self.assertNotIn("[shell:", plain.stdout)
        self.assertIn("[shell:", tools.stdout)
        self.assertIn("No matches found.", by_project.stdout)

    def test_opencode_database_is_searched(self):
        with tempfile.TemporaryDirectory() as temporary:
            db_dir = Path(temporary) / ".local" / "share" / "opencode"
            db_dir.mkdir(parents=True)
            conn = sqlite3.connect(db_dir / "opencode.db")
            conn.executescript(
                "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);"
                "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT);"
                "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,"
                " time_created INTEGER, data TEXT);"
            )
            conn.execute("INSERT INTO session VALUES ('ses_abc12345', '/code/demo')")
            conn.execute("INSERT INTO message VALUES ('m1', 'ses_abc12345', ?)", (json.dumps({"role": "user"}),))
            conn.execute("INSERT INTO message VALUES ('m2', 'ses_abc12345', ?)", (json.dumps({"role": "assistant"}),))
            parts = [
                ("p1", "m1", 1767323045000, {"type": "text", "text": "where is the zorbify helper"}),
                ("p2", "m2", 1767323046000, {"type": "text", "text": "zorbify lives in utils"}),
            ]
            for pid, mid, created, data in parts:
                conn.execute(
                    "INSERT INTO part VALUES (?, ?, 'ses_abc12345', ?, ?)", (pid, mid, created, json.dumps(data))
                )
            conn.commit()
            conn.close()

            found = self.run_cli("search-clanker-history", "zorbify", "-t", "assistant", home=temporary)

        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("| opencode | -code-demo | session:ses_abc1", found.stdout)
        self.assertIn("zorbify lives in utils", found.stdout)
        self.assertNotIn("where is the zorbify helper", found.stdout)

    def test_antigravity_transcripts_are_searched(self):
        with tempfile.TemporaryDirectory() as temporary:
            agy = Path(temporary) / ".gemini" / "antigravity-cli"
            sid = "1946fe8f-0000-0000-0000-000000000000"
            logs = agy / "brain" / sid / ".system_generated" / "logs"
            logs.mkdir(parents=True)
            steps = [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
                 "created_at": "2026-01-02T03:04:05Z", "content": "<USER_REQUEST>\nwhere is the glorpify helper\n</USER_REQUEST>"},
                {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
                 "created_at": "2026-01-02T03:04:06Z", "thinking": "glorpify might be in utils, check first",
                 "tool_calls": [{"name": "run_command", "args": {"CommandLine": "grep glorpify"}}]},
                {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE",
                 "created_at": "2026-01-02T03:04:07Z", "content": "utils.py: def glorpify"},
                {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
                 "created_at": "2026-01-02T03:04:08Z", "content": "glorpify is in utils"},
            ]
            (logs / "transcript_full.jsonl").write_text("".join(json.dumps(step) + "\n" for step in steps))
            (logs / "transcript.jsonl").write_text(json.dumps({**steps[0], "content": "glorpify duplicate"}) + "\n")
            conn = sqlite3.connect(agy / "conversation_summaries.db")
            conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT NOT NULL)")
            conn.execute("INSERT INTO conversation_summaries VALUES (?, ?)", (sid, json.dumps(["file:///code/demo"])))
            conn.commit()
            conn.close()

            plain = self.run_cli("search-clanker-history", "glorpify", "--agent", "antigravity", "-B", "1", home=temporary)
            thinking = self.run_cli("search-clanker-history", "check first", "--thinking", "-p", "demo", home=temporary)
            tools = self.run_cli("search-clanker-history", "glorpify", "--tools", "--thinking", home=temporary)

        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn("── 2026-01-02 03:04 | antigravity | -code-demo | session:1946fe8f ──", plain.stdout)
        self.assertIn("> [user] where is the glorpify helper", plain.stdout)
        self.assertIn("  [user] where is the glorpify helper", plain.stdout)
        self.assertIn("> [assistant] glorpify is in utils", plain.stdout)
        self.assertIn("2 match(es)", plain.stdout)
        self.assertNotIn("duplicate", plain.stdout)
        self.assertIn("[thinking] glorpify might be in utils, check first", thinking.stdout)
        self.assertIn('[run_command: {"CommandLine": "grep glorpify"}]', tools.stdout)
        self.assertIn("[tool] utils.py: def glorpify", tools.stdout)
        self.assertIn("5 match(es)", tools.stdout)

    def test_thinking_is_searched_only_with_the_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary) / ".claude"
            project = config_dir / "projects" / "-code-demo"
            project.mkdir(parents=True)
            claude_thought = {**record("assistant", "", "2026-01-02T00:00:01Z"), "message": {
                "role": "assistant", "content": [{"type": "thinking", "thinking": "the wibble is claude's", "signature": "x"}]}}
            (project / "0123abcd-0000-0000-0000-000000000000.jsonl").write_text(json.dumps(claude_thought) + "\n")

            day = Path(temporary) / ".codex" / "sessions" / "2026" / "01" / "02"
            day.mkdir(parents=True)
            sid = "01a10334-5465-7210-a943-b674c6eea5b6"
            codex_lines = [
                {"timestamp": "2026-01-02T00:00:00Z", "type": "session_meta", "payload": {"id": sid, "cwd": "/code/demo"}},
                {"timestamp": "2026-01-02T00:00:02Z", "type": "response_item", "payload": {
                    "type": "reasoning", "summary": [{"type": "summary_text", "text": "the wibble is codex's"}],
                    "encrypted_content": "x"}},
                {"timestamp": "2026-01-02T00:00:03Z", "type": "response_item", "payload": {
                    "type": "reasoning", "summary": [], "encrypted_content": "wibble"}},
            ]
            (day / f"rollout-2026-01-02T00-00-00-{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in codex_lines) + "\n")

            db_dir = Path(temporary) / ".local" / "share" / "opencode"
            db_dir.mkdir(parents=True)
            conn = sqlite3.connect(db_dir / "opencode.db")
            conn.executescript(
                "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);"
                "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT);"
                "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
            )
            conn.execute("INSERT INTO session VALUES ('ses_abc12345', '/code/demo')")
            conn.execute("INSERT INTO message VALUES ('m1', 'ses_abc12345', ?)", (json.dumps({"role": "assistant"}),))
            conn.execute("INSERT INTO part VALUES ('p1', 'm1', 'ses_abc12345', 1767312004000, ?)",
                         (json.dumps({"type": "reasoning", "text": "the wibble is opencode's"}),))
            conn.commit()
            conn.close()

            hermes = Path(temporary) / ".hermes"
            hermes.mkdir()
            conn = sqlite3.connect(hermes / "state.db")
            conn.executescript(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, cwd TEXT);"
                "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,"
                " content TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, reasoning TEXT);"
            )
            conn.execute("INSERT INTO sessions VALUES ('20260102_abc', 'cli', '/code/demo')")
            conn.execute("INSERT INTO messages (session_id, role, content, timestamp, reasoning) VALUES"
                         " ('20260102_abc', 'assistant', 'done', 1767312005.0, 'the wibble is hermes''s')")
            conn.commit()
            conn.close()

            hidden = self.run_cli("search-clanker-history", "wibble", config_dir=config_dir, home=temporary)
            shown = self.run_cli("search-clanker-history", "wibble", "--thinking", config_dir=config_dir, home=temporary)

        self.assertIn("No matches found.", hidden.stdout)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        for agent in ("claude", "codex", "opencode", "hermes"):
            self.assertIn(f"[thinking] the wibble is {agent}'s", shown.stdout)
        self.assertIn("4 match(es)", shown.stdout)

    def test_hermes_database_is_searched(self):
        with tempfile.TemporaryDirectory() as temporary:
            hermes = Path(temporary) / ".hermes"
            hermes.mkdir()
            conn = sqlite3.connect(hermes / "state.db")
            conn.executescript(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, cwd TEXT);"
                "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,"
                " role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL);"
            )
            conn.execute("INSERT INTO sessions VALUES ('20260102_abc', 'cli', NULL)")
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES"
                " ('20260102_abc', 'user', 'locate the blorpify helper', 1767323045.0)"
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES"
                " ('20260102_abc', 'assistant', 'blorpify is in utils', 1767323046.0)"
            )
            conn.commit()
            conn.close()

            found = self.run_cli("search-clanker-history", "locate the blorpify", "-A", "1", home=temporary)
            other = self.run_cli("search-clanker-history", "blorpify", "--agent", "codex", home=temporary)
            thinking = self.run_cli("search-clanker-history", "blorpify is", "--thinking", home=temporary)

        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("| hermes | cli | session:20260102", found.stdout)
        self.assertIn("blorpify is in utils", found.stdout)
        self.assertEqual(other.returncode, 1)
        self.assertIn("no codex history found", other.stderr)
        self.assertEqual(thinking.stderr, "")  # databases without a reasoning column still search
        self.assertIn("blorpify is in utils", thinking.stdout)

    def write_sessions(self, config_dir, count):
        project = config_dir / "projects" / "-code-demo"
        project.mkdir(parents=True)
        for n in range(count):
            lines = [
                record("user", f"filler before {n} " + "x" * 200_000, f"2026-01-0{n + 1}T00:00:00Z"),
                record("user", f"needle in session {n}", f"2026-01-0{n + 1}T00:00:01Z"),
                {**record("assistant", "", f"2026-01-0{n + 1}T00:00:02Z"),
                 "message": {"role": "assistant", "content": [{"type": "text", "text": f"reply {n}"}]}},
            ]
            path = project / f"0123abcd-0000-0000-0000-00000000000{n}.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in lines) + "\n")
            modified = 1_767_225_600 + n * 86_400 + 3  # just after the last message, as a real session file is
            os.utime(path, (modified, modified))

    def test_limit_keeps_the_most_recent_matches_in_chronological_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            self.write_sessions(config_dir, 4)
            found = self.run_cli("search-clanker-history", "needle", "-n", "2", config_dir=config_dir, home=temporary)

        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertNotIn("session 0", found.stdout)
        self.assertNotIn("session 1", found.stdout)
        self.assertLess(found.stdout.index("session 2"), found.stdout.index("session 3"))
        self.assertIn("2 match(es)", found.stdout)

    def test_context_lines_are_read_around_the_match_offset(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            self.write_sessions(config_dir, 1)
            found = self.run_cli("search-clanker-history", "needle", "-B", "1", "-A", "1", "-C", "30", config_dir=config_dir, home=temporary)

        self.assertIn("filler before 0", found.stdout)
        self.assertIn("reply 0", found.stdout)

    def test_context_counts_visible_messages_and_marks_the_match(self):
        tool = {**record("assistant", "", "2026-01-02T00:00:01Z"),
                "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            project = config_dir / "projects" / "-code-demo"
            project.mkdir(parents=True)
            lines = [
                record("user", "earlier question", "2026-01-02T00:00:00Z"),
                *[tool] * 3,
                record("user", "where is the needle", "2026-01-02T00:00:02Z"),
                *[tool] * 3,
                {**record("assistant", "", "2026-01-02T00:00:03Z"),
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "the answer"}]}},
            ]
            (project / "0123abcd-0000-0000-0000-000000000000.jsonl").write_text(
                "\n".join(json.dumps(item) for item in lines) + "\n"
            )
            found = self.run_cli("search-clanker-history", "needle", "-B", "1", "-A", "1", config_dir=config_dir, home=temporary)
            plain = self.run_cli("search-clanker-history", "needle", config_dir=config_dir, home=temporary)

        self.assertEqual(
            [line for line in found.stdout.splitlines() if line[2:3] == "["],
            ["  [user] earlier question", "> [user] where is the needle", "  [assistant] the answer"],
        )
        self.assertIn("  [user] where is the needle", plain.stdout)

    def test_regex_match_is_shown_and_case_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            project = config_dir / "projects" / "-code-demo"
            project.mkdir(parents=True)
            text = "x" * 5000 + " the Deadline is Friday " + "y" * 5000
            (project / "0123abcd-0000-0000-0000-000000000000.jsonl").write_text(
                json.dumps(record("user", text, "2026-01-02T00:00:00Z")) + "\n"
            )
            found = self.run_cli("search-clanker-history", "-i", "deadline|due by", "-C", "20", config_dir=config_dir, home=temporary)
            whole = self.run_cli("search-clanker-history", "deadline|due by", "-C", "0", config_dir=config_dir, home=temporary)

        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("the Deadline is Friday", found.stdout)
        self.assertLess(len(found.stdout), 500)
        self.assertIn(text, whole.stdout)

    def test_chat_logs_are_searched_in_text_and_jsonl_form(self):
        messages = [
            ("2026-03-01T09:00:00-05:00", "Ann Lee", "lunch at noon?"),
            ("2026-03-01T09:05:00-05:00", "Bot", "noon works.\nSee you there"),
            ("2026-03-02T10:00:00-05:00", "Ann Lee", "the deadline moved to Friday"),
            ("2026-03-03T11:00:00-05:00", "Bot", "Ann Lee: noted, Friday it is"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            text_log = Path(temporary) / "chat.txt"
            text_log.write_text("".join(
                f"{sent[:10]} {sent[11:16]} {sender}: {text.replace(chr(10), chr(0x23CE))}\n"
                for sent, sender, text in messages
            ))
            json_log = Path(temporary) / "chat.jsonl"
            json_log.write_text("".join(
                json.dumps({"sent": sent, "sender": sender, "text": text, "attachments": []}) + "\n"
                for sent, sender, text in messages
            ))
            for log in (text_log, json_log):
                with self.subTest(log=log.name):
                    sender = self.run_cli("search-clanker-history", "^Ann Lee:", "--file", str(log), home=temporary, tz="EST5")
                    self.assertEqual(sender.returncode, 0, sender.stderr)
                    self.assertIn("2 match(es)", sender.stdout)
                    self.assertIn(f"── 2026-03-01 09:00 | {log.name}:1 ──", sender.stdout)
                    self.assertNotIn("noted", sender.stdout)

                    context = self.run_cli("search-clanker-history", "deadline", "--file", str(log), "-B", "1", "-A", "1", home=temporary)
                    self.assertIn("  Bot: noon works. See you there", context.stdout)
                    self.assertIn("> Ann Lee: the deadline moved to Friday", context.stdout)
                    self.assertIn("  Bot: Ann Lee: noted, Friday it is", context.stdout)

                    recent = self.run_cli("search-clanker-history", "noon|friday", "--file", str(log), "-n", "1", "--since", "2026-03-02", home=temporary)
                    self.assertIn("1 match(es)", recent.stdout)
                    self.assertIn("noted", recent.stdout)

            agent = self.run_cli("search-clanker-history", "noon", "--file", str(text_log), "--agent", "claude", home=temporary)
            missing = self.run_cli("search-clanker-history", "noon", "--file", str(Path(temporary) / "nope.txt"), home=temporary)
        self.assertEqual(agent.returncode, 2)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no such file", missing.stderr)

    def test_session_filter_and_local_time_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            self.write_sessions(config_dir, 3)
            one = self.run_cli("search-clanker-history", "needle", "-s", "0123ABCD-0000-0000-0000-000000000001", config_dir=config_dir, home=temporary)
            prefix = self.run_cli("search-clanker-history", "needle", "--session", "0123abcd", config_dir=config_dir, home=temporary)
            toronto = self.run_cli("search-clanker-history", "needle in session 1", config_dir=config_dir, home=temporary, tz="EST5")

        self.assertIn("1 match(es)", one.stdout)
        self.assertIn("needle in session 1", one.stdout)
        self.assertIn("3 match(es)", prefix.stdout)
        self.assertIn("── 2026-01-01 19:00 | claude |", toronto.stdout)

    def test_after_starts_at_the_latest_matching_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            self.write_sessions(config_dir, 3)
            found = self.run_cli("search-clanker-history", "--after", "needle in session [01]", config_dir=config_dir, home=temporary)
            replies = self.run_cli("search-clanker-history", "reply", "--after", "needle in session 0", config_dir=config_dir, home=temporary)
            missing = self.run_cli("search-clanker-history", "--after", "no such words", config_dir=config_dir, home=temporary)

        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertNotIn("session 0", found.stdout)
        self.assertIn("needle in session 1", found.stdout)
        self.assertIn("reply 1", found.stdout)
        self.assertIn("needle in session 2", found.stdout)
        self.assertIn("reply 0", replies.stdout)
        self.assertIn("3 match(es)", replies.stdout)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no message matches --after", missing.stderr)

    def test_json_prints_one_object_per_match_with_whole_texts(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            self.write_sessions(config_dir, 1)
            found = self.run_cli("search-clanker-history", "filler", "--json", "-A", "1", config_dir=config_dir, home=temporary)

        self.assertEqual(found.returncode, 0, found.stderr)
        lines = found.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        hit = json.loads(lines[0])
        self.assertEqual(hit["time"], "2026-01-01T00:00:00+00:00")
        self.assertEqual((hit["agent"], hit["project"], hit["kind"], hit["role"]), ("claude", "-code-demo", "user", "user"))
        self.assertEqual(hit["session"], "0123abcd-0000-0000-0000-000000000000")
        self.assertEqual(len(hit["text"]), len("filler before 0 ") + 200_000)
        self.assertEqual(hit["after"], [{"kind": "user", "role": "user", "text": "needle in session 0"}])

    def test_since_skips_sessions_last_modified_earlier(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            self.write_sessions(config_dir, 3)
            found = self.run_cli("search-clanker-history", "needle", "--since", "2026-01-01T12:00:00Z", config_dir=config_dir, home=temporary)

        self.assertNotIn("session 0", found.stdout)
        self.assertIn("session 1", found.stdout)
        self.assertIn("session 2", found.stdout)

    def test_pattern_rejected_by_ripgrep_falls_back_to_the_python_scanner(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            self.write_sessions(config_dir, 2)
            found = self.run_cli("search-clanker-history", "needle(?= in session 1)", config_dir=config_dir, home=temporary)

        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("needle in session 1", found.stdout)
        self.assertNotIn("session 0", found.stdout)


class ConfigPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "cli_under_test", Path(__file__).resolve().parent.parent / "src" / "search_clanker_history" / "cli.py")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def config_path(self, platform, **env):
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(self.cli.sys, "platform", platform), \
                mock.patch.object(self.cli.Path, "home", return_value=Path("/home/user")):
            return self.cli._config_path().as_posix()

    def test_config_lives_where_each_platform_expects_it(self):
        self.assertEqual(self.config_path("linux"), "/home/user/.config/search-history/config.json")
        self.assertEqual(self.config_path("darwin", XDG_CONFIG_HOME="/x"), "/x/search-history/config.json")
        self.assertEqual(self.config_path("linux", XDG_CONFIG_HOME=""), "/home/user/.config/search-history/config.json")
        self.assertEqual(self.config_path("win32", APPDATA="/appdata"), "/appdata/search-history/config.json")
        self.assertEqual(self.config_path("win32"), "/home/user/AppData/Roaming/search-history/config.json")


if __name__ == "__main__":
    unittest.main()
