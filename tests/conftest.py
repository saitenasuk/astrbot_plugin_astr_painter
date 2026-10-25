"""测试共用夹具：把仓库根目录变成可导入的包，并注入 AstrBot 桩模块。"""

import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT / "tests" / "stubs") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))

# 让测试里的 `painter.x` 与 main.py 里的相对导入指向同一批模块对象：
# 伪造的 `painter` 包必须把子模块也映射到带包名前缀的真实模块对象上。
import importlib  # noqa: E402

_PKG = "astrbot_plugin_astr_painter.painter"
sys.modules.setdefault("painter", importlib.import_module(_PKG))
for _name in ("errors", "config", "options", "client", "references", "storage"):
    sys.modules.setdefault(f"painter.{_name}", importlib.import_module(f"{_PKG}.{_name}"))

from PIL import Image as PILImage  # noqa: E402


def make_png_bytes(size: tuple[int, int] = (32, 32), color: str = "red") -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_jpeg_bytes(size: tuple[int, int] = (32, 32), color: str = "blue") -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def make_png_data_url(size: tuple[int, int] = (16, 16)) -> str:
    return "data:image/png;base64," + base64.b64encode(make_png_bytes(size)).decode()
