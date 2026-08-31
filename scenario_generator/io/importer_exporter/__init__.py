from scenario_generator.io.importer_exporter.base import Exporter, Importer, ImportResult
from scenario_generator.io.importer_exporter.omega_prime import OmegaPrimeAdapter
from scenario_generator.io.importer_exporter.openscenario import (
    AUTHOR,
    OpenScenarioExporter,
    build_openscenario_xml,
    current_datetime_string,
    format_float,
    write_openscenario,
)
from scenario_generator.io.importer_exporter.registry import (
    ExporterRegistry,
    ImporterRegistry,
    exporter_registry,
    importer_registry,
)
from scenario_generator.io.importer_exporter.simple_scenario import SimpleScenarioAdapter

exporter_registry.register(OpenScenarioExporter())
exporter_registry.register(OmegaPrimeAdapter())
importer_registry.register(SimpleScenarioAdapter())

__all__ = [
    "AUTHOR",
    "Exporter",
    "ExporterRegistry",
    "ImportResult",
    "Importer",
    "ImporterRegistry",
    "OmegaPrimeAdapter",
    "OpenScenarioExporter",
    "SimpleScenarioAdapter",
    "build_openscenario_xml",
    "current_datetime_string",
    "exporter_registry",
    "format_float",
    "importer_registry",
    "write_openscenario",
]
