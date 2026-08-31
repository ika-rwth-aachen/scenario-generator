from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from scenario_generator.config.settings import (
    load_simple_scenario_dt_s,
    load_simple_scenario_duration_s,
    load_simple_scenario_lane_width_m,
    load_simple_scenario_lanelet_id_base,
    load_simple_scenario_vehicle_type,
)
from scenario_generator.io.import_validation import (
    MAX_IMPORTED_ACTORS,
    MAX_TOTAL_WAYPOINTS,
    MAX_WAYPOINTS_PER_ACTOR,
    finite_number,
)
from scenario_generator.io.importer_exporter.base import Importer
from scenario_generator.scenario_elements.road_user.road_user import (
    VehicleDimensions,
    safe_vehicle_name,
)
from scenario_generator.scenario_elements.road_user.trajectory import Waypoint


class SimpleScenarioAdapter(Importer):
    """Read ika-rwth-aachen/simple-scenario JSON configs.

    simple-scenario describes actors on a lane-based road model. The importer
    expands the native simple-scenario kinematic description into GUI waypoints.
    """

    supported_suffixes = (".json",)

    def __init__(
        self,
        lane_width_m: float | None = None,
        dt_s: float | None = None,
        lanelet_id_base: int | None = None,
        default_duration_s: float | None = None,
    ):
        self.lane_width_m = (
            load_simple_scenario_lane_width_m()
            if lane_width_m is None
            else lane_width_m
        )
        self.dt_s = load_simple_scenario_dt_s() if dt_s is None else dt_s
        self.lanelet_id_base = (
            load_simple_scenario_lanelet_id_base()
            if lanelet_id_base is None
            else lanelet_id_base
        )
        self.default_duration_s = (
            load_simple_scenario_duration_s()
            if default_duration_s is None
            else default_duration_s
        )

    def load(self, path: Path):
        return self.import_file(path)

    def import_file(
        self,
        input_path: Path,
    ) -> tuple[dict[str, list[Waypoint]], dict[str, VehicleDimensions]]:
        with input_path.open(encoding="utf-8") as file:
            config = json.load(file)
        return self.from_config(config)

    def from_config(
        self,
        config: dict[str, Any],
    ) -> tuple[dict[str, list[Waypoint]], dict[str, VehicleDimensions]]:
        road = config.get("road")
        ego = config.get("ego_configuration")
        if not isinstance(road, dict) or not isinstance(ego, dict):
            raise ValueError(  # noqa: TRY004 - invalid scenario contents use the importer error contract.
                "Simple Scenario config needs road and ego_configuration objects.",
            )
        duration = finite_number(config.get("duration", self.default_duration_s), "duration")
        dt = finite_number(config.get("dt", self.dt_s), "dt")
        if duration <= 0.0 or dt <= 0.0:
            raise ValueError("Simple Scenario duration and dt must be positive.")
        raw_vehicles = config.get("vehicles", [])
        if not isinstance(raw_vehicles, list):
            raise ValueError(  # noqa: TRY004 - invalid scenario contents use the importer error contract.
                "Simple Scenario vehicles must be a list.",
            )
        actor_count = len(raw_vehicles) + 1
        if actor_count > MAX_IMPORTED_ACTORS:
            raise ValueError(
                f"Scenario exceeds the limit of {MAX_IMPORTED_ACTORS} actors."
            )
        sample_count = math.floor(duration / dt) + 1
        if sample_count > MAX_WAYPOINTS_PER_ACTOR:
            raise ValueError(
                f"Scenario exceeds the limit of {MAX_WAYPOINTS_PER_ACTOR} waypoints per actor."
            )
        if sample_count * actor_count > MAX_TOTAL_WAYPOINTS:
            raise ValueError(
                f"Scenario exceeds the limit of {MAX_TOTAL_WAYPOINTS} waypoints."
            )

        names = self.metadata_actor_names(config)
        vehicles: dict[str, list[Waypoint]] = {}
        dimensions: dict[str, VehicleDimensions] = {}
        ego_name = names[0] if names else "ego"
        vehicles[ego_name] = self.waypoints_from_actor(ego, road, duration, dt)
        dimensions[ego_name] = self.dimensions_from_simple_type(
            str(ego.get("vehicle_type_name", "medium")),
        )
        for index, actor in enumerate(raw_vehicles):
            if not isinstance(actor, dict):
                continue
            default_name = f"vehicle_{actor.get('vehicle_id', index)}"
            name = names[index + 1] if index + 1 < len(names) else default_name
            name = safe_vehicle_name(str(name))
            vehicles[name] = self.waypoints_from_actor(actor, road, duration, dt)
            dimensions[name] = self.dimensions_from_simple_type(
                str(actor.get("vehicle_type_name", "medium")),
            )
        return vehicles, dimensions

    def waypoints_from_actor(
        self,
        actor: dict[str, Any],
        road: dict[str, Any],
        duration: float,
        dt: float,
    ) -> list[Waypoint]:
        lane_width = float(road.get("lane_width", self.lane_width_m))
        heading = float(road.get("segments", [{"heading": 0.0}])[0].get("heading", 0.0))
        x0 = float(road.get("x0", 0.0))
        y0 = float(road.get("y0", 0.0))
        lane_id = (
            int(actor.get("start_lanelet_id", self.lanelet_id_base))
            - self.lanelet_id_base
        )
        start_s = float(actor.get("start_s", 0.0))
        start_t = lane_id * lane_width + float(actor.get("start_t", 0.0))
        v0 = float(actor.get("v0", 0.0))
        a0 = float(actor.get("a0", 0.0))
        a_delay = float(actor.get("a_delay", 0.0))
        lc_direction = float(actor.get("lc_direction", 0.0))
        lc_delay = float(actor.get("lc_delay", 0.0))
        lc_duration = max(float(actor.get("lc_duration", 0.0)), 1e-9)
        sample_count = math.floor(duration / dt) + 1
        waypoints: list[Waypoint] = []
        for sample_index in range(sample_count):
            time_s = min(duration, sample_index * dt)
            accel_time = max(0.0, time_s - a_delay)
            s = start_s + v0 * time_s + 0.5 * a0 * accel_time * accel_time
            lc_fraction = min(1.0, max(0.0, (time_s - lc_delay) / lc_duration))
            lc_fraction = lc_fraction * lc_fraction * (3.0 - 2.0 * lc_fraction)
            t = start_t + lc_direction * lane_width * lc_fraction
            x_m = x0 + s * math.cos(heading) - t * math.sin(heading)
            y_m = y0 + s * math.sin(heading) + t * math.cos(heading)
            waypoints.append(Waypoint(time_s=time_s, x_m=x_m, y_m=y_m))
        return waypoints

    def dimensions_from_simple_type(self, vehicle_type_name: str) -> VehicleDimensions:
        dimensions = load_simple_scenario_vehicle_type(vehicle_type_name)
        return VehicleDimensions(
            length_m=float(dimensions["length_m"]),
            width_m=float(dimensions["width_m"]),
            height_m=float(dimensions["height_m"]),
            actor_type=str(dimensions["actor_type"]),
        )

    def metadata_actor_names(self, config: dict[str, Any]) -> list[str]:
        metadata = config.get("metadata", {})
        names = metadata.get("actor_names") if isinstance(metadata, dict) else None
        if isinstance(names, list):
            return [safe_vehicle_name(str(name)) for name in names]
        return []
