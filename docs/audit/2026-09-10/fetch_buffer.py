from __future__ import annotations
import http.server, threading, os, time
from coderio.tools.web_fetch import WebFetchTool


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2000000")
        self.end_headers()
        self.wfile.write(b"x" * 1100000)
        self.wfile.flush()
        time.sleep(3)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["HTTP_PROXY"] = f"http://127.0.0.1:{server.server_port}"
os.environ["NO_PROXY"] = ""
start = time.monotonic()
# The local server is explicitly used as an HTTP proxy. No public network request is sent.
print(WebFetchTool().run("http://93.184.216.34/audit", timeout=1))
print("elapsed", round(time.monotonic() - start, 2), "server sent 1100000 bytes then paused; cap is 1000000")
server.shutdown()
server.server_close()
