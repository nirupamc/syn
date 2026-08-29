"""Model registry (M9).

Maps public/canonical model IDs and their aliases to a configured backend and a
backend-native model identifier. The public Syn model ID is intentionally
decoupled from the backend's filesystem/native model path: clients request
``local-general`` while the backend receives ``D:\\llama\\...\\model.gguf``.

Matching is case-sensitive and exact (no fuzzy matching). Aliases must be
globally unique and may not collide with a canonical model ID.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.errors import ModelNotFoundError


@dataclass(frozen=True)
class ModelEntry:
    """A single registered model."""

    id: str
    backend_id: str
    backend_model: str
    enabled: bool = True
    aliases: tuple[str, ...] = field(default=())
    metadata: dict = field(default_factory=dict)


class ModelRegistry:
    """Registry of public models and their aliases."""

    def __init__(self, entries: list[ModelEntry]) -> None:
        self._by_id: dict[str, ModelEntry] = {}
        self._by_alias: dict[str, str] = {}
        for entry in entries:
            if entry.id in self._by_id:
                # Defensive: uniqueness is enforced by config validation.
                raise ModelNotFoundError(
                    f"duplicate model id: {entry.id!r}",
                    code="model_not_found",
                )
            self._by_id[entry.id] = entry
            for alias in entry.aliases:
                if alias in self._by_alias:
                    raise ModelNotFoundError(
                        f"duplicate alias: {alias!r}",
                        code="model_not_found",
                    )
                self._by_alias[alias] = entry.id

    def resolve(self, requested: str) -> ModelEntry:
        """Resolve a requested model or alias to its canonical entry.

        Raises :class:`ModelNotFoundError` if neither a canonical ID nor a
        registered alias matches.
        """
        entry = self._by_id.get(requested)
        if entry is None:
            canonical = self._by_alias.get(requested)
            if canonical is None:
                raise ModelNotFoundError(
                    f"model '{requested}' not found",
                    code="model_not_found",
                )
            entry = self._by_id[canonical]
        return entry

    def get(self, model_id: str) -> Optional[ModelEntry]:
        return self._by_id.get(model_id)

    def list_all(self) -> list[ModelEntry]:
        return list(self._by_id.values())

    def list_enabled(self) -> list[ModelEntry]:
        return [e for e in self._by_id.values() if e.enabled]
