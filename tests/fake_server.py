"""A tiny local HTTP server that plays back a script of responses and records requests."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeAPI:
    def __init__(self, script):
        """``script``: list of (status, headers, body_bytes_or_dict, delay_seconds). The last item repeats."""
        self.script = list(script)
        self.requests = []  # dicts: path, headers, json
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep test output quiet
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                body = self.rfile.read(n)
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "json": json.loads(body or b"null")})  # fmt: skip
                item = outer.script.pop(0) if len(outer.script) > 1 else outer.script[0]
                status, headers, payload, delay = (list(item) + [0])[:4] if len(item) == 3 else item
                if delay:
                    time.sleep(delay)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def ok(text):
    return (200, {}, {"id": "msg_x", "type": "message", "role": "assistant",
                      "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}, 0)  # fmt: skip
