from __future__ import annotations

from pathlib import Path

from scenario_generator.io.importer_exporter.base import Exporter, Importer


class ImporterRegistry:
    """Small registry that lets new importers be added without central switches."""

    def __init__(self):
        self._importers: list[Importer] = []

    def register(self, importer: Importer):
        self._importers.append(importer)

    def importer_for(self, path: Path) -> Importer | None:
        for importer in self._importers:
            if importer.supports(path):
                return importer
        return None


class ExporterRegistry:
    """Small registry that lets new exporters be added without central switches."""

    def __init__(self):
        self._exporters: list[Exporter] = []

    def register(self, exporter: Exporter):
        self._exporters.append(exporter)

    def exporter_for(self, path: Path) -> Exporter | None:
        for exporter in self._exporters:
            if exporter.supports(path):
                return exporter
        return None


importer_registry = ImporterRegistry()
exporter_registry = ExporterRegistry()
registry = exporter_registry
