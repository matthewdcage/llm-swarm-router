#!/usr/bin/env python3
"""Serve MediaWiki API HTML dumps where body text was stored HTML-escaped."""

from __future__ import annotations

import html
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

ROOT = os.environ.get(
    "WIKI_HTML_ROOT",
    "/mnt/tnas/Website Backups/soyjakwiki.org/html",
)
PORT = int(os.environ.get("WIKI_VIEWER_PORT", "18765"))

_DOC_WRAPPER = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 52rem; margin: 1.5rem auto; padding: 0 1rem; line-height: 1.55; }}
    .mw-parser-output img {{ max-width: 100%; height: auto; }}
    a {{ color: #0645ad; }}
    nav {{ margin-bottom: 1rem; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <nav><a href="/Main%20Page.html">Main Page</a> · <a href="/">All pages</a></nav>
  {body}
</body>
</html>
"""


def _render_page(path: str, raw: str) -> bytes:
    body = raw
    if body.lstrip().startswith("&lt;"):
        body = html.unescape(body)
    lower = body.lstrip().lower()
    if not lower.startswith("<!doctype") and "<html" not in lower[:300]:
        title = os.path.basename(path).replace(".html", "")
        body = _DOC_WRAPPER.format(title=html.escape(title), body=body)
    return body.encode("utf-8")


def _list_pages() -> bytes:
    names = sorted(f for f in os.listdir(ROOT) if f.endswith(".html"))
    items = "\n".join(
        f'<li><a href="/{html.escape(n)}">{html.escape(n[:-5])}</a></li>'
        for n in names[:500]
    )
    more = ""
    if len(names) > 500:
        more = f"<p>… and {len(names) - 500} more (open files under html/)</p>"
    body = f"<h1>Soyjak Wiki archive</h1><p>{len(names)} pages</p><ul>{items}</ul>{more}"
    return _DOC_WRAPPER.format(title="Index", body=body).encode("utf-8")


class WikiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        rel = unquote(self.path.split("?", 1)[0]).lstrip("/")
        if rel in ("", "index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_list_pages())
            return

        fp = os.path.join(ROOT, rel)
        if not os.path.isfile(fp):
            self.send_error(404, f"Not found: {rel}")
            return

        with open(fp, encoding="utf-8") as f:
            raw = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_render_page(rel, raw))


def main() -> None:
    if not os.path.isdir(ROOT):
        sys.exit(f"Missing WIKI_HTML_ROOT: {ROOT}")
    server = HTTPServer(("127.0.0.1", PORT), WikiHandler)
    print(f"Serving {ROOT} on http://127.0.0.1:{PORT}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
