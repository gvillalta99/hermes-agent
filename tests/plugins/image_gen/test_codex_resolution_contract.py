"""Offline characterization of routing, configuration and profile boundaries.

No real credential readers or external generation calls are allowed here.
"""
import importlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from agent import image_gen_registry
from hermes_cli import plugins
from tools import image_generation_tool as tool

codex = importlib.import_module("plugins.image_gen.openai-codex")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network/real credentials forbidden in contract tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(codex, "_read_codex_access_token", lambda: "fixture-only")
    monkeypatch.setattr(codex, "_build_codex_client", forbidden)
    monkeypatch.setattr(plugins, "_ensure_plugins_discovered", lambda **kw: None)
    monkeypatch.setattr(tool, "check_fal_api_key", lambda: False)
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)
    image_gen_registry._reset_for_tests()
    yield
    image_gen_registry._reset_for_tests()


def config(monkeypatch, root, section):
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(yaml.safe_dump({"image_gen": section}))
    monkeypatch.setenv("HERMES_HOME", str(root))


def test_available_plugin_does_not_select_itself(monkeypatch, tmp_path):
    config(monkeypatch, tmp_path, {})
    image_gen_registry.register_provider(codex.OpenAICodexImageGenProvider())
    fal = Mock(return_value='{"fixture": "fal"}')
    monkeypatch.setattr(tool, "image_generate_tool", fal)
    assert tool.check_image_generation_requirements() is True
    assert json.loads(tool._handle_image_generate({"prompt": "fixture"})) == {"fixture": "fal"}
    fal.assert_called_once()


def test_explicit_selection_routes_to_real_plugin_without_fal(monkeypatch, tmp_path):
    config(monkeypatch, tmp_path, {"provider": "openai-codex"})
    image_gen_registry.register_provider(codex.OpenAICodexImageGenProvider())
    # Keep real plugin generate/auth error semantics; do not replace generate.
    monkeypatch.setattr(codex, "_read_codex_access_token", lambda: None)
    fal = Mock(side_effect=AssertionError("Unexpected fallback"))
    monkeypatch.setattr(tool, "image_generate_tool", fal)
    result = json.loads(tool._handle_image_generate({"prompt": "fixture"}))
    assert result["provider"] == "openai-codex"
    assert result["error_type"] == "auth_required"
    fal.assert_not_called()


def test_missing_registration_is_error_not_fal(monkeypatch, tmp_path):
    config(monkeypatch, tmp_path, {"provider": "nonexistent-fixture"})
    fal = Mock(side_effect=AssertionError("Unexpected fallback"))
    monkeypatch.setattr(tool, "image_generate_tool", fal)
    result = json.loads(tool._handle_image_generate({"prompt": "fixture"}))
    assert result["error_type"] == "provider_not_registered"
    fal.assert_not_called()


@pytest.mark.parametrize("fail_on_refresh", [False, True])
def test_discovery_error_fallback_depends_on_phase(monkeypatch, tmp_path, fail_on_refresh):
    config(monkeypatch, tmp_path, {"provider": "openai-codex"})
    calls = []

    def discover(force=False):
        calls.append(force)
        if force == fail_on_refresh:
            raise RuntimeError("fixture discovery failure")

    monkeypatch.setattr(plugins, "_ensure_plugins_discovered", discover)
    fal = Mock(return_value='{"fixture": "fal"}')
    monkeypatch.setattr(tool, "image_generate_tool", fal)
    result = json.loads(tool._handle_image_generate({"prompt": "fixture"}))
    if fail_on_refresh:
        assert calls == [False, True]
        assert result["error_type"] == "provider_not_registered"
        fal.assert_not_called()
    else:
        assert calls == [False]
        assert result == {"fixture": "fal"}
        fal.assert_called_once_with(prompt="fixture", aspect_ratio="landscape")


@pytest.mark.parametrize("section", ["openai-codex", [], {"provider": ["openai-codex"]}])
def test_incompatible_provider_shape_is_treated_as_unset(monkeypatch, tmp_path, section):
    config(monkeypatch, tmp_path, section)
    assert tool._read_configured_image_provider() is None
    assert tool._dispatch_to_plugin_provider("fixture", "square") is None


@pytest.mark.parametrize(
    "env,section,expected",
    [
        ("gpt-image-2-low", {"openai-codex": {"model": "gpt-image-2-high"}}, "gpt-image-2-low"),
        (None, {"model": "gpt-image-2-low", "openai-codex": {"model": "gpt-image-2-high"}}, "gpt-image-2-high"),
        (None, {"model": "gpt-image-2-low"}, "gpt-image-2-low"),
        ("invalid", {"model": "gpt-image-2-low"}, "gpt-image-2-low"),
        (None, {"model": "fal-ai/flux-2-pro", "openai-codex": {"model": "gpt-6-luna"}}, codex.DEFAULT_MODEL),
        (None, {"openai-codex": "gpt-image-2-high"}, codex.DEFAULT_MODEL),
    ],
)
def test_tier_precedence_and_invalid_value_fallback(monkeypatch, tmp_path, env, section, expected):
    config(monkeypatch, tmp_path, section)
    if env is not None:
        monkeypatch.setenv("OPENAI_IMAGE_MODEL", env)
    assert codex._resolve_model()[0] == expected


def test_profile_config_and_output_paths_do_not_leak(monkeypatch, tmp_path):
    first, second = tmp_path / "author-a", tmp_path / "author-b"
    config(monkeypatch, first, {"provider": "openai-codex", "model": "gpt-image-2-low"})
    config(monkeypatch, second, {"provider": "fal", "model": "gpt-image-2-high"})
    # Save via actual helper, while mocking only the external result.
    monkeypatch.setattr(codex, "_build_codex_client", lambda: object())
    monkeypatch.setattr(codex, "_collect_image_b64", lambda *a, **kw: "aW1hZ2UtZml4dHVyZQ==")
    provider = codex.OpenAICodexImageGenProvider()
    for home, selected, tier in [(first, "openai-codex", "gpt-image-2-low"), (second, "fal", "gpt-image-2-high"), (first, "openai-codex", "gpt-image-2-low")]:
        monkeypatch.setenv("HERMES_HOME", str(home))
        assert tool._read_configured_image_provider() == selected
        assert codex._resolve_model()[0] == tier
        result = provider.generate("offline fixture")
        assert result["success"] is True
        assert Path(result["image"]).parent == home / "cache" / "images"
        assert Path(result["image"]).read_bytes() == b"image-fixture"


def test_availability_does_not_prove_stream_works(monkeypatch, tmp_path):
    config(monkeypatch, tmp_path, {"provider": "openai-codex"})
    provider = codex.OpenAICodexImageGenProvider()
    def fail(**kwargs):
        raise RuntimeError("fixture access denied")
    monkeypatch.setattr(codex, "_build_codex_client", lambda: SimpleNamespace(responses=SimpleNamespace(stream=fail)))
    assert provider.is_available() is True
    assert provider.generate("fixture")["error_type"] == "api_error"
