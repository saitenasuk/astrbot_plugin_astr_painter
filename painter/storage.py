"""生成图落盘与按数量清理。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

_EXT_ALIASES = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp", "gif": "gif"}


def clean_extension(ext: str) -> str:
    value = (ext or "").strip().lstrip(".").lower()
    return _EXT_ALIASES.get(value, "png")


class ImageStore:
    """把生成结果写到 ``data/plugin_data/<plugin>/images/YYYYMMDD/``。"""

    def __init__(self, root: Path, max_files: int = 500) -> None:
        self.root = Path(root)
        self.max_files = max(1, int(max_files))

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, ext: str, *, when: datetime | None = None) -> Path:
        self.ensure_ready()
        day = (when or datetime.now()).strftime("%Y%m%d")
        directory = self.root / day
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid4().hex[:16]}.{clean_extension(ext)}"
        temp_path = path.with_name(path.name + ".part")
        temp_path.write_bytes(data)
        temp_path.replace(path)
        return path

    def list_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return [item for item in self.root.rglob("*") if item.is_file() and item.suffix != ".part"]

    def prune(self) -> int:
        """超出 ``max_files`` 时从最旧的开始删除，返回删除数量。"""
        files = self.list_files()
        if len(files) <= self.max_files:
            return 0
        files.sort(key=_mtime)
        victims = files[: len(files) - self.max_files]
        for path in victims:
            try:
                path.unlink()
            except OSError:
                pass
        for directory in sorted(
            {path.parent for path in victims}, key=lambda p: len(p.parts), reverse=True
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        return len(victims)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
