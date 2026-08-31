"""Example postprocessor that translates every exported trajectory JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

APPLICABLE_EXPORTS = frozenset({"json"})
PARAMETERS = [
    {"name": "x_offset_m", "label": "X offset [m]", "type": "number", "default": 0.0},
    {"name": "y_offset_m", "label": "Y offset [m]", "type": "number", "default": 0.0},
]


def run(export_directory: Path, parameters: dict[str, object]) -> bool:
    """Apply the configured constant offset to exported trajectory coordinates."""
    x_offset_m = float(parameters.get("x_offset_m", 0.0))
    y_offset_m = float(parameters.get("y_offset_m", 0.0))
    for path in export_directory.glob("*.json"):
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        trajectories = [value for value in payload.values() if isinstance(value, dict)]
        if not trajectories or not all("x_m" in value and "y_m" in value for value in trajectories):
            continue
        for trajectory in trajectories:
            trajectory["x_m"] = [float(value) + x_offset_m for value in trajectory["x_m"]]
            trajectory["y_m"] = [float(value) + y_offset_m for value in trajectory["y_m"]]
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True
