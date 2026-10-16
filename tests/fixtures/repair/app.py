"""Local fixture: deliberately redundant delay, unchanged GET semantics."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time

DELAY_SECONDS = 0.080


def response_for_path(path):
    return (200, b'ok\n') if path == '/' else (404, b'missing\n')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(DELAY_SECONDS)
        status, body = response_for_path(self.path)
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
