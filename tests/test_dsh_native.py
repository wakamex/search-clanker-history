"""Search a session the installed DeepSeek Harness writes, against a loopback model server.

Set SCH_TEST_DSH_BIN to a dsh binary to run it, for example SCH_TEST_DSH_BIN=$(command -v dsh).
dsh runs in a temporary DSH_HOME and calls a local server that answers in Anthropic's Messages
streaming format, so no key or quota is used. The model first thinks and calls the bash tool, then
answers after seeing the tool's output.
"""

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DSH = os.environ.get("SCH_TEST_DSH_BIN")


def _block(index, start, delta):
    return [
        ("content_block_start", {"type": "content_block_start", "index": index, "content_block": start}),
        ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


class _ModelServer(BaseHTTPRequestHandler):
    """Think and call bash on the first request that offers tools; answer in text otherwise."""

    tool_calls = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if payload.get("tools") and _ModelServer.tool_calls == 0:
            _ModelServer.tool_calls += 1
            blocks = _block(0, {"type": "thinking", "thinking": "", "signature": ""},
                            {"type": "thinking_delta", "thinking": "the zibble may be in utils"})
            blocks += _block(1, {"type": "tool_use", "id": "tool-1", "name": "bash", "input": {}}, {
                "type": "input_json_delta",
                "partial_json": json.dumps({"command": "echo zibble-tool-output", "description": "look for the zibble"}),
            })
            stop = "tool_use"
        else:
            blocks = _block(0, {"type": "text", "text": ""}, {"type": "text_delta", "text": "the zibble is in utils"})
            stop = "end_turn"
        events = [("message_start", {"type": "message_start", "message": {
            "id": "msg", "type": "message", "role": "assistant", "model": payload.get("model"), "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0},
        }})] + blocks + [
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                               "usage": {"output_tokens": 4}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for name, event in events:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(event)}\n\n".encode())


@unittest.skipUnless(DSH, "set SCH_TEST_DSH_BIN to a dsh binary")
class NativeDshTests(unittest.TestCase):
    def test_a_real_dsh_session_is_searched(self):
        _ModelServer.tool_calls = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelServer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as temporary:
            home, work = Path(temporary) / "dsh-home", Path(temporary) / "work"
            home.mkdir()
            work.mkdir()
            (home / "settings.yaml").write_text(
                f"llm-deepseek:\n  apiKeyEnv: SCH_TEST_DSH_KEY\n  baseURL: http://127.0.0.1:{server.server_port}\n"
            )
            env = {k: v for k, v in os.environ.items() if not k.startswith(("DEEPSEEK", "DSH_"))}
            env.update(DSH_HOME=str(home), SCH_TEST_DSH_KEY="local-test-key", HOME=temporary, NO_COLOR="1")
            run = subprocess.run([DSH, "headless", "find the zibble"], cwd=work, env=env,
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(_ModelServer.tool_calls, 1, "dsh never offered the model its tools")

            def sch(*args):
                found = subprocess.run(["search-clanker-history", *args, "--agent", "dsh", "-C", "0"],
                                       env=env, capture_output=True, text=True, check=False)
                self.assertEqual(found.returncode, 0, found.stderr)
                return found.stdout

            plain = sch("zibble")
            everything = sch("zibble", "--thinking", "--tools")
            injected = sch("runtime context|skill")

        self.assertIn(f"| dsh | {str(work).replace('/', '-')} | session:", plain)
        self.assertIn("[user] find the zibble", plain)
        self.assertIn("[assistant] the zibble is in utils", plain)
        self.assertIn("2 match(es)", plain)
        self.assertIn("[thinking] the zibble may be in utils", everything)
        self.assertIn('[assistant] [bash: {"command": "echo zibble-tool-output"', everything)
        self.assertIn("[tool] zibble-tool-output", everything)
        self.assertIn("5 match(es)", everything)
        self.assertIn("No matches found.", injected)


if __name__ == "__main__":
    unittest.main()
