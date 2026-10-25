import pytest

from painter.config import Settings


def test_defaults_are_sane():
    settings = Settings.from_config({})
    assert settings.base_url == "https://api.openai.com/v1"
    assert settings.model == "gpt-image-1"
    assert settings.max_concurrent == 1
    assert settings.output_format == "png"


def test_from_config_reads_nested_sections():
    settings = Settings.from_config(
        {
            "api": {"base_url": "https://x.test/v1/", "api_key": " k ", "model": " m "},
            "image": {"size": "1536x1024", "quality": "high"},
            "quota": {"enabled": True, "daily_limit": 3},
            "access": {"whitelist": "1,2 3\n4"},
        }
    )
    assert settings.base_url == "https://x.test/v1/"
    assert settings.api_key == "k"
    assert settings.size == "1536x1024"
    assert settings.daily_limit == 3
    assert settings.whitelist == frozenset({"1", "2", "3", "4"})


@pytest.mark.parametrize("value", ["", None, 0])
def test_empty_values_fall_back(value):
    settings = Settings.from_config({"api": {"base_url": value, "model": value}})
    assert settings.base_url == "https://api.openai.com/v1"
    assert settings.model == "gpt-image-1"


def test_bad_url_is_reported_not_replaced():
    settings = Settings.from_config({"api": {"base_url": "abc"}})
    assert settings.base_url == "abc"
    assert any("http" in problem for problem in settings.validate())


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("max_concurrent", 99, 8),
        ("max_concurrent", 0, 1),
        ("timeout", 1, 10),
        ("storage_max_files", 1, 20),
    ],
)
def test_numbers_are_clamped(key, value, expected):
    settings = Settings.from_config({"runtime": {key: value}, "api": {key: value}})
    assert getattr(settings, key) == expected


def test_unknown_choices_fall_back():
    settings = Settings.from_config({"image": {"quality": "ultra", "size": "999x999"}})
    assert settings.quality == "auto"
    assert settings.size == "auto"


def test_non_mapping_config_is_tolerated():
    assert Settings.from_config(None).model == "gpt-image-1"
    assert Settings.from_config({"api": "nope"}).model == "gpt-image-1"


def test_bools_accept_strings():
    settings = Settings.from_config({"quota": {"enabled": "true"}, "access": {"admin_only": "否"}})
    assert settings.quota_enabled is True
    assert settings.admin_only is False


def test_endpoint_builds_and_strips():
    settings = Settings.from_config({"api": {"base_url": "https://x.test/v1/images/edits"}})
    assert settings.endpoint("generate") == "https://x.test/v1/images/generations"
    assert settings.endpoint("edit") == "https://x.test/v1/images/edits"


def test_validate_reports_missing_key():
    problems = Settings.from_config({}).validate()
    assert any("API Key" in problem for problem in problems)


def test_validate_passes_with_key():
    assert Settings.from_config({"api": {"api_key": "sk-x"}}).validate() == []


def test_pending_message_has_fallback():
    settings = Settings.from_config({"runtime": {"pending_message": "   "}})
    assert settings.pending_message == "🎨 收到，正在绘制…"


def test_moderation_default_and_choice():
    assert Settings.from_config({}).moderation == "auto"
    assert Settings.from_config({"image": {"moderation": "low"}}).moderation == "low"
    assert Settings.from_config({"image": {"moderation": "high"}}).moderation == "auto"
