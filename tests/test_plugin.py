"""插件层测试：用 tests/stubs 里的 AstrBot 桩驱动真实 main.py。"""

import asyncio

import pytest
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import StarTools
from astrbot_plugin_astr_painter import main

from painter.client import GeneratedImage
from painter.errors import PainterError
from painter.options import LANDSCAPE_SIZE
from tests.conftest import make_png_bytes


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    StarTools._data_root = tmp_path / "plugin_data"
    main.AstrPainterPlugin._kv = {}
    yield


@pytest.fixture
def plugin():
    config = {
        "api": {"base_url": "https://api.test/v1", "api_key": "sk-unit-1234567890", "model": "m"},
    }
    instance = main.AstrPainterPlugin(context=None, config=config)
    yield instance


async def drain(agen):
    return [item async for item in agen]


def fake_image(**kwargs):
    return GeneratedImage(data=make_png_bytes(), ext="png")


async def test_generate_command_queues_and_sends(plugin, monkeypatch):
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", fake_generate)
    event = AstrMessageEvent(message_str="/生图 一只猫 --ratio 16:9")

    results = await drain(plugin.cmd_generate(event))
    assert results[0].text == plugin.settings.pending_message
    for task in list(plugin._tasks):
        await task

    assert calls[0]["size"] == LANDSCAPE_SIZE
    assert calls[0]["prompt"] == "一只猫"
    assert event.sent and event.sent[0].kind == "chain"
    assert isinstance(event.sent[0].chain[-1], Image)


async def test_generate_without_prompt_shows_usage(plugin):
    results = await drain(plugin.cmd_generate(AstrMessageEvent(message_str="/生图")))
    assert "/生图" in results[0].text


async def test_edit_requires_reference(plugin):
    monkeypatch_event = AstrMessageEvent(message_str="/改图 换个背景")
    results = await drain(plugin.cmd_edit(monkeypatch_event))
    assert "没有找到参考图" in results[0].text


async def test_edit_uses_reply_image(plugin, monkeypatch, tmp_path):
    image_path = tmp_path / "ref.png"
    image_path.write_bytes(make_png_bytes())
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", fake_generate)
    messages = [Reply(chain=[Image(str(image_path))]), Plain("/改图 换成夜晚")]
    event = AstrMessageEvent(message_str="/改图 换成夜晚", messages=messages)

    await drain(plugin.cmd_edit(event))
    for task in list(plugin._tasks):
        await task

    assert calls[0]["references"] and calls[0]["references"][0].content_type == "image/png"
    assert event.sent[0].chain[-1].path.endswith(".png")


async def test_blacklisted_user_is_rejected(plugin):
    plugin.settings = main.Settings.from_config(
        {"api": {"api_key": "k"}, "access": {"blacklist": "999"}}
    )
    results = await drain(
        plugin.cmd_generate(AstrMessageEvent(message_str="/生图 猫", sender_id="999"))
    )
    assert "权限" in results[0].text


async def test_whitelist_blocks_outsider(plugin):
    plugin.settings = main.Settings.from_config(
        {"api": {"api_key": "k"}, "access": {"whitelist": "1 2"}}
    )
    results = await drain(
        plugin.cmd_generate(AstrMessageEvent(message_str="/生图 猫", sender_id="3"))
    )
    assert "权限" in results[0].text


async def test_admin_only_blocks_normal_user(plugin):
    plugin.settings = main.Settings.from_config(
        {"api": {"api_key": "k"}, "access": {"admin_only": True}}
    )
    results = await drain(plugin.cmd_generate(AstrMessageEvent(message_str="/生图 猫")))
    assert "管理员" in results[0].text


async def test_daily_quota_blocks_second_call(plugin, monkeypatch):
    async def fake_generate(**kwargs):
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", fake_generate)
    plugin.settings = main.Settings.from_config(
        {
            "api": {"api_key": "k"},
            "quota": {"enabled": True, "daily_limit": 1, "admin_bypass": False},
        }
    )
    event = AstrMessageEvent(message_str="/生图 猫")
    first = await drain(plugin.cmd_generate(event))
    assert first[0].text == plugin.settings.pending_message
    for task in list(plugin._tasks):
        await task

    second = await drain(plugin.cmd_generate(event))
    assert "次数已用完" in second[0].text


async def test_cooldown_blocks_immediate_retry(plugin, monkeypatch):
    async def fake_generate(**kwargs):
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", fake_generate)
    plugin.settings = main.Settings.from_config(
        {"api": {"api_key": "k"}, "quota": {"cooldown_seconds": 60, "admin_bypass": False}}
    )
    event = AstrMessageEvent(message_str="/生图 猫")
    await drain(plugin.cmd_generate(event))
    for task in list(plugin._tasks):
        await task

    second = await drain(plugin.cmd_generate(event))
    assert "太频繁" in second[0].text


async def test_failure_refunds_quota_and_reports(plugin, monkeypatch):
    async def boom(**kwargs):
        raise PainterError("上游炸了")

    monkeypatch.setattr(plugin, "_generate", boom)
    plugin.settings = main.Settings.from_config(
        {
            "api": {"api_key": "k"},
            "quota": {
                "enabled": True,
                "daily_limit": 5,
                "admin_bypass": False,
                "cooldown_seconds": 0,
            },
        }
    )
    event = AstrMessageEvent(message_str="/生图 猫")
    await drain(plugin.cmd_generate(event))
    for task in list(plugin._tasks):
        await task

    assert "生图失败：上游炸了" in event.sent[0].text
    record = await plugin.get_kv_data("quota:10001", {})
    assert record["count"] == 0


async def test_missing_api_key_is_reported(plugin):
    plugin.settings = main.Settings.from_config({})
    results = await drain(plugin.cmd_generate(AstrMessageEvent(message_str="/生图 猫")))
    assert "还没配置" in results[0].text


async def test_llm_tool_reports_missing_prompt(plugin):
    assert "缺少" in await plugin.tool_generate_image(AstrMessageEvent(), prompt=" ")


async def test_llm_tool_submits_and_dedupes(plugin, monkeypatch):
    async def slow(**kwargs):
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", slow)
    event = AstrMessageEvent(message_str="画一只猫")

    first = await plugin.tool_generate_image(event, prompt="猫")
    assert "已提交" in first
    second = await plugin.tool_generate_image(event, prompt="猫")
    assert "已经在生成中" in second

    for task in list(plugin._inflight.values()):
        await task
    assert event.sent


async def test_llm_tool_disabled(plugin):
    plugin.settings = main.Settings.from_config(
        {"api": {"api_key": "k"}, "runtime": {"enable_llm_tool": False}}
    )
    assert "已关闭" in await plugin.tool_generate_image(AstrMessageEvent(), prompt="猫")


async def test_llm_tool_needs_reference_image(plugin):
    reply = await plugin.tool_generate_image(
        AstrMessageEvent(), prompt="改成夜晚", reference_images=True
    )
    assert "没有找到参考图" in reply


async def test_hook_appends_instruction(plugin):
    from astrbot.api.provider import ProviderRequest

    request = ProviderRequest(system_prompt="基础提示")
    await plugin.inject_tool_instruction(AstrMessageEvent(), request)
    assert "generate_image" in request.system_prompt
    assert request.system_prompt.startswith("基础提示")


async def test_hook_silent_when_disabled(plugin):
    from astrbot.api.provider import ProviderRequest

    plugin.settings = main.Settings.from_config({"runtime": {"enable_llm_tool": False}})
    request = ProviderRequest(system_prompt="x")
    await plugin.inject_tool_instruction(AstrMessageEvent(), request)
    assert request.system_prompt == "x"


async def test_help_lists_commands_and_warns_without_key(plugin):
    plugin.settings = main.Settings.from_config({})
    results = await drain(plugin.cmd_help(AstrMessageEvent()))
    assert "/生图" in results[0].text and "/改图" in results[0].text
    assert "API Key" in results[0].text


async def test_walk_images_is_depth_limited():
    deep = [Image("a")]
    for _ in range(3):
        deep = [Reply(chain=deep)]
    assert len(main._walk_images(deep)) == 1
    assert (
        len(
            main._walk_images(
                [Reply(chain=[Reply(chain=[Reply(chain=[Reply(chain=[Image("a")])])])])]
            )
        )
        == 0
    )
    assert main._walk_images(None) == []


def test_strip_command_variants():
    assert main._strip_command("/生图 猫", "生图") == "猫"
    assert main._strip_command("生图", "生图") == ""
    assert main._strip_command("/改图", "改图") == ""


async def test_terminate_cancels_and_closes(plugin, monkeypatch):
    async def slow(**kwargs):
        await asyncio.sleep(30)
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", slow)
    await drain(plugin.cmd_generate(AstrMessageEvent(message_str="/生图 猫")))
    await plugin.terminate()
    assert not plugin._tasks


async def test_storage_prune_runs_after_save(plugin, monkeypatch):
    async def fake_generate(**kwargs):
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", fake_generate)
    plugin.settings = main.Settings.from_config(
        {
            "api": {"api_key": "k"},
            "runtime": {"storage_max_files": 20},
            "quota": {"cooldown_seconds": 0},
        }
    )
    for _ in range(3):
        event = AstrMessageEvent(message_str="/生图 猫")
        await drain(plugin.cmd_generate(event))
        for task in list(plugin._tasks):
            await task
    assert len(plugin._store.list_files()) == 3


async def test_queue_timeout_reports_busy_and_refunds(plugin, monkeypatch):
    async def slow(**kwargs):
        await asyncio.sleep(30)
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", slow)
    plugin.settings = main.Settings.from_config(
        {
            "api": {"api_key": "k"},
            "quota": {
                "enabled": True,
                "daily_limit": 5,
                "admin_bypass": False,
                "cooldown_seconds": 0,
            },
            "runtime": {"max_concurrent": 1, "queue_wait_seconds": 0},
        }
    )
    first = AstrMessageEvent(message_str="/生图 猫")
    await drain(plugin.cmd_generate(first))

    second = AstrMessageEvent(message_str="/生图 狗")
    results = await drain(plugin.cmd_generate(second))
    assert "任务较多" in results[0].text
    assert (await plugin.get_kv_data("quota:10001", {}))["count"] == 1
    await plugin.terminate()


async def test_semaphore_is_not_leaked_after_queue_timeout(plugin, monkeypatch):
    async def slow(**kwargs):
        await asyncio.sleep(30)
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", slow)
    plugin.settings = main.Settings.from_config(
        {"api": {"api_key": "k"}, "runtime": {"max_concurrent": 1, "queue_wait_seconds": 0}}
    )
    await drain(plugin.cmd_generate(AstrMessageEvent(message_str="/生图 猫")))
    for _ in range(3):
        await drain(plugin.cmd_generate(AstrMessageEvent(message_str="/生图 狗")))
    assert plugin._semaphore._value == 0
    await plugin.terminate()
    assert plugin._semaphore._value == 1


async def test_slot_released_when_task_cancelled_before_start(plugin, monkeypatch):
    async def slow(**kwargs):
        await asyncio.sleep(30)
        return fake_image()

    monkeypatch.setattr(plugin, "_generate", slow)
    plugin.settings = main.Settings.from_config(
        {
            "api": {"api_key": "k"},
            "quota": {"cooldown_seconds": 0},
            "runtime": {"max_concurrent": 1},
        }
    )
    await drain(plugin.cmd_generate(AstrMessageEvent(message_str="/生图 猫")))
    task = next(iter(plugin._tasks))
    task.cancel()  # 协程体可能还没开始执行
    await asyncio.gather(task, return_exceptions=True)
    assert plugin._semaphore._value == 1
    assert not plugin._tasks
    await plugin.terminate()
