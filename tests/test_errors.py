from pathlib import Path

import pytest

from painter.errors import ConfigError, PainterError, extract_error_detail, map_http_error, sanitize
from painter.storage import ImageStore, clean_extension


def test_sanitize_masks_secrets_headers_and_queries():
    text = (
        "post https://api.test/v1?key=abc&sig=1 "
        "Authorization: Bearer abcdefghijklmnop "
        "key sk-abcdefghijklmnopqrst"
    )
    cleaned = sanitize(text, secrets=("sk-abcdefghijklmnopqrst",))
    assert "sig=1" not in cleaned
    assert "abcdefghijklmnop" not in cleaned.replace("Bearer ***", "")
    assert "sk-abcdefghijklmnopqrst" not in cleaned


def test_sanitize_truncates():
    assert len(sanitize("a" * 500, limit=50)) == 51


def test_extract_error_detail_variants():
    assert extract_error_detail('{"error": {"message": "bad size"}}') == "bad size"
    assert extract_error_detail('{"message": "plain"}') == "plain"
    assert extract_error_detail("not json at all") == "not json at all"
    assert extract_error_detail("") == ""


@pytest.mark.parametrize(
    ("status", "body", "keyword"),
    [
        (401, '{"error":{"message":"x"}}', "API Key"),
        (404, "", "/v1"),
        (400, '{"error":{"message":"unsupported parameter: quality"}}', "参数"),
        (400, '{"error":{"message":"bad thing"}}', "拒绝"),
        (429, "", "限流"),
        (503, "", "上游"),
        (418, "", "HTTP 418"),
    ],
)
def test_map_http_error(status, body, keyword):
    error = map_http_error(status, body)
    assert isinstance(error, PainterError)
    assert keyword in error.user_message


def test_map_http_error_keeps_detail():
    error = map_http_error(400, '{"error":{"message":"invalid value for size"}}')
    assert "invalid value for size" in error.user_message
    assert error.status == 400


def test_config_error_is_painter_error():
    error = ConfigError("缺配置")
    assert isinstance(error, PainterError)
    assert error.user_message == "缺配置"


def test_clean_extension():
    assert clean_extension("JPEG") == "jpg"
    assert clean_extension(".webp") == "webp"
    assert clean_extension("tiff") == "png"


def test_store_save_and_prune(tmp_path: Path):
    store = ImageStore(tmp_path, max_files=3)
    saved = [store.save(b"x", "png") for _ in range(5)]
    assert all(path.exists() for path in saved)
    assert store.prune() == 2
    assert len(store.list_files()) == 3
    assert not saved[0].exists()


def test_store_keeps_temp_suffix_ignored(tmp_path: Path):
    store = ImageStore(tmp_path, max_files=10)
    store.ensure_ready()
    stale = tmp_path / "half.png.part"
    stale.write_bytes(b"x")
    assert store.list_files() == []
