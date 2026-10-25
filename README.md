# Astr Painter

调用 OpenAI 兼容 Images API 的 AstrBot 文生图 / 图生图插件。

- `/生图 <提示词>`：文生图。
- `/改图 <提示词>`：图生图。同一条消息带图，或回复 / 引用一张图和合并转发都支持，最多取 `image.max_reference_images` 张。
- `generate_image`：注册给 LLM 的工具，聊天里说「画一只赛博猫」就能直接出图，并附带一段教模型何时调用的提示词（可在配置里改）。

接口只走标准 Images API：文生图 `POST {base_url}/images/generations`，图生图 `POST {base_url}/images/edits`，所以官方 OpenAI、各类 OpenAI 兼容中转站、自建 OneAPI / NewAPI 都能直接填。

## 安装

1. 把仓库放进 `AstrBot/data/plugins/`（或从插件市场安装）。
2. 在 WebUI 插件页重载插件。
3. 打开插件配置，至少填 `api.base_url`、`api.api_key`、`api.model`。

依赖只有 `httpx`，AstrBot 会自动按 `requirements.txt` 安装。`Pillow` 若已存在会用上（参考图等比缩放、格式转换），没有也能正常工作。

## 配置

| 配置项 | 说明 |
| --- | --- |
| `api.base_url` | 填到 `/v1` 即可，插件会自动补 `/images/generations` 或 `/images/edits`。也接受直接填完整叶子地址。 |
| `api.api_key` | 供应商 Key，未填时插件不发起请求，指令会提示还没配置。 |
| `api.model` | 按供应商给的模型名填，例如 `gpt-image-1`。 |
| `api.timeout` | 请求超时秒数，默认 180。 |
| `api.proxy` / `api.extra_headers` | 可选。代理地址、自定义请求头。 |
| `image.size` / `quality` / `output_format` / `background` | 默认出图参数；指令里显式传参优先。 |
| `image.moderation` | 内容审核强度，`auto`（默认，不发送该字段）/ `low`（放宽）。**仅文生图生效**，官方 `/images/edits` 没有这个字段，`/改图` 不会发送。 |
| `image.max_reference_images` / `max_side` | 参考图张数上限、参考图最长边（超过会等比缩小）。 |
| `prompt.prefix` / `suffix` | 拼在用户提示词前后的固定文字，不替换、不改写原文。 |
| `access.whitelist` / `blacklist` / `admin_only` | 权限。黑名单优先，白名单留空表示不限制。用户 ID 用换行、逗号或空格分隔。 |
| `quota.enabled` / `daily_limit` / `cooldown_seconds` / `admin_bypass` | 每日次数与冷却。提交任务即计数，生成失败会自动退回。 |
| `runtime.max_concurrent` / `queue_wait_seconds` | 并发上限与排队等待上限，超过时间直接提示繁忙。 |
| `runtime.enable_llm_tool` / `tool_instruction` | 是否允许 LLM 自动出图，以及注入的说明文字。 |
| `runtime.pending_message` | 提交后的即时回复，图片完成后再单独发一条。 |
| `runtime.show_meta` | 是否在图片前附带模型、尺寸、耗时。 |
| `runtime.storage_max_files` | 生成图保留数量，超过从最旧的开始删。 |
| `runtime.debug_log_response` | 排障用，记录接口响应摘要（含提示词）。 |

## 指令参数

```text
/生图 一只赛博猫坐在霓虹窗边 --ratio 16:9 --quality high
/改图 把背景换成夜晚城市，保留人物姿势 --size 1536x1024
/生图 扁平图标，透明背景的蓝色火箭 --transparent --format png
```

- `--ratio`（别名 `--aspect` / `--比例`）：`1:1`、`16:9`、`9:16` 等，映射到最接近的 `1024x1024` / `1536x1024` / `1024x1536`。
- `--size`（别名 `-s` / `--尺寸`）：`1024x1024` 这类 `宽x高` 或 `auto`，优先于 `--ratio`。
- `--quality`（`-q` / `--质量`）、`--format`（`-f` / `--格式`）、`--style`（`--风格`）、`--transparent`（`--透明`）。
- 提示词里的 `16:9` 这类比例会被自动认出；未知的 `--xxx` 原样留在提示词里。

`/生图` 带图时仍是文生图，只在结果里提示「要基于图片修改请用 /改图」；`/改图` 没有参考图时直接回用法，不消耗额度。

## 行为说明

- 指令先回等待文案，生成在后台跑，完成后用 `event.send` 把图片发回原会话——长耗时请求不会卡住消息管道。
- POST 不自动重试，避免超时后重复生成重复扣费；只有下载返回的图片链接会重试一次。
- 接口 400 报参数错误时，会用最小参数集重试一次并记住该结果；图生图遇到参考图字段名报错会自动在 `image` / `image[]` 之间切换并记住。
- 错误文案一律脱敏（Key、Authorization、URL 查询串）。
- 生成图落在 `data/plugin_data/astrbot_plugin_astr_painter/images/YYYYMMDD/`，插件启动和每次出图后按上限裁剪。

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pillow pytest pytest-asyncio ruff astrbot   # astrbot 仅本地冒烟需要

.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m pytest -q           # 单测：解析/客户端/参考图/存储/配置/错误/插件层
.venv/bin/python scripts/e2e_mock.py    # 对本地假接口跑真实 HTTP 全链路
.venv/bin/python scripts/smoke_astrbot.py  # 真 AstrBot 运行时加载 + 跑通指令与工具
```

`scripts/mock_images_server.py` 也可单独起，然后把 `api.base_url` 指向 `http://127.0.0.1:8099/v1` 手工联调。

## 已知边界

- 不支持 Gemini 原生 `generateContent`、chat-completions 透传、ComfyUI、多节点回退、mask 局部重绘、多图批量与视频。
- `n` 固定为 1。
- 冷却记录存在内存里，插件重载后清空。

## 灵感来源

功能设计参考了 [astrbot-plugin-gpt-image](https://github.com/starmiaoa/astrbot-plugin-gpt-image)（命令 + LLM 工具 + 后台投递）与 [astrbot_plugin_omnidraw](https://github.com/diaomin66/astrbot_plugin_omnidraw)（权限、额度、缓存清理的配置分层），实现为独立代码。

## License

AGPL-3.0-or-later，见 [LICENSE](LICENSE)。
