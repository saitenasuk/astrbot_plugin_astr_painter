"""端到端：用真实 httpx 打本地假接口，跑通文生图与图生图。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from painter.client import ImagesClient
from painter.config import Settings
from painter.references import ReferenceImage
from tests.conftest import make_png_bytes

RECEIVED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # noqa: D401 - 静默
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        RECEIVED.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
                "content_type": self.headers.get("Content-Type", ""),
            }
        )
        payload = json.dumps({"data": [{"b64_json": _PNG_B64}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


_PNG_B64 = __import__("base64").b64encode(make_png_bytes((8, 8))).decode()


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()
    thread.join(timeout=5)


async def test_generate_round_trip(server):
    RECEIVED.clear()
    settings = Settings(base_url=server, api_key="sk-e2e-1234567890", model="gpt-image-1")
    gen = ImagesClient(settings)
    try:
        image = await gen.generate("赛博猫", size="1024x1024")
    finally:
        await gen.aclose()

    assert image.data.startswith(b"\x89PNG")
    assert RECEIVED[0]["path"] == "/v1/images/generations"
    assert RECEIVED[0]["auth"] == "Bearer sk-e2e-1234567890"
    assert "赛博猫" in json.loads(RECEIVED[0]["body"])["prompt"]


async def test_edit_round_trip(server):
    RECEIVED.clear()
    settings = Settings(base_url=server, api_key="sk-e2e-1234567890", model="gpt-image-1")
    gen = ImagesClient(settings)
    ref = ReferenceImage(make_png_bytes(), "r.png", "image/png")
    try:
        image = await gen.edit("改成夜晚", [ref])
    finally:
        await gen.aclose()

    assert image.data.startswith(b"\x89PNG")
    assert RECEIVED[0]["path"] == "/v1/images/edits"
    assert RECEIVED[0]["content_type"].startswith("multipart/form-data")
    assert b'name="image[]"' in RECEIVED[0]["body"]
