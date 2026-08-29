"""M9 tests for routing configuration parsing and validation."""

from __future__ import annotations

import json
import pytest

from app.config import BackendType
from app.core.errors import RoutingConfigError
from app.routing.config import (
    BackendConfig,
    ModelConfig,
    load_routing_config,
    parse_routing_config,
)


# ---- load_routing_config ----------------------------------------------------


def test_load_nonexistent_file_returns_none(tmp_path):
    result = load_routing_config(str(tmp_path / "missing.json"))
    assert result is None


def test_load_empty_file_returns_none(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    result = load_routing_config(str(p))
    assert result is None


def test_load_whitespace_only_file_returns_none(tmp_path):
    p = tmp_path / "ws.json"
    p.write_text("   \n  \t  ", encoding="utf-8")
    result = load_routing_config(str(p))
    assert result is None


def test_load_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RoutingConfigError, match="not valid JSON"):
        load_routing_config(str(p))


def test_load_non_dict_json_raises(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(RoutingConfigError, match="must be a JSON object"):
        load_routing_config(str(p))


def test_load_valid_json_returns_dict(tmp_path):
    p = tmp_path / "ok.json"
    data = {"backends": [], "models": []}
    p.write_text(json.dumps(data), encoding="utf-8")
    result = load_routing_config(str(p))
    assert result == data


# ---- parse_routing_config ---------------------------------------------------


_VALID_CONFIG = {
    "backends": [
        {"id": "gpu-1", "type": "llama_cpp", "base_url": "http://localhost:8080"},
    ],
    "models": [
        {"id": "local-general", "backend_id": "gpu-1", "backend_model": "model.gguf"},
    ],
}


def test_valid_minimal_config():
    parsed = parse_routing_config(_VALID_CONFIG)
    assert len(parsed.backends) == 1
    assert len(parsed.models) == 1
    assert parsed.backends[0].id == "gpu-1"
    assert parsed.backends[0].type == BackendType.LLAMA_CPP
    assert parsed.backends[0].base_url == "http://localhost:8080"
    assert parsed.models[0].id == "local-general"
    assert parsed.models[0].backend_id == "gpu-1"
    assert parsed.models[0].backend_model == "model.gguf"
    assert parsed.models[0].enabled is True
    assert parsed.models[0].aliases == ()


def test_config_missing_backends_key():
    with pytest.raises(RoutingConfigError, match="non-empty 'backends' list"):
        parse_routing_config({"models": []})


def test_config_missing_models_key():
    with pytest.raises(RoutingConfigError, match="non-empty 'backends' list"):
        parse_routing_config({"backends": []})


def test_config_empty_backends_list():
    with pytest.raises(RoutingConfigError, match="non-empty 'backends' list"):
        parse_routing_config({"backends": [], "models": []})


def test_config_empty_models_list():
    with pytest.raises(RoutingConfigError, match="non-empty 'models' list"):
        parse_routing_config({
            "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
            "models": [],
        })


def test_duplicate_backend_id():
    config = {
        "backends": [
            {"id": "a", "type": "llama_cpp", "base_url": "http://x"},
            {"id": "a", "type": "llama_cpp", "base_url": "http://y"},
        ],
        "models": [{"id": "m", "backend_id": "a"}],
    }
    with pytest.raises(RoutingConfigError, match="duplicate backend id"):
        parse_routing_config(config)


def test_unsupported_backend_type():
    config = {
        "backends": [{"id": "a", "type": "vllm", "base_url": "http://x"}],
        "models": [{"id": "m", "backend_id": "a"}],
    }
    with pytest.raises(RoutingConfigError, match="unsupported backend type"):
        parse_routing_config(config)


def test_duplicate_model_id():
    config = {
        "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
        "models": [
            {"id": "m", "backend_id": "a"},
            {"id": "m", "backend_id": "a"},
        ],
    }
    with pytest.raises(RoutingConfigError, match="duplicate model id"):
        parse_routing_config(config)


def test_model_references_unknown_backend():
    config = {
        "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
        "models": [{"id": "m", "backend_id": "nonexistent"}],
    }
    with pytest.raises(RoutingConfigError, match="unknown backend"):
        parse_routing_config(config)


def test_alias_collides_with_earlier_model_id():
    """An alias that collides with a model ID declared earlier is rejected."""
    config = {
        "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
        "models": [
            {"id": "model-b", "backend_id": "a"},
            {"id": "model-a", "backend_id": "a", "aliases": ["model-b"]},
        ],
    }
    with pytest.raises(RoutingConfigError, match="collides with model id"):
        parse_routing_config(config)


def test_duplicate_alias():
    config = {
        "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
        "models": [
            {"id": "m1", "backend_id": "a", "aliases": ["alias-x"]},
            {"id": "m2", "backend_id": "a", "aliases": ["alias-x"]},
        ],
    }
    with pytest.raises(RoutingConfigError, match="duplicate alias"):
        parse_routing_config(config)


def test_config_with_aliases_and_metadata():
    config = {
        "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
        "models": [
            {
                "id": "m1",
                "backend_id": "a",
                "backend_model": "path/to/model.gguf",
                "enabled": True,
                "aliases": ["alias1", "alias2"],
                "metadata": {"gpu_layers": 33},
            },
        ],
    }
    parsed = parse_routing_config(config)
    m = parsed.models[0]
    assert m.aliases == ("alias1", "alias2")
    assert m.metadata == {"gpu_layers": 33}
    assert m.backend_model == "path/to/model.gguf"


def test_disabled_model():
    config = {
        "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
        "models": [{"id": "m", "backend_id": "a", "enabled": False}],
    }
    parsed = parse_routing_config(config)
    assert parsed.models[0].enabled is False


def test_backend_model_defaults_to_model_id():
    config = {
        "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
        "models": [{"id": "my-model", "backend_id": "a"}],
    }
    parsed = parse_routing_config(config)
    assert parsed.models[0].backend_model == "my-model"


def test_model_object_item_raises():
    with pytest.raises(RoutingConfigError, match="must be an object"):
        parse_routing_config({
            "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
            "models": ["bad"],
        })


def test_backend_object_item_raises():
    with pytest.raises(RoutingConfigError, match="must be an object"):
        parse_routing_config({
            "backends": ["bad"],
            "models": [{"id": "m", "backend_id": "a"}],
        })


def test_backend_missing_id_raises():
    with pytest.raises(RoutingConfigError, match="requires a non-empty 'id'"):
        parse_routing_config({
            "backends": [{"type": "llama_cpp", "base_url": "http://x"}],
            "models": [{"id": "m", "backend_id": "a"}],
        })


def test_backend_missing_base_url_raises():
    with pytest.raises(RoutingConfigError, match="requires a non-empty 'base_url'"):
        parse_routing_config({
            "backends": [{"id": "a", "type": "llama_cpp"}],
            "models": [{"id": "m", "backend_id": "a"}],
        })


def test_model_missing_id_raises():
    with pytest.raises(RoutingConfigError, match="requires a non-empty 'id'"):
        parse_routing_config({
            "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
            "models": [{"backend_id": "a"}],
        })


def test_model_missing_backend_id_raises():
    with pytest.raises(RoutingConfigError, match="requires a non-empty 'backend_id'"):
        parse_routing_config({
            "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
            "models": [{"id": "m"}],
        })


def test_model_aliases_not_list_raises():
    with pytest.raises(RoutingConfigError, match="'aliases' must be a list"):
        parse_routing_config({
            "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
            "models": [{"id": "m", "backend_id": "a", "aliases": "bad"}],
        })


def test_model_alias_not_string_raises():
    with pytest.raises(RoutingConfigError, match="invalid alias"):
        parse_routing_config({
            "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
            "models": [{"id": "m", "backend_id": "a", "aliases": [123]}],
        })


def test_model_empty_alias_string_raises():
    with pytest.raises(RoutingConfigError, match="invalid alias"):
        parse_routing_config({
            "backends": [{"id": "a", "type": "llama_cpp", "base_url": "http://x"}],
            "models": [{"id": "m", "backend_id": "a", "aliases": [""]}],
        })
