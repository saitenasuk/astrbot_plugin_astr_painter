#!/usr/bin/env python3
"""本地假的 OpenAI Images 接口，用来在无真实 Key 的情况下联调插件。

    python scripts/mock_images_server.py --port 8099

然后把插件配置的 api.base_url 改成 http://127.0.0.1:8099/v1，随便填一个 Key 即可。
生成的图片是一张带随机色块的 PNG，方便肉眼确认链路通不通。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw


def render_png(width: int = 512, height: int = 512) -> bytes:
    image = Image.new("RGB", (width, height), (24, 24, 32))
    draw = ImageDraw.Draw(image)
    for index in range(0, width, 32):
        draw.rectangle([index, 0, index + 16, height], fill=(index % 255, 80, 160))
    draw.text((16, 16), "astr_painter mock", fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


DEFAULT_SIZE = (512, 512)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"  {self.command} {self.path} -> {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        body = render_png()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self._json(401, {"error": {"message": "missing api key"}})
            return
        if not self.path.startswith("/v1/"):
            self._json(404, {"error": {"message": f"unknown path {self.path}"}})
            return
        if self.path.endswith("/images/generations"):
            prompt = self._json_prompt(raw)
            size = self._json_size(raw)
        elif self.path.endswith("/images/edits"):
            prompt = "(multipart edit)"
            size = DEFAULT_SIZE
        else:
            self._json(404, {"error": {"message": f"unknown path {self.path}"}})
            return

        print(f"    prompt={prompt!r} size={size}")
        payload = {
            "data": [
                {
                    "b64_json": base64.b64encode(render_png(*size)).decode(),
                    "revised_prompt": prompt,
                }
            ]
        }
        self._json(200, payload)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _json_prompt(raw: bytes) -> str:
        try:
            return str(json.loads(raw).get("prompt", ""))
        except ValueError:
            return ""

    @staticmethod
    def _json_size(raw: bytes) -> tuple[int, int]:
        try:
            value = str(json.loads(raw).get("size", ""))
            width, height = value.split("x")
            return int(width), int(height)
        except (ValueError, TypeError):
            return DEFAULT_SIZE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"mock images API at http://{args.host}:{args.port}/v1 （Ctrl-C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
