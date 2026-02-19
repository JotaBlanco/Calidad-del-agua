#!/usr/bin/env python3
"""Simple HTTP server with no-cache headers."""
import http.server
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

print("Serving on http://localhost:8000 (no-cache)")
http.server.HTTPServer(("", 8000), NoCacheHandler).serve_forever()
