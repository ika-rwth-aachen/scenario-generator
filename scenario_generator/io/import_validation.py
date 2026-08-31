"""Shared safety limits for data imported from untrusted files."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Protocol

from scenario_generator.config.settings import load_import_limits


_IMPORT_LIMITS = load_import_limits()
MAX_IMPORTED_ACTORS = int(_IMPORT_LIMITS["max_imported_actors"])
MAX_WAYPOINTS_PER_ACTOR = int(_IMPORT_LIMITS["max_waypoints_per_actor"])
MAX_TOTAL_WAYPOINTS = int(_IMPORT_LIMITS["max_total_waypoints"])
MAX_IMPORTED_ROADS = int(_IMPORT_LIMITS["max_imported_roads"])
MAX_POINTS_PER_ROAD = int(_IMPORT_LIMITS["max_points_per_road"])
MAX_TOTAL_MAP_POINTS = int(_IMPORT_LIMITS["max_total_map_points"])
MAX_TEXT_FIELD_CHARS = int(_IMPORT_LIMITS["max_text_field_chars"])
MAX_ACTOR_NAME_CHARS = int(_IMPORT_LIMITS["max_actor_name_chars"])
MAX_ABS_COORDINATE_M = float(_IMPORT_LIMITS["max_abs_coordinate_m"])
MAX_TIME_S = float(_IMPORT_LIMITS["max_time_s"])
MAX_SPEED_MPS = float(_IMPORT_LIMITS["max_speed_mps"])
MAX_ACTOR_DIMENSION_M = float(_IMPORT_LIMITS["max_actor_dimension_m"])


class ImportedWaypoint(Protocol):
    time_s: float
    x_m: float
    y_m: float
    speed_mps: float | None


class ImportedDimensions(Protocol):
    length_m: float
    width_m: float
    height_m: float
    parameter_declarations: str
    controller_xml: str


class ImportedRoad(Protocol):
    points: Sequence[tuple[float, float]]


def finite_number(value: object, field_name: str) -> float:
    """Convert one imported number and reject non-finite values."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite.")
    return number


def validate_imported_scenario(
    vehicles: Mapping[str, Sequence[ImportedWaypoint]],
    dimensions: Mapping[str, ImportedDimensions],
) -> None:
    """Reject imported scenario structures that are invalid or unreasonably large."""
    if len(vehicles) > MAX_IMPORTED_ACTORS:
        raise ValueError(f"Scenario exceeds the limit of {MAX_IMPORTED_ACTORS} actors.")

    total_waypoints = 0
    for name, waypoints in vehicles.items():
        if len(name) > MAX_ACTOR_NAME_CHARS:
            raise ValueError(
                f"Actor name exceeds the limit of {MAX_ACTOR_NAME_CHARS} characters."
            )
        if len(waypoints) > MAX_WAYPOINTS_PER_ACTOR:
            raise ValueError(
                f"{name}: exceeds the limit of {MAX_WAYPOINTS_PER_ACTOR} waypoints."
            )
        total_waypoints += len(waypoints)
        if total_waypoints > MAX_TOTAL_WAYPOINTS:
            raise ValueError(
                f"Scenario exceeds the limit of {MAX_TOTAL_WAYPOINTS} waypoints."
            )

        previous_time: float | None = None
        for index, waypoint in enumerate(waypoints):
            prefix = f"{name}: waypoint {index + 1}"
            time_s = finite_number(waypoint.time_s, f"{prefix} time")
            x_m = finite_number(waypoint.x_m, f"{prefix} x")
            y_m = finite_number(waypoint.y_m, f"{prefix} y")
            if not 0.0 <= time_s <= MAX_TIME_S:
                raise ValueError(f"{prefix} time is outside the supported range.")
            if abs(x_m) > MAX_ABS_COORDINATE_M or abs(y_m) > MAX_ABS_COORDINATE_M:
                raise ValueError(f"{prefix} coordinates are outside the supported range.")
            if waypoint.speed_mps is not None:
                speed_mps = finite_number(waypoint.speed_mps, f"{prefix} speed")
                if not 0.0 <= speed_mps <= MAX_SPEED_MPS:
                    raise ValueError(f"{prefix} speed is outside the supported range.")
            if previous_time is not None and time_s <= previous_time:
                raise ValueError(f"{name}: waypoint times must be unique and increasing.")
            previous_time = time_s

        actor_dimensions = dimensions.get(name)
        if actor_dimensions is None:
            continue
        for field_name in ("length_m", "width_m", "height_m"):
            value = finite_number(
                getattr(actor_dimensions, field_name), f"{name}: {field_name}"
            )
            if not 0.0 < value <= MAX_ACTOR_DIMENSION_M:
                raise ValueError(f"{name}: {field_name} is outside the supported range.")
        for field_name in ("parameter_declarations", "controller_xml"):
            if len(getattr(actor_dimensions, field_name)) > MAX_TEXT_FIELD_CHARS:
                raise ValueError(
                    f"{name}: {field_name} exceeds the supported text length."
                )


def validate_imported_map(roads: Sequence[ImportedRoad]) -> None:
    """Reject imported maps with unsafe sizes or non-finite coordinates."""
    if len(roads) > MAX_IMPORTED_ROADS:
        raise ValueError(f"Map exceeds the limit of {MAX_IMPORTED_ROADS} roads.")
    total_points = 0
    for road_index, road in enumerate(roads):
        if len(road.points) > MAX_POINTS_PER_ROAD:
            raise ValueError(
                f"Road {road_index + 1} exceeds the limit of {MAX_POINTS_PER_ROAD} points."
            )
        total_points += len(road.points)
        if total_points > MAX_TOTAL_MAP_POINTS:
            raise ValueError(f"Map exceeds the limit of {MAX_TOTAL_MAP_POINTS} points.")
        for point_index, (raw_x, raw_y) in enumerate(road.points):
            x_m = finite_number(raw_x, f"Road {road_index + 1}, point {point_index + 1} x")
            y_m = finite_number(raw_y, f"Road {road_index + 1}, point {point_index + 1} y")
            if abs(x_m) > MAX_ABS_COORDINATE_M or abs(y_m) > MAX_ABS_COORDINATE_M:
                raise ValueError(
                    f"Road {road_index + 1}, point {point_index + 1} is outside the supported range."
                )
