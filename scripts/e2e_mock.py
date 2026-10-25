"""用 mock 服务跑一次真实 HTTP 的文生图 / 图生图，验证端到端链路。

.venv/bin/python -m pytest tests/test_e2e.py      # 由测试自带服务
.venv/bin/python scripts/e2e_mock.py              # 手动跑，打印落盘路径
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from painter.client import ImagesClient  # noqa: E402
from painter.config import Settings  # noqa: E402
from painter.references import normalize_reference  # noqa: E402
from painter.storage import ImageStore  # noqa: E402

PORT = 8137


async def run() -> None:
    settings = Settings(
        base_url=f"http://127.0.0.1:{PORT}/v1",
        api_key="sk-mock-1234567890",
        model="mock-image",
        timeout=30,
    )
    client = ImagesClient(settings)
    store = ImageStore(ROOT / ".e2e-out", max_files=50)
    try:
        text = await client.generate("一只赛博猫", size="1024x512")
        print("文生图:", len(text.data), "bytes ->", store.save(text.data, text.ext))

        source = ROOT / "tests" / "fixtures" / "sample.png"
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            from PIL import Image

            Image.new("RGB", (300, 300), "purple").save(source)
        reference = normalize_reference(source.read_bytes(), source.name, 2048)
        edited = await client.edit("改成夜晚", [reference])
        print("图生图:", len(edited.data), "bytes ->", store.save(edited.data, edited.ext))
    finally:
        await client.aclose()


def main() -> int:
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "mock_images_server.py"), "--port", str(PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(1.0)
        asyncio.run(run())
    finally:
        server.terminate()
        try:
            out = server.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            out = ""
        if out:
            print("--- mock server log ---")
            print(out.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
