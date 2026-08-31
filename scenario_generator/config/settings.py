"""Configuration helpers for the packaged application defaults."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(str(files("scenario_generator.config").joinpath("default_config.yml")))

REQUIRED_SERIES = ("time_s", "x_m", "y_m", "speed_mps", "yaw_rad")
COLORS = ("#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf")
XOSC_EXPORT_MODE_LABELS = {
    "Trajectory": "trajectory",
    "Route": "route",
    "Reach position": "reach_position",
    "Clear trajectory": "clear_trajectory",
}
XOSC_EXPORT_MODE_LABEL_BY_VALUE = {
    value: label for label, value in XOSC_EXPORT_MODE_LABELS.items()
}

DEFAULT_TIME_STEP_S = 1.0
DEFAULT_PROFILE_DISTANCE_SAMPLES_PER_SEGMENT = 64
DEFAULT_CANVAS_MIN_SCALE = 0.2
DEFAULT_CANVAS_MAX_SCALE = 120.0
DEFAULT_OMEGA_PRIME_INTERPOLATION_HZ = 10
DEFAULT_MAP_EXPORT_MAX_LATERAL_DEVIATION_M = 0.5
DEFAULT_IMPORT_LIMITS: dict[str, int | float] = {
    "max_imported_actors": 256,
    "max_waypoints_per_actor": 100_000,
    "max_total_waypoints": 250_000,
    "max_imported_roads": 10_000,
    "max_points_per_road": 100_000,
    "max_total_map_points": 500_000,
    "max_text_field_chars": 1_000_000,
    "max_actor_name_chars": 128,
    "max_abs_coordinate_m": 10_000_000.0,
    "max_time_s": 1_000_000.0,
    "max_speed_mps": 10_000.0,
    "max_actor_dimension_m": 1_000.0,
}
DEFAULT_LANE_TYPE_COLORS = {
    "driving": "#4a4f55",
    "sidewalk": "#d8d8d8",
    "walking": "#d8d8d8",
    "pedestrian": "#d8d8d8",
    "biking": "#f2bd78",
    "parking": "#8fa7b5",
    "restricted": "#c98b8b",
    "default": "#f6d7df",
}
DEFAULT_SIMPLE_SCENARIO_LANE_WIDTH_M = 3.75
DEFAULT_SIMPLE_SCENARIO_DT_S = 0.1
DEFAULT_SIMPLE_SCENARIO_LANELET_ID_BASE = 1000
DEFAULT_SIMPLE_SCENARIO_DURATION_S = 10.0
DEFAULT_XOSC_REV_MINOR = 1
DEFAULT_SIMULATION_TIME_CONDITION_FACTOR = 1.0
DEFAULT_ACTOR_DIMENSIONS = {
    "vehicle": {"width_m": 1.8, "length_m": 4.5, "height_m": 1.8},
    "cyclist": {"width_m": 0.7, "length_m": 1.8, "height_m": 1.7},
    "pedestrian": {"width_m": 0.6, "length_m": 0.6, "height_m": 1.8},
}
DEFAULT_SIMPLE_SCENARIO_VEHICLE_TYPES = {
    "medium": {
        "length_m": 4.5,
        "width_m": 1.8,
        "height_m": 1.8,
        "actor_type": "vehicle",
    },
    "small": {
        "length_m": 4.0,
        "width_m": 1.7,
        "height_m": 1.6,
        "actor_type": "vehicle",
    },
    "large": {
        "length_m": 5.2,
        "width_m": 2.0,
        "height_m": 2.2,
        "actor_type": "vehicle",
    },
    "bicycle": {
        "length_m": 1.8,
        "width_m": 0.7,
        "height_m": 1.7,
        "actor_type": "cyclist",
    },
    "cyclist": {
        "length_m": 1.8,
        "width_m": 0.7,
        "height_m": 1.7,
        "actor_type": "cyclist",
    },
    "pedestrian": {
        "length_m": 0.6,
        "width_m": 0.6,
        "height_m": 1.8,
        "actor_type": "pedestrian",
    },
}


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load a YAML configuration, falling back to defaults for missing files."""
    if not config_path.exists():
        return {}
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("Configuration must contain a YAML mapping at its root.")
    return loaded


def config_value(config_path: Path, key_name: str) -> str | None:
    """Return a top-level config value as text, or ``None`` when it is absent."""
    value = load_config(config_path).get(key_name)
    return str(value) if value is not None else None


def nested_config_value(
    config_path: Path,
    section_name: str,
    key_name: str,
) -> str | None:
    """Return a value from ``section.key`` with dotted-key fallback support."""
    section = load_config(config_path).get(section_name)
    if isinstance(section, dict):
        value = section.get(key_name)
        return str(value) if value is not None else None
    value = load_config(config_path).get(f"{section_name}.{key_name}")
    return str(value) if value is not None else None


def nested_config_section(config_path: Path, *section_names: str) -> dict[str, Any]:
    """Return a nested dictionary section, or an empty dict for missing sections."""
    section: Any = load_config(config_path)
    for section_name in section_names:
        if not isinstance(section, dict):
            return {}
        section = section.get(section_name)
    return section if isinstance(section, dict) else {}


def _float_value(
    config_path: Path,
    key_name: str,
    default: float,
    minimum: float = 0.001,
) -> float:
    """Read and clamp a top-level float setting with a fallback default."""
    value = config_value(config_path, key_name)
    if value is None:
        return default
    try:
        return max(float(value), minimum)
    except ValueError:
        return default


def _nested_float_value(
    config_path: Path,
    section_name: str,
    key_name: str,
    default: float,
    minimum: float = 0.001,
) -> float:
    """Read and clamp a nested float setting with a fallback default."""
    value = nested_config_value(config_path, section_name, key_name)
    if value is None:
        return default
    try:
        return max(float(value), minimum)
    except ValueError:
        return default


def _nested_int_value(
    config_path: Path,
    section_name: str,
    key_name: str,
    default: int,
    minimum: int = 0,
) -> int:
    """Read and clamp a nested integer setting with a fallback default."""
    value = nested_config_value(config_path, section_name, key_name)
    if value is None:
        return default
    try:
        return max(int(value), minimum)
    except ValueError:
        return default


def load_time_step_s(config_path: Path = CONFIG_PATH) -> float:
    """Return the default time delta for newly appended waypoints."""
    return _float_value(config_path, "time_step_s", DEFAULT_TIME_STEP_S)


def load_simulation_time_condition_factor(config_path: Path = CONFIG_PATH) -> float:
    """Return the XOSC SimulationTimeCondition multiplier, never below one."""
    value = nested_config_value(
        config_path,
        "openscenario",
        "simulation_time_condition_factor",
    )
    if value is None:
        # Preserve project compatibility with the short-lived previous key.
        value = nested_config_value(
            config_path, "openscenario", "simulation_time_factor"
        )
    try:
        return (
            max(float(value), 1.0)
            if value is not None
            else DEFAULT_SIMULATION_TIME_CONDITION_FACTOR
        )
    except ValueError:
        return DEFAULT_SIMULATION_TIME_CONDITION_FACTOR
def load_profile_distance_samples_per_segment(
    config_path: Path = CONFIG_PATH,
) -> int:
    """Return the geometric sampling density used for trajectory distance profiles."""
    value = config_value(config_path, "PROFILE_DISTANCE_SAMPLES_PER_SEGMENT")
    if value is None:
        return DEFAULT_PROFILE_DISTANCE_SAMPLES_PER_SEGMENT
    try:
        return max(int(value), 1)
    except ValueError:
        return DEFAULT_PROFILE_DISTANCE_SAMPLES_PER_SEGMENT


def load_canvas_min_scale(config_path: Path = CONFIG_PATH) -> float:
    """Return the minimum canvas zoom in pixels per meter."""
    return _float_value(config_path, "canvas_min_scale", DEFAULT_CANVAS_MIN_SCALE)


def load_canvas_max_scale(config_path: Path = CONFIG_PATH) -> float:
    """Return the maximum canvas zoom, never below the configured minimum."""
    value = config_value(config_path, "canvas_max_scale")
    if value is None:
        return DEFAULT_CANVAS_MAX_SCALE
    try:
        return max(float(value), load_canvas_min_scale(config_path))
    except ValueError:
        return DEFAULT_CANVAS_MAX_SCALE


def load_omega_prime_interpolation_hz(config_path: Path = CONFIG_PATH) -> int:
    """Return the Omega-Prime import interpolation frequency in Hertz."""
    return _nested_int_value(
        config_path,
        "omega_prime",
        "interpolation_hz",
        DEFAULT_OMEGA_PRIME_INTERPOLATION_HZ,
        minimum=1,
    )


def load_map_export_max_lateral_deviation_m(config_path: Path = CONFIG_PATH) -> float:
    """Return the maximum lateral deviation allowed for exported map geometry."""
    return _nested_float_value(
        config_path,
        "map_export",
        "max_lateral_deviation_m",
        DEFAULT_MAP_EXPORT_MAX_LATERAL_DEVIATION_M,
    )


def load_import_limits(
    config_path: Path = CONFIG_PATH,
) -> dict[str, int | float]:
    """Return positive safety and resource limits for imported data."""
    limits: dict[str, int | float] = {}
    for key, default in DEFAULT_IMPORT_LIMITS.items():
        if isinstance(default, int):
            limits[key] = _nested_int_value(
                config_path,
                "import_limits",
                key,
                default,
                minimum=1,
            )
        else:
            limits[key] = _nested_float_value(
                config_path,
                "import_limits",
                key,
                default,
                minimum=0.001,
            )
    return limits


def load_lane_type_colors(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    """Return configured map colors keyed by OpenDRIVE lane type."""
    colors = dict(DEFAULT_LANE_TYPE_COLORS)
    configured = nested_config_section(config_path, "map_display", "lane_type_colors")
    for lane_type, color in configured.items():
        color_text = str(color).strip()
        if color_text:
            colors[str(lane_type).lower()] = color_text
    return colors


def load_simple_scenario_lane_width_m(config_path: Path = CONFIG_PATH) -> float:
    """Return the fallback lane width for simple-scenario imports."""
    return _nested_float_value(
        config_path,
        "simple_scenario",
        "lane_width_m",
        DEFAULT_SIMPLE_SCENARIO_LANE_WIDTH_M,
    )


def load_simple_scenario_dt_s(config_path: Path = CONFIG_PATH) -> float:
    """Return the sampling step used when expanding simple-scenario actors."""
    return _nested_float_value(
        config_path,
        "simple_scenario",
        "dt_s",
        DEFAULT_SIMPLE_SCENARIO_DT_S,
    )


def load_simple_scenario_lanelet_id_base(config_path: Path = CONFIG_PATH) -> int:
    """Return the base lanelet id used when generating simple-scenario maps."""
    return _nested_int_value(
        config_path,
        "simple_scenario",
        "lanelet_id_base",
        DEFAULT_SIMPLE_SCENARIO_LANELET_ID_BASE,
    )


def load_simple_scenario_duration_s(config_path: Path = CONFIG_PATH) -> float:
    """Return the default duration for simple-scenario actors without end time."""
    return _nested_float_value(
        config_path,
        "simple_scenario",
        "default_duration_s",
        DEFAULT_SIMPLE_SCENARIO_DURATION_S,
    )


def load_default_xosc_rev_minor(config_path: Path = CONFIG_PATH) -> int:
    """Return the default OpenSCENARIO revMinor value."""
    return _nested_int_value(
        config_path,
        "openscenario",
        "default_rev_minor",
        DEFAULT_XOSC_REV_MINOR,
    )


def load_actor_dimensions(
    actor_type: str,
    config_path: Path = CONFIG_PATH,
) -> dict[str, float]:
    """Return OpenSCENARIO bounding-box defaults for one actor type.

    Unknown actor types fall back to vehicle dimensions. Configured dimensions
    override individual numeric fields only when they are valid positive values.
    """
    defaults = DEFAULT_ACTOR_DIMENSIONS.get(
        actor_type,
        DEFAULT_ACTOR_DIMENSIONS["vehicle"],
    )
    configured = nested_config_section(
        config_path,
        "openscenario",
        "actor_dimensions",
        actor_type,
    )
    dimensions = dict(defaults)
    for key in ("width_m", "length_m", "height_m"):
        value = configured.get(key) if isinstance(configured, dict) else None
        if value is None:
            continue
        try:
            dimensions[key] = max(float(value), 0.001)
        except (TypeError, ValueError):
            pass
    return dimensions


def load_actor_xosc_defaults(
    actor_type: str,
    config_path: Path = CONFIG_PATH,
) -> dict[str, dict[str, str]]:
    """Return required OpenSCENARIO entity defaults from the project config."""
    normalized_type = (
        actor_type if actor_type in {"vehicle", "cyclist", "pedestrian"} else "vehicle"
    )
    configured = nested_config_section(
        config_path,
        "openscenario",
        "actor_defaults",
        normalized_type,
    )
    required_sections = {
        "vehicle": ("attributes", "performance", "front_axle", "rear_axle"),
        "cyclist": ("attributes", "performance", "front_axle", "rear_axle"),
        "pedestrian": ("attributes",),
    }
    missing_sections = [
        section_name
        for section_name in required_sections[normalized_type]
        if not isinstance(configured.get(section_name), dict)
        or not configured[section_name]
    ]
    if missing_sections:
        missing = ", ".join(missing_sections)
        raise ValueError(
            f"Missing openscenario.actor_defaults.{normalized_type} config sections: {missing}",
        )
    return {
        section_name: {
            str(key): str(value) for key, value in configured[section_name].items()
        }
        for section_name in required_sections[normalized_type]
    }


def load_simple_scenario_vehicle_type(
    vehicle_type_name: str,
    config_path: Path = CONFIG_PATH,
) -> dict[str, float | str]:
    """Return dimensions and actor type for a simple-scenario vehicle type.

    Unknown vehicle type names use the ``medium`` preset. Numeric config fields
    override defaults only when they parse as positive values.
    """
    vehicle_type = (
        vehicle_type_name
        if vehicle_type_name in DEFAULT_SIMPLE_SCENARIO_VEHICLE_TYPES
        else "medium"
    )
    defaults = DEFAULT_SIMPLE_SCENARIO_VEHICLE_TYPES[vehicle_type]
    configured = nested_config_section(
        config_path,
        "simple_scenario",
        "vehicle_types",
        vehicle_type,
    )
    dimensions: dict[str, float | str] = dict(defaults)
    for key in ("length_m", "width_m", "height_m"):
        value = configured.get(key) if isinstance(configured, dict) else None
        if value is None:
            continue
        try:
            dimensions[key] = max(float(value), 0.001)
        except (TypeError, ValueError):
            pass
    actor_type = configured.get("actor_type") if isinstance(configured, dict) else None
    if actor_type:
        dimensions["actor_type"] = str(actor_type)
    return dimensions
