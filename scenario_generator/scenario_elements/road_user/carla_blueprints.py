from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("carla_blueprints.json")
DIMENSION_KEYS = ("length_m", "width_m", "height_m")


def load_carla_blueprint_catalog(
    path: Path = CATALOG_PATH,
) -> dict[str, list[dict[str, Any]]]:
    """Load carla blueprint catalog.

    Args:
        path: path used by this operation.

    """
    with path.open(encoding="utf-8") as file:
        raw_catalog = json.load(file)
    catalog: dict[str, list[dict[str, Any]]] = {}
    for category in ("vehicles", "cyclists", "pedestrians"):
        raw_entries = raw_catalog.get(category, [])
        if not isinstance(raw_entries, list):
            raw_entries = []
        catalog[category] = [
            normalize_entry(entry) for entry in raw_entries if isinstance(entry, dict)
        ]
    return catalog


def normalize_dimensions(raw_dimensions: Any) -> dict[str, float] | None:
    """Normalize dimensions.

    Args:
        raw_dimensions: raw dimensions used by this operation.

    """
    if not isinstance(raw_dimensions, dict):
        return None
    dimensions: dict[str, float] = {}
    for source_key, target_key in (
        ("length_m", "length_m"),
        ("width_m", "width_m"),
        ("height_m", "height_m"),
        ("length", "length_m"),
        ("width", "width_m"),
        ("height", "height_m"),
    ):
        if source_key not in raw_dimensions:
            continue
        dimensions[target_key] = float(raw_dimensions[source_key])
    if not all(key in dimensions for key in DIMENSION_KEYS):
        return None
    return dimensions


def normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize entry.

    Args:
        entry: entry used by this operation.

    """
    blueprint_id = str(entry.get("id", "")).strip()
    if not blueprint_id:
        raise ValueError("CARLA blueprint entry is missing id.")
    label = str(entry.get("label", blueprint_id)).strip() or blueprint_id
    category = str(entry.get("category", "")).strip()
    normalized: dict[str, Any] = {
        "id": blueprint_id,
        "label": label,
        "category": category,
    }
    special_type = str(entry.get("special_type", "")).strip()
    if special_type:
        normalized["special_type"] = special_type
    dimensions = normalize_dimensions(entry.get("dimensions"))
    if dimensions is not None:
        normalized["dimensions"] = dimensions
    return normalized


def blueprint_entries_for_actor_type(
    actor_type: str,
    catalog: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Handle blueprint entries for actor type.

    Args:
        actor_type: actor type used by this operation.
        catalog: catalog used by this operation.

    """
    if catalog is None:
        catalog = load_carla_blueprint_catalog()
    if actor_type == "pedestrian":
        return catalog.get("pedestrians", [])
    if actor_type == "cyclist":
        return catalog.get("cyclists", [])
    return catalog.get("vehicles", [])


def format_dimensions(dimensions: dict[str, float]) -> str:
    return f"{dimensions['length_m']:.2f} x {dimensions['width_m']:.2f} x {dimensions['height_m']:.2f} m"


def blueprint_label(entry: dict[str, Any]) -> str:
    """Handle blueprint label.

    Args:
        entry: entry used by this operation.

    """
    category = entry.get("category", "")
    suffix = f" [{category}]" if category else ""
    dimension_text = ""
    dimensions = entry.get("dimensions")
    if isinstance(dimensions, dict) and all(
        key in dimensions for key in DIMENSION_KEYS
    ):
        dimension_text = f" - {format_dimensions(dimensions)}"
    return f"{entry.get('label', entry['id'])} ({entry['id']}){suffix}{dimension_text}"
