import base64

import httpx
import pytest

from painter.client import ImagesClient
from painter.config import Settings
from painter.errors import PainterError
from painter.references import ReferenceImage
from tests.conftest import make_png_bytes

PROMPT = "一只赛博猫"


def settings(**overrides) -> Settings:
    base = {
        "base_url": "https://api.test/v1",
        "api_key": "sk-test-1234567890",
        "model": "gpt-image-1",
    }
    base.update(overrides)
    return Settings(**base)


def client(handler, **overrides) -> ImagesClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return ImagesClient(settings(**overrides), http_client=http)


def reference() -> ReferenceImage:
    return ReferenceImage(make_png_bytes(), "ref.png", "image/png")


def image_response(data: bytes) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(data).decode()}]})


async def test_generate_sends_json_and_decodes_base64():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.read().decode()
        return image_response(make_png_bytes())

    gen = client(handler)
    try:
        image = await gen.generate(PROMPT, size="1024x1024", quality="high", output_format="png")
    finally:
        await gen.aclose()

    assert image.ext == "png"
    assert image.data.startswith(b"\x89PNG")
    assert seen["url"] == "https://api.test/v1/images/generations"
    assert seen["auth"] == "Bearer sk-test-1234567890"


async def test_generate_omits_auto_fields():
    body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body.update(httpx.Response(200, json={}).json() or {})
        body["raw"] = request.read().decode()
        return image_response(make_png_bytes())

    gen = client(handler)
    try:
        await gen.generate(PROMPT, size="auto", quality="auto", background="auto")
    finally:
        await gen.aclose()

    assert '"size"' not in body["raw"]
    assert '"quality"' not in body["raw"]
    assert '"background"' not in body["raw"]
    assert '"n": 1' in body["raw"].replace('"n":1', '"n": 1')


async def test_edit_uses_bracket_field_for_multiple_images():
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read().decode("utf-8", "ignore"))
        return image_response(make_png_bytes())

    gen = client(handler)
    try:
        await gen.edit(PROMPT, [reference(), reference()])
    finally:
        await gen.aclose()

    assert 'name="image[]"' in bodies[0]
    assert 'name="model"' in bodies[0]


async def test_edit_retries_with_single_field_on_400():
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8", "ignore")
        attempts.append(body)
        if 'name="image[]"' in body:
            return httpx.Response(400, json={"error": {"message": "unknown field image[]"}})
        return image_response(make_png_bytes())

    gen = client(handler)
    try:
        image = await gen.edit(PROMPT, [reference()])
    finally:
        await gen.aclose()

    assert len(attempts) == 2
    assert image.data.startswith(b"\x89PNG")


async def test_param_error_falls_back_to_minimal_payload_and_remembers():
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        bodies.append(body)
        if '"quality"' in body:
            return httpx.Response(
                400, json={"error": {"message": "unsupported parameter: quality"}}
            )
        return image_response(make_png_bytes())

    gen = client(handler)
    try:
        await gen.generate(PROMPT, quality="high")
        await gen.generate(PROMPT, quality="high")
    finally:
        await gen.aclose()

    assert len(bodies) == 3
    assert '"quality"' not in bodies[-1]


async def test_url_response_is_downloaded():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "https://cdn.test/a.png"}]})
        return httpx.Response(200, content=make_png_bytes())

    gen = client(handler)
    try:
        image = await gen.generate(PROMPT)
    finally:
        await gen.aclose()

    assert image.data.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    ("status", "keyword"),
    [(401, "API Key"), (403, "API Key"), (404, "/v1"), (413, "太大"), (429, "限流"), (500, "上游")],
)
async def test_http_errors_are_translated(status, keyword):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom"}})

    gen = client(handler)
    try:
        with pytest.raises(PainterError) as captured:
            await gen.generate(PROMPT)
    finally:
        await gen.aclose()

    assert keyword in captured.value.user_message
    assert "sk-test" not in captured.value.user_message


async def test_timeout_is_reported():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    gen = client(handler, timeout=30)
    try:
        with pytest.raises(PainterError) as captured:
            await gen.generate(PROMPT)
    finally:
        await gen.aclose()

    assert "超时" in captured.value.user_message
    assert captured.value.retryable is True


async def test_network_error_is_reported():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    gen = client(handler)
    try:
        with pytest.raises(PainterError) as captured:
            await gen.generate(PROMPT)
    finally:
        await gen.aclose()

    assert "无法连接" in captured.value.user_message


async def test_non_json_body_is_reported():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    gen = client(handler)
    try:
        with pytest.raises(PainterError) as captured:
            await gen.generate(PROMPT)
    finally:
        await gen.aclose()

    assert "非 JSON" in captured.value.user_message


async def test_missing_data_is_reported():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    gen = client(handler)
    try:
        with pytest.raises(PainterError) as captured:
            await gen.generate(PROMPT)
    finally:
        await gen.aclose()

    assert "没有返回图片数据" in captured.value.user_message


async def test_endpoint_strips_full_leaf_url():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return image_response(make_png_bytes())

    gen = client(handler, base_url="https://api.test/v1/images/generations/")
    try:
        await gen.generate(PROMPT)
    finally:
        await gen.aclose()

    assert urls == ["https://api.test/v1/images/generations"]


async def test_extra_headers_are_sent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["x"] = request.headers.get("X-Channel")
        return image_response(make_png_bytes())

    gen = client(handler, extra_headers={"X-Channel": "abc"})
    try:
        await gen.generate(PROMPT)
    finally:
        await gen.aclose()

    assert captured["x"] == "abc"


async def test_moderation_sent_for_generate():
    body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body["raw"] = request.read().decode()
        return image_response(make_png_bytes())

    gen = client(handler)
    try:
        await gen.generate(PROMPT, moderation="low")
    finally:
        await gen.aclose()

    assert '"moderation": "low"' in body["raw"] or '"moderation":"low"' in body["raw"]


async def test_moderation_omitted_when_auto():
    body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body["raw"] = request.read().decode()
        return image_response(make_png_bytes())

    gen = client(handler)
    try:
        await gen.generate(PROMPT, moderation="auto")
    finally:
        await gen.aclose()

    assert "moderation" not in body["raw"]


async def test_edit_never_sends_moderation():
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read().decode("utf-8", "ignore"))
        return image_response(make_png_bytes())

    gen = client(handler)
    try:
        await gen.edit(PROMPT, [reference()], moderation="low")
    finally:
        await gen.aclose()

    assert "moderation" not in bodies[0]
