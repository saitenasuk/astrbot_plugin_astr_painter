import pytest

from painter.options import (
    LANDSCAPE_SIZE,
    PORTRAIT_SIZE,
    SQUARE_SIZE,
    build_prompt,
    extract_ratio_from_text,
    normalize_ratio,
    parse_inline_options,
    ratio_to_size,
    resolve_size,
)


def test_parse_keeps_plain_prompt_intact():
    options, prompt = parse_inline_options("一只赛博猫, 霓虹窗边")
    assert prompt == "一只赛博猫, 霓虹窗边"
    assert options == parse_inline_options("x")[0].__class__()


def test_parse_extracts_flags_in_both_forms():
    options, prompt = parse_inline_options(
        "猫 --ratio 16:9 --quality=high --style 赛博 --transparent"
    )
    assert prompt == "猫"
    assert options.ratio == "16:9"
    assert options.quality == "high"
    assert options.style == "赛博"
    assert options.transparent is True


def test_parse_accepts_chinese_flags_and_fullwidth_colon():
    options, prompt = parse_inline_options("猫 --比例 16：9 --尺寸 1024×1024 --格式 png")
    assert prompt == "猫"
    assert options.ratio == "16：9"
    assert options.size == "1024×1024"
    assert options.output_format == "png"


def test_parse_price_like_tokens_stay_in_prompt():
    options, prompt = parse_inline_options("价格 3:2 的横幅 --quality high")
    assert prompt == "价格 3:2 的横幅"
    assert options.quality == "high"
    assert options.ratio == "3:2"


def test_parse_unknown_flag_is_kept():
    _options, prompt = parse_inline_options("猫 --foo bar")
    assert "--foo" in prompt and "bar" in prompt


def test_parse_ratio_flag_without_value_falls_back_to_prompt():
    options, prompt = parse_inline_options("猫 --ratio")
    assert prompt == "猫 --ratio"
    assert options.ratio == ""


def test_extract_ratio_from_text():
    assert extract_ratio_from_text("画成 9:16 手机壁纸") == "9:16"
    assert extract_ratio_from_text("随便画点") == ""


def test_normalize_ratio_variants():
    assert normalize_ratio("16：9") == "16:9"
    assert normalize_ratio("16/9") == "16:9"
    with pytest.raises(ValueError):
        normalize_ratio("宽:高")


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        ("1:1", SQUARE_SIZE),
        ("16:9", LANDSCAPE_SIZE),
        ("9:16", PORTRAIT_SIZE),
        ("21:9", LANDSCAPE_SIZE),
        ("9:21", PORTRAIT_SIZE),
    ],
)
def test_ratio_to_size(ratio, expected):
    assert ratio_to_size(ratio) == expected


def test_resolve_size_prefers_explicit_size():
    assert resolve_size("1536x1024", "1:1", "auto") == "1536x1024"
    assert resolve_size("", "16:9", "auto") == LANDSCAPE_SIZE
    assert resolve_size("", "", "1024x1024") == "1024x1024"
    assert resolve_size("auto", "16:9", "1024x1024") == "auto"


def test_resolve_size_normalizes_separator():
    assert resolve_size("1024×1024", "", "auto") == "1024x1024"


def test_resolve_size_rejects_garbage():
    with pytest.raises(ValueError):
        resolve_size("大海", "", "auto")


def test_build_prompt_order():
    assert build_prompt("猫", prefix="jpg 质感", style="anime", suffix="高细节") == (
        "jpg 质感, 猫, anime, 高细节"
    )
    assert build_prompt("猫") == "猫"
