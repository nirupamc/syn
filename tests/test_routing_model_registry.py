"""M9 tests for model registry: resolve, list, aliases, edge cases."""

from __future__ import annotations

import pytest

from app.core.errors import ModelNotFoundError
from app.routing.model_registry import ModelEntry, ModelRegistry


def _entry(id: str, aliases=(), enabled: bool = True, backend_id: str = "gpu-1") -> ModelEntry:
    return ModelEntry(id=id, backend_id=backend_id, backend_model=id, enabled=enabled, aliases=aliases)


class TestModelRegistryResolve:
    def test_resolve_canonical_id(self):
        reg = ModelRegistry([_entry("model-a"), _entry("model-b")])
        entry = reg.resolve("model-a")
        assert entry.id == "model-a"

    def test_resolve_by_alias(self):
        reg = ModelRegistry([_entry("model-a", aliases=["alias-x"])])
        entry = reg.resolve("alias-x")
        assert entry.id == "model-a"

    def test_resolve_unknown_raises(self):
        reg = ModelRegistry([_entry("model-a")])
        with pytest.raises(ModelNotFoundError, match="not found"):
            reg.resolve("nonexistent")

    def test_resolve_disabled_still_resolves(self):
        reg = ModelRegistry([_entry("model-a", enabled=False)])
        entry = reg.resolve("model-a")
        assert entry.id == "model-a"
        assert entry.enabled is False

    def test_case_sensitive(self):
        reg = ModelRegistry([_entry("Model-A", aliases=["alias-x"])])
        with pytest.raises(ModelNotFoundError):
            reg.resolve("model-a")
        with pytest.raises(ModelNotFoundError):
            reg.resolve("ALIAS-X")


class TestModelRegistryList:
    def test_list_all(self):
        reg = ModelRegistry([_entry("a"), _entry("b"), _entry("c")])
        assert len(reg.list_all()) == 3

    def test_list_enabled(self):
        reg = ModelRegistry([
            _entry("a", enabled=True),
            _entry("b", enabled=False),
            _entry("c", enabled=True),
        ])
        enabled = reg.list_enabled()
        assert len(enabled) == 2
        assert {e.id for e in enabled} == {"a", "c"}

    def test_get_existing(self):
        reg = ModelRegistry([_entry("a")])
        assert reg.get("a") is not None
        assert reg.get("a").id == "a"

    def test_get_nonexistent_returns_none(self):
        reg = ModelRegistry([_entry("a")])
        assert reg.get("nonexistent") is None


class TestModelRegistryAliases:
    def test_multiple_aliases(self):
        reg = ModelRegistry([_entry("a", aliases=["x", "y", "z"])])
        assert reg.resolve("x").id == "a"
        assert reg.resolve("y").id == "a"
        assert reg.resolve("z").id == "a"

    def test_alias_not_model_id(self):
        reg = ModelRegistry([
            _entry("model-a", aliases=["alias-a"]),
            _entry("model-b"),
        ])
        assert reg.resolve("alias-a").id == "model-a"
        assert reg.resolve("model-b").id == "model-b"

    def test_duplicate_model_id_raises(self):
        with pytest.raises(ModelNotFoundError, match="duplicate model id"):
            ModelRegistry([_entry("a"), _entry("a")])

    def test_duplicate_alias_raises(self):
        with pytest.raises(ModelNotFoundError, match="duplicate alias"):
            ModelRegistry([
                _entry("a", aliases=["shared"]),
                _entry("b", aliases=["shared"]),
            ])
