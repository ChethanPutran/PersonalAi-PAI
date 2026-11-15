#!/usr/bin/env python3
"""Test HTTP server for the browser plugin.

Serves known-good pages designed to exercise each capability:

    GET  /test.html       → form (name, email, submit)
    POST /test.html       → confirmation page
    GET  /book.html       → booking search form
    GET  /booked.html?date=…  → confirmation, echoes date back
    GET  /healthz         → "ok"

Run standalone:
    python scripts/test_server.py --port 5555
"""
from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


TEST_FORM = """<!doctype html>
<html><head><title>Test Form</title></head>
<body>
  <h1>Test Form</h1>
  <form method="post" action="/test.html">
    <label>Name <input type="text" id="name" name="name" required></label>
    <label>Email <input type="email" id="email" name="email" required></label>
    <button type="submit" id="submit">Submit</button>
  </form>
</body></html>
"""

CONFIRM = """<!doctype html>
<html><head><title>Thank You</title></head>
<body>
  <h1>Thank you</h1>
  <p>Submission received from <strong>{name}</strong> ({email}).</p>
</body></html>
"""

BOOK_FORM = """<!doctype html>
<html><head><title>Book a slot</title></head>
<body>
  <h1>Book a slot</h1>
  <form method="get" action="/booked.html">
    <label>Date <input type="text" id="date" name="date" required></label>
    <button type="submit" id="search">Search</button>
  </form>
</body></html>
"""

BOOKED = """<!doctype html>
<html><head><title>Booking Confirmed</title></head>
<body>
  <h1>Booking confirmed</h1>
  <p>Your slot for <strong>{date}</strong> is reserved.</p>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/test.html"):
            self._send(200, TEST_FORM)
        elif path == "/book.html":
            self._send(200, BOOK_FORM)
        elif path == "/booked.html":
            qs = parse_qs(parsed.query)
            date = html.escape(qs.get("date", ["<missing>"])[0])
            self._send(200, BOOKED.format(date=date))
        elif path == "/healthz":
            self._send(200, "ok")
        else:
            self._send(404, "<h1>404 Not Found</h1>")

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/test.html":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            params = parse_qs(body)
            name = html.escape(params.get("name", [""])[0])
            email = html.escape(params.get("email", [""])[0])
            self._send(200, CONFIRM.format(name=name, email=email))
        else:
            self._send(404, "<h1>404 Not Found</h1>")

    def _send(self, status: int, body: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args) -> None:   # silence per-request noise
        pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5555)
    args = p.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"test server on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())