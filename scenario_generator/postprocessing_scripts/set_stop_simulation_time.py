"""Set the stop-trigger simulation time in exported OpenSCENARIO files."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

APPLICABLE_EXPORTS = frozenset({"xosc"})
PARAMETERS = [
    {
        "name": "simulation_time_s",
        "label": "Stop simulation time [s]",
        "type": "number",
        "default": 30.0,
    }
]

STOP_TRIGGER_PATHS = (
    "./Storyboard/Story/Act/StopTrigger//SimulationTimeCondition",
    "./Storyboard/StopTrigger//SimulationTimeCondition",
)


def run(export_directory: Path, parameters: dict[str, object]) -> bool:
    """Set Act and Storyboard stop-trigger times to the requested seconds."""
    simulation_time_s = float(parameters.get("simulation_time_s", 30.0))
    if simulation_time_s < 0.0:
        return False
    changed = False
    for path in export_directory.glob("*.xosc"):
        tree = ET.parse(path)
        for trigger_path in STOP_TRIGGER_PATHS:
            for condition in tree.findall(trigger_path):
                condition.set("value", f"{simulation_time_s:g}")
                changed = True
        tree.write(path, encoding="utf-8", xml_declaration=True)
    return changed
