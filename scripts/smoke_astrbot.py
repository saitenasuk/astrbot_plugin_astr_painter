"""真实 AstrBot 运行时冒烟测试（不进 CI，需要本地装好 astrbot）。

    .venv/bin/python scripts/smoke_astrbot.py

它做三件事：
1. 用 StarManager 的路径规则真实 ``__import__`` 本插件（验证相对导入、装饰器注册、
   metadata、_conf_schema 解析）。
2. 用真实 ``AstrMessageEvent`` 子类跑 /生图、/改图、/生图帮助 与 LLM 工具。
3. 打本地 mock 接口，确认图片真的落到 data/plugin_data 且消息链里带 Image。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8138

FAILURES: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("  OK   " if ok else "  FAIL ") + name + (f" — {extra}" if extra else ""))
    if not ok:
        FAILURES.append(name)


def bootstrap_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="astr-smoke-"))
    plugins = root / "data" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "astrbot_plugin_astr_painter").symlink_to(ROOT, target_is_directory=True)
    os.environ["ASTRBOT_ROOT"] = str(root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(plugins))
    return root


async def main() -> int:
    bootstrap_root()

    from astrbot.api.provider import ProviderRequest
    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.message.components import Image, Plain, Reply
    from astrbot.core.message.message_event_result import MessageChain
    from astrbot.core.platform import AstrBotMessage, MessageMember, MessageType, PlatformMetadata
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.star.star_manager import PluginManager

    print("1) 真实导入 + 注册")
    module = __import__("data.plugins.astrbot_plugin_astr_painter.main", fromlist=["main"])
    check("模块导入", hasattr(module, "AstrPainterPlugin"))

    schema_path = ROOT / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    check("_conf_schema.json 可解析", isinstance(schema, dict) and "api" in schema)

    config = AstrBotConfig(
        config_path=str(Path(os.environ["ASTRBOT_ROOT"]) / "data" / "plugin.json"),
        schema=schema,
    )
    config["api"]["base_url"] = f"http://127.0.0.1:{PORT}/v1"
    config["api"]["api_key"] = "sk-smoke-1234567890"
    config["api"]["model"] = "mock-image"
    config["runtime"]["pending_message"] = "绘制中"
    config["quota"]["cooldown_seconds"] = 0

    tool_mgr = PluginManager  # 仅用于确认导入可用
    check("PluginManager 可导入", tool_mgr is not None)

    class SmokeEvent(AstrMessageEvent):
        def __init__(self, text, messages):
            message_obj = AstrBotMessage()
            message_obj.type = MessageType.FRIEND_MESSAGE
            message_obj.self_id = "1"
            message_obj.session_id = "10001"
            message_obj.message_id = "m1"
            message_obj.sender = MessageMember(user_id="10001", nickname="tester")
            message_obj.message = messages
            message_obj.message_str = text
            message_obj.raw_message = None
            super().__init__(
                text,
                message_obj,
                PlatformMetadata(name="smoke", description="smoke", id="smoke"),
                "10001",
            )
            self.sent: list[MessageChain] = []

        async def send(self, message: MessageChain) -> None:
            self.sent.append(message)

    plugin = module.AstrPainterPlugin(context=None, config=config)
    await plugin.initialize()

    async def drain(agen):
        return [item async for item in agen]

    async def settle():
        for _ in range(200):
            if not plugin._tasks:
                break
            await asyncio.gather(*list(plugin._tasks), return_exceptions=True)

    print("2) 指令")
    event = SmokeEvent("/生图 一只赛博猫 --ratio 16:9", [Plain("/生图 一只赛博猫 --ratio 16:9")])
    results = await drain(plugin.cmd_generate(event))
    check("返回等待文案", results and results[0].chain[0].text == "绘制中")
    await settle()
    sent = event.sent[-1].chain if event.sent else []
    images = [c for c in sent if isinstance(c, Image)]
    check("图生图完成并发图", bool(images), f"{len(images)} 张")
    if images:
        path = await images[0].convert_to_file_path()
        check("图片已落盘", Path(path).is_file(), path)
        check("落在 plugin_data 下", "plugin_data" in path, path)

    print("3) 图生图")
    src = ROOT / "tests" / "fixtures" / "sample.png"
    reply = Reply(id="m0", chain=[Image.fromFileSystem(str(src))])
    event2 = SmokeEvent("/改图 改成夜晚", [reply, Plain("/改图 改成夜晚")])
    results2 = await drain(plugin.cmd_edit(event2))
    check("有参考图时提交", results2 and results2[0].chain[0].text == "绘制中")
    await settle()
    check("图生图返回图片", any(isinstance(c, Image) for e in event2.sent for c in e.chain))

    print("4) 帮助与边界")
    help_event = SmokeEvent("/生图帮助", [Plain("/生图帮助")])
    help_results = await drain(plugin.cmd_help(help_event))
    check("帮助含两条指令", "/改图" in help_results[0].chain[0].text)

    bare = await drain(plugin.cmd_edit(SmokeEvent("/改图", [Plain("/改图")])))
    check("改图空参提示用法", "用法" in bare[0].chain[0].text)

    empty = await drain(plugin.cmd_edit(SmokeEvent("/改图 换背景", [Plain("/改图 换背景")])))
    check("改图缺参考图有提示", "没有找到参考图" in empty[0].chain[0].text)

    bad = await drain(plugin.cmd_generate(SmokeEvent("/生图 猫 --size 大海", [Plain("x")])))
    check("非法尺寸有提示", "尺寸" in bad[0].chain[0].text)

    print("5) LLM 工具与 hook")
    tool_event = SmokeEvent("画一只猫", [Plain("画一只猫")])
    tool_reply = await plugin.tool_generate_image(tool_event, prompt="猫")
    check("工具受理", "已提交" in tool_reply)
    await settle()
    check("工具出图", any(isinstance(c, Image) for e in tool_event.sent for c in e.chain))

    request = ProviderRequest(prompt="x")
    request.system_prompt = "base"
    await plugin.inject_tool_instruction(SmokeEvent("x", []), request)
    check("hook 注入说明", "generate_image" in request.system_prompt)

    print("6) 上游失败路径")
    plugin.settings = dataclasses.replace(
        plugin.settings, base_url=f"http://127.0.0.1:{PORT}/nope/v1"
    )
    plugin._client = None
    fail_event = SmokeEvent("/生图 猫", [Plain("/生图 猫")])
    await drain(plugin.cmd_generate(fail_event))
    await settle()
    last = next((c.text for e in fail_event.sent for c in e.chain if isinstance(c, Plain)), "")
    check("上游 404 有可读报错", "接口地址或模型名不存在" in last, last[:60])

    await plugin.terminate()
    return 0 if not FAILURES else 1


def run() -> int:
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "mock_images_server.py"), "--port", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    try:
        return asyncio.run(main())
    finally:
        server.terminate()


if __name__ == "__main__":
    code = run()
    print("\nRESULT:", "PASS" if code == 0 else f"FAIL ({len(FAILURES)}): {FAILURES}")
    raise SystemExit(code)
