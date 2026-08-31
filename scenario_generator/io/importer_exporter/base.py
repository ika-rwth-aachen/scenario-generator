from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ImportResult:
    """Common return envelope for importer implementations."""

    payload: Any
    source_path: Path


class Importer(ABC):
    """Base class for file importers registered by suffix."""

    supported_suffixes: tuple[str, ...] = ()

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.supported_suffixes

    @abstractmethod
    def load(self, path: Path) -> ImportResult:
        raise NotImplementedError


class Exporter(ABC):
    """Base class for file exporters registered by output suffix."""

    default_suffix: str = ""
    format_name: str = ""

    def supports(self, path: Path) -> bool:
        return bool(self.default_suffix) and path.suffix.lower() == self.default_suffix

    @abstractmethod
    def export_file(
        self,
        output_path: Path,
        trajectories: dict[str, dict[str, Any]],
        **options: Any,
    ):
        raise NotImplementedError
