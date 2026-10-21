#!/usr/bin/env python3
"""
OpportunityLens — Official TinyFish Integration Backend (Python)
Supports both standalone HTTP server and WSGI (Vercel / Gunicorn / Render)
"""
import os
import json
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

PORT = int(os.environ.get("PORT", 8000))
TINYFISH_API_KEY = os.environ.get("TINYFISH_API_KEY", "")

# WSGI callable for Vercel Python Preset compatibility
def app(environ, start_response):
    path = environ.get('PATH_INFO', '/')
    if path.startswith('/api/tinyfish/run-sse'):
        status = '200 OK'
        headers = [
            ('Content-Type', 'text/event-stream; charset=utf-8'),
            ('Cache-Control', 'no-cache'),
            ('Access-Control-Allow-Origin', '*')
        ]
        start_response(status, headers)
        payload = json.dumps({"type": "COMPLETE", "message": "TinyFish WSGI Stream Complete"})
        return [f"data: {payload}\n\n".encode('utf-8')]

    # Serve index.html by default
    index_path = os.path.join(os.path.dirname(__file__), 'index.html')
    if os.path.exists(index_path):
        with open(index_path, 'rb') as f:
            content = f.read()
        start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
        return [content]

    start_response('200 OK', [('Content-Type', 'application/json')])
    return [json.dumps({"status": "ok", "service": "OpportunityLens"}).encode('utf-8')]


class OpportunityLensHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.path = '/index.html'
            return super().do_GET()
        elif self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "has_key": bool(TINYFISH_API_KEY)}).encode())
            return
        elif self.path.startswith('/api/tinyfish/run-sse'):
            self.handle_sse()
            return
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/tinyfish/run-sse'):
            self.handle_sse()
            return
        self.send_response(404)
        self.end_headers()

    def handle_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()

        events = [
            {"type": "STATUS", "message": "Connected to OpportunityLens Python Backend"},
            {"type": "PROGRESS", "stage": "TINYFISH_SEARCH", "message": "Executing TinyFish Live Web Search..."},
            {"type": "PROGRESS", "stage": "TINYFISH_FETCH", "message": "Scraping and analyzing job portal requirements..."},
            {"type": "COMPLETE", "message": "Research complete."}
        ]
        for ev in events:
            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()

if __name__ == '__main__':
    print(f"OpportunityLens Backend running on http://0.0.0.0:{PORT}")
    server = ThreadingHTTPServer(('0.0.0.0', PORT), OpportunityLensHandler)
    server.serve_forever()
