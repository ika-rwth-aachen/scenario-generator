"""FastAPI server for the browser-based scenario.generator interface."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
from copy import deepcopy
from contextvars import ContextVar
from html import escape
import math
import itertools
import importlib.util
from importlib.resources import files
import os
import re
import shutil
import tempfile
import time
from uuid import uuid4
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt

from scenario_generator.io.importer_exporter.openscenario import write_openscenario
from scenario_generator.io.import_validation import (
    validate_imported_map,
    validate_imported_scenario,
)
from scenario_generator.io.scenario_files import (
    load_openscenario_map_path,
    load_openscenario_xosc,
    load_scenario_config,
    load_scenario_config_additional_information,
    load_trajectory_json,
    load_trajectory_json_dimensions,
    load_xodr_reference_map,
    write_scenario_config,
    write_trajectory_json,
    write_xodr_map,
)
from scenario_generator.io.importer_exporter.omega_prime import OmegaPrimeAdapter
from scenario_generator.io.importer_exporter.registry import exporter_registry
from scenario_generator.io.importer_exporter.simple_scenario import SimpleScenarioAdapter
from scenario_generator.map.map import MapPolyline, ScenarioMap
from scenario_generator.metrics import min_thw_targets_by_actor, min_ttc_targets_by_actor
from scenario_generator.scenario_elements.road_user.detection_gap import DetectionGap
from scenario_generator.scenario_elements.road_user.carla_blueprints import (
    load_carla_blueprint_catalog,
)
from scenario_generator.scenario_elements.road_user.road_user import (
    VehicleDimensions,
    actor_default_dimensions,
    actor_state_from_trajectory,
    safe_vehicle_name,
)
from scenario_generator.scenario_elements.road_user.trajectory import Trajectory, Waypoint

WEBAPP_RESOURCES = files("scenario_generator.webapp")
PACKAGE_RESOURCES = files("scenario_generator")
STATIC_DIRECTORY = Path(str(WEBAPP_RESOURCES.joinpath("static")))
BRANDING_DIRECTORY = Path(str(WEBAPP_RESOURCES.joinpath("branding")))
EXPORT_DIRECTORY = Path(tempfile.gettempdir()) / "scenario.generator-web"
HELP_PATH = Path(str(WEBAPP_RESOURCES.joinpath("help.md")))
ABOUT_PATH = Path(str(WEBAPP_RESOURCES.joinpath("about.md")))
DOCUMENTATION_DIRECTORY = Path(str(WEBAPP_RESOURCES.joinpath("documentation")))
ENVIRONMENT_TEMPLATE_DIRECTORY = Path(
    str(WEBAPP_RESOURCES.joinpath("environment_templates"))
)
DEFAULT_SCENARIO_DIRECTORY = Path(
    str(WEBAPP_RESOURCES.joinpath("default_scenarios"))
)
DEFAULT_MAP_DIRECTORY = Path(str(WEBAPP_RESOURCES.joinpath("default_maps")))
POSTPROCESSING_SCRIPT_DIRECTORY = Path(
    str(PACKAGE_RESOURCES.joinpath("postprocessing_scripts"))
)
SESSION_COOKIE_NAME = "scenario_generator_session"
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def positive_environment_integer(name: str, default: int) -> int:
    """Read a positive integer setting while retaining a safe default."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def session_cookie_path() -> str:
    """Return a safe cookie path for standalone or reverse-proxy deployments."""
    configured = os.getenv("SCENARIO_GENERATOR_SESSION_COOKIE_PATH", "/").strip()
    if (
        not configured.startswith("/")
        or any(character in configured for character in ";\r\n\t ?#")
    ):
        return "/"
    return configured.rstrip("/") or "/"


SESSION_TTL_SECONDS = positive_environment_integer(
    "SCENARIO_GENERATOR_SESSION_TTL_SECONDS", 24 * 60 * 60
)
SESSION_CLEANUP_INTERVAL_SECONDS = positive_environment_integer(
    "SCENARIO_GENERATOR_SESSION_CLEANUP_INTERVAL_SECONDS", 5 * 60
)
MAX_UPLOAD_BYTES = positive_environment_integer(
    "SCENARIO_GENERATOR_MAX_UPLOAD_BYTES", 100 * 1024 * 1024
)
MAX_STRUCTURED_UPLOAD_BYTES = positive_environment_integer(
    "SCENARIO_GENERATOR_MAX_STRUCTURED_UPLOAD_BYTES", 20 * 1024 * 1024
)
UPLOAD_CHUNK_BYTES = 1024 * 1024
IMPORT_DATA_ERRORS = (
    KeyError,
    OSError,
    OverflowError,
    RuntimeError,
    SyntaxError,
    TypeError,
    ValueError,
)


def quality_checker_dynamic_peak(
    quality_checker: object, entity_name: str, metric_name: str
) -> tuple[float, float | None] | None:
    """Return a checker metric peak when dynamic trace data is available."""
    try:
        dynamic_data = quality_checker._get_dynamic_data()
        positions, times = dynamic_data[entity_name]
        dataframe = quality_checker._build_dynamic_data_df(positions, times)
        dataframe = quality_checker._calculate_acceleration_swimangle(dataframe)
        values = dataframe[metric_name].abs().dropna()
        if values.empty:
            return None
        peak_index = values.idxmax()
        raw_time = dataframe.loc[peak_index, "time"]
        peak_time = None if raw_time is None or math.isnan(raw_time) else float(raw_time)
        return float(values.loc[peak_index]), peak_time
    except Exception:  # noqa: BLE001 - checker trace details are optional.
        return None


def quality_checker_dynamic_message(
    quality_checker: object,
    entity_name: str,
    metric_name: str,
    severity: str,
    threshold: float | None,
) -> str:
    """Format one actionable dynamic Scenario Quality Checker finding."""
    is_acceleration = metric_name == "acceleration"
    label, unit = ("Acceleration", "m/s^2") if is_acceleration else ("Sideslip angle", "rad")
    explanation = "" if is_acceleration else " (heading differs from direction of travel)"
    message = f"{label} {severity} for {entity_name}{explanation}"
    details: list[str] = []
    peak = quality_checker_dynamic_peak(quality_checker, entity_name, metric_name)
    if peak is not None:
        value, time_s = peak
        detail = f"peak |{label.lower()}|={value:.3g} {unit}"
        if not is_acceleration:
            detail += f" ({math.degrees(value):.1f} deg)"
        if time_s is not None:
            detail += f" at t={time_s:.3g} s"
        details.append(detail)
    if threshold is not None:
        detail = f"{severity} limit={threshold:.3g} {unit}"
        if not is_acceleration:
            detail += f" ({math.degrees(threshold):.1f} deg)"
        details.append(detail)
    return f"{message}: {', '.join(details)}" if details else message


def quality_checker_issue_lists(quality_checker: object) -> tuple[list[str], list[str]]:
    """Return browser-ready warnings and problems from an SQC result object."""
    try:
        from quality_checker.config import Config
    except Exception:  # noqa: BLE001 - checker configuration is optional.
        Config = None
    dynamic_errors = getattr(quality_checker, "dynamic_errors", None) or ([], [], [], [])
    acceleration_warning = float(Config.ACCELERATION_WARNING_THRESHOLD) if Config else None
    acceleration_error = float(Config.ACCELERATION_ERROR_THRESHOLD) if Config else None
    sideslip_warning = float(Config.SWIMANGLE_WARNING_THRESHOLD) if Config else None
    sideslip_error = float(Config.SWIMANGLE_ERROR_THRESHOLD) if Config else None
    warnings: list[str] = []
    problems: list[str] = []
    if len(dynamic_errors) > 1:
        warnings.extend(
            quality_checker_dynamic_message(
                quality_checker, entity, "acceleration", "warning", acceleration_warning
            )
            for entity in dynamic_errors[1] or []
        )
    if len(dynamic_errors) > 3:
        warnings.extend(
            quality_checker_dynamic_message(
                quality_checker, entity, "swimangle", "warning", sideslip_warning
            )
            for entity in dynamic_errors[3] or []
        )
    warnings.extend(
        f"Position resolution warning: {entry}"
        for entry in getattr(quality_checker, "position_resolution_warnings", []) or []
    )
    labels = (
        "Missing entity definitions",
        "Identical init positions",
        "Intersecting entities",
        "Missing in/out entities",
    )
    for label, entries in zip(labels, getattr(quality_checker, "file_errors", None) or ([], [], [], [])):
        problems.extend(f"{label}: {entry}" for entry in entries or [])
    problems.extend(
        f"XSD error: {entry}"
        for entry in getattr(quality_checker, "xsd_errors", []) or []
    )
    if len(dynamic_errors) > 0:
        problems.extend(
            quality_checker_dynamic_message(
                quality_checker, entity, "acceleration", "error", acceleration_error
            )
            for entity in dynamic_errors[0] or []
        )
    if len(dynamic_errors) > 2:
        problems.extend(
            quality_checker_dynamic_message(
                quality_checker, entity, "swimangle", "error", sideslip_error
            )
            for entity in dynamic_errors[2] or []
        )
    return warnings, problems


class WebScenario:
    """In-memory scenario state owned by one browser session."""

    def __init__(self):
        self.vehicles: dict[str, list[Waypoint]] = {}
        self.dimensions: dict[str, VehicleDimensions] = {}
        self.carla_blueprints = load_carla_blueprint_catalog()
        self.map = ScenarioMap()
        self.detection_gaps: list[DetectionGap] = []
        self.additional_information: dict[str, object] = {}
        self.map_load_hint: str | None = None
        self.settings: dict[str, object] = {
            "map_mode": False,
            "time_step_s": 1.0,
            "waypoint_timing_mode": "fixed_time",
            "trajectory_calculation_mode": "forward",
            "export_2d": False,
            "show_vehicles": True,
            "show_bounding_boxes": True,
            "show_waypoint_table": True,
            "show_road_waypoint_table": True,
            "show_road_relations_table": True,
            "show_trajectory_waypoints": True,
            "show_point_indices": True,
            "show_waypoint_times": True,
            "show_speed_labels": True,
            "show_segment_average_speeds": True,
            "show_actor_names": True,
            "show_speed_profile": False,
            "show_min_ttc": False,
            "show_min_thw": False,
            "show_map": True,
            "show_road_connections": False,
            "show_road_points": False,
            "show_lane_numbers": False,
            "show_road_centerlines": True,
            "show_map_helpers": False,
            "show_detection_gaps": False,
            "lane_snap_enabled": False,
            "show_sqc_warnings": True,
            "show_sqc_errors": True,
            "tooltips_enabled": True,
            "adjustment_warnings_enabled": True,
        }
        self.actor_counter = 0
        self.add_actor()
        self.add_actor()

    def add_actor(self, requested_name: str | None = None) -> str:
        """Create an actor with a unique sanitized name and return that name."""
        base_name = safe_vehicle_name(requested_name or "vehicle")
        if not requested_name:
            self.actor_counter += 1
            base_name = f"vehicle_{self.actor_counter}"
        name = base_name
        suffix = 2
        while name in self.vehicles:
            name = f"{base_name}_{suffix}"
            suffix += 1
        # Keep default vehicles in one lane with an 8 m longitudinal gap between
        # their 4.5 m bounding boxes instead of placing them on top of each other.
        offset = (self.actor_counter - 1) * 12.5
        self.vehicles[name] = [
            Waypoint(0.0, offset, 0.0, speed_mps=13.33),
            Waypoint(1.0, offset + 13.33, 0.0, speed_mps=13.33),
            Waypoint(2.0, offset + 26.66, 0.0, speed_mps=13.33),
        ]
        self.dimensions[name] = VehicleDimensions()
        return name

    def next_actor_counter(self) -> int:
        """Return the next default actor counter used by the desktop UI."""
        counters = [
            int(match.group(1))
            for name in self.vehicles
            if (match := re.fullmatch(r"vehicle_(\d+)", name)) is not None
        ]
        return max(counters, default=len(self.vehicles))

    def road_logic_file(self, output_path: Path) -> str | None:
        """Return the configured OpenDRIVE reference for an XOSC export."""
        configured_path = str(self.additional_information.get("xosc_map_path", "")).strip()
        if configured_path:
            return configured_path
        if self.map.path is None:
            return None
        return self.map.path.name

    @staticmethod
    def project_to_polyline(
        point: tuple[float, float], points: list[tuple[float, float]]
    ) -> tuple[float, float]:
        """Return nearest normalized progress and heading on a reference polyline."""
        if len(points) < 2:
            return 0.0, 0.0
        lengths = [
            math.hypot(end[0] - start[0], end[1] - start[1])
            for start, end in itertools.pairwise(points)
        ]
        total_length = sum(lengths)
        if total_length <= 1e-9:
            return 0.0, 0.0
        best_distance_squared = math.inf
        best_progress, best_heading, traversed = 0.0, 0.0, 0.0
        for (start, end), length in zip(itertools.pairwise(points), lengths):
            dx, dy = end[0] - start[0], end[1] - start[1]
            if length <= 1e-9:
                continue
            projection = max(
                0.0,
                min(
                    1.0,
                    ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
                    / (length * length),
                ),
            )
            x_m, y_m = start[0] + projection * dx, start[1] + projection * dy
            distance_squared = (point[0] - x_m) ** 2 + (point[1] - y_m) ** 2
            if distance_squared < best_distance_squared:
                best_distance_squared = distance_squared
                best_progress = (traversed + projection * length) / total_length
                best_heading = math.atan2(dy, dx)
            traversed += length
        return best_progress, best_heading

    @staticmethod
    def point_on_polyline(
        points: list[tuple[float, float]], normalized_progress: float
    ) -> tuple[float, float, float]:
        """Evaluate a piecewise-linear reference polyline by arc-length progress."""
        if len(points) < 2:
            x_m, y_m = points[0] if points else (0.0, 0.0)
            return x_m, y_m, 0.0
        lengths = [
            math.hypot(end[0] - start[0], end[1] - start[1])
            for start, end in itertools.pairwise(points)
        ]
        target = max(0.0, min(1.0, normalized_progress)) * sum(lengths)
        traversed = 0.0
        for (start, end), length in zip(itertools.pairwise(points), lengths):
            if length <= 1e-9:
                continue
            if traversed + length >= target - 1e-9:
                fraction = max(0.0, min(1.0, (target - traversed) / length))
                return (
                    start[0] + fraction * (end[0] - start[0]),
                    start[1] + fraction * (end[1] - start[1]),
                    math.atan2(end[1] - start[1], end[0] - start[0]),
                )
            traversed += length
        start, end = points[-2:]
        return end[0], end[1], math.atan2(end[1] - start[1], end[0] - start[0])

    def update_imported_road_geometry(
        self, road: MapPolyline, previous_points: list[tuple[float, float]]
    ) -> None:
        """Deform imported lane profiles along an edited reference polyline."""
        if not road.lane_cross_sections or len(previous_points) < 2 or len(road.points) < 2:
            return
        for profile in road.lane_cross_sections:
            progress, previous_heading = self.project_to_polyline(
                (profile.x_m, profile.y_m), previous_points
            )
            old_x, old_y, _ = self.point_on_polyline(previous_points, progress)
            new_x, new_y, new_heading = self.point_on_polyline(road.points, progress)
            heading_change = new_heading - previous_heading
            residual_x, residual_y = profile.x_m - old_x, profile.y_m - old_y
            profile.x_m = (
                new_x
                + residual_x * math.cos(heading_change)
                - residual_y * math.sin(heading_change)
            )
            profile.y_m = (
                new_y
                + residual_x * math.sin(heading_change)
                + residual_y * math.cos(heading_change)
            )
            profile.heading_rad += heading_change

    @staticmethod
    def update_imported_road_lanes(road: MapPolyline) -> None:
        """Apply editable lane metadata to all imported profile samples."""
        if not road.lane_cross_sections:
            return
        lane_widths = road.lane_widths_by_id()
        lane_types = {
            lane_id: road.lane_type_for(lane_id)
            for lane_id in road.opendrive_lane_ids()
        }
        for profile in road.lane_cross_sections:
            profile.lane_widths_m = dict(lane_widths)
            profile.lane_types = dict(lane_types)

    @staticmethod
    def road_display_points(road: MapPolyline) -> list[tuple[float, float]]:
        """Return the smoothed editable reference line used by the desktop canvas."""
        if road.kind != "reference" or len(road.points) < 2:
            return list(road.points)
        waypoints = [
            Waypoint(float(index), x_m, y_m)
            for index, (x_m, y_m) in enumerate(road.points)
        ]
        return [
            (x_m, y_m)
            for _time_s, x_m, y_m in Trajectory(
                waypoints=waypoints
            ).sampled_curve_points()
        ]

    def road_by_name(self, name: str) -> MapPolyline | None:
        """Resolve an editable reference road by name or one-based index."""
        reference_index = 0
        for road in self.map.roads:
            if road.kind != "reference":
                continue
            reference_index += 1
            if road.name == name or str(reference_index) == name:
                return road
        return None

    def sync_moved_road_endpoint(
        self, road: MapPolyline, point_index: int
    ) -> None:
        """Move the opposite endpoint through an unqualified road connection."""
        if road.kind != "reference" or len(road.points) < 2:
            return
        if point_index == 0 and road.predecessor_road and not road.predecessor_lane_links:
            predecessor = self.road_by_name(road.predecessor_road)
            if (
                predecessor is not None
                and predecessor.points
                and not predecessor.successor_lane_links
            ):
                previous_points = list(predecessor.points)
                predecessor.points[-1] = road.points[0]
                self.update_imported_road_geometry(predecessor, previous_points)
        elif (
            point_index == len(road.points) - 1
            and road.successor_road
            and not road.successor_lane_links
        ):
            successor = self.road_by_name(road.successor_road)
            if (
                successor is not None
                and successor.points
                and not successor.predecessor_lane_links
            ):
                previous_points = list(successor.points)
                successor.points[0] = road.points[-1]
                self.update_imported_road_geometry(successor, previous_points)

    def clear_road_predecessor(self, road: MapPolyline) -> None:
        """Clear a predecessor relation and its reciprocal successor relation."""
        predecessor_name = road.predecessor_road
        road.predecessor_road = ""
        road.predecessor_lane_links = ""
        predecessor = self.road_by_name(predecessor_name)
        if predecessor is not None and predecessor.successor_road == road.name:
            predecessor.successor_road = ""
            predecessor.successor_lane_links = ""

    def clear_road_successor(self, road: MapPolyline) -> None:
        """Clear a successor relation and its reciprocal predecessor relation."""
        successor_name = road.successor_road
        road.successor_road = ""
        road.successor_lane_links = ""
        successor = self.road_by_name(successor_name)
        if successor is not None and successor.predecessor_road == road.name:
            successor.predecessor_road = ""
            successor.predecessor_lane_links = ""

    def set_road_predecessor(self, road: MapPolyline, target_name: str) -> None:
        """Set one predecessor and maintain the matching successor relation."""
        target_name = target_name.strip()
        if not target_name:
            self.clear_road_predecessor(road)
            return
        predecessor = self.road_by_name(target_name)
        if predecessor is None or predecessor is road:
            raise ValueError(f"Unknown predecessor road: {target_name}")
        self.clear_road_predecessor(road)
        self.clear_road_successor(predecessor)
        road.predecessor_road = predecessor.name
        predecessor.successor_road = road.name

    def set_road_successor(self, road: MapPolyline, target_name: str) -> None:
        """Set one successor and maintain the matching predecessor relation."""
        target_name = target_name.strip()
        if not target_name:
            self.clear_road_successor(road)
            return
        successor = self.road_by_name(target_name)
        if successor is None or successor is road:
            raise ValueError(f"Unknown successor road: {target_name}")
        self.clear_road_successor(road)
        self.clear_road_predecessor(successor)
        road.successor_road = successor.name
        successor.predecessor_road = road.name

    def validate_lane_links(
        self, road: MapPolyline, target_name: str, link_spec: str
    ) -> None:
        """Validate signed OpenDRIVE lane links against both connected roads."""
        if not link_spec.strip() or not target_name.strip():
            return
        target = self.road_by_name(target_name)
        if target is None:
            raise ValueError(f"Unknown linked road: {target_name}")
        source_ids, target_ids = set(road.opendrive_lane_ids()), set(target.opendrive_lane_ids())
        for part in link_spec.replace(",", ";").split(";"):
            part = part.strip()
            if not part:
                continue
            source_text, target_text = part.split("->", 1) if "->" in part else (part, part)
            try:
                source_lane, target_lane = int(source_text.strip()), int(target_text.strip())
            except ValueError as exc:
                raise ValueError("Lane links must use lane IDs like -1 or -1->-2.") from exc
            if source_lane not in source_ids:
                raise ValueError(f"Lane {source_lane} does not exist on {road.name}.")
            if target_lane not in target_ids:
                raise ValueError(f"Lane {target_lane} does not exist on {target.name}.")

    def replace_waypoints(self, name: str, values: list[dict[str, Any]]):
        """Replace an actor's trajectory with waypoints parsed from API values."""
        if name not in self.vehicles:
            raise KeyError(name)
        waypoints = [
            Waypoint(
                time_s=float(value["time_s"]),
                x_m=float(value["x_m"]),
                y_m=float(value["y_m"]),
                speed_mps=(
                    None
                    if value.get("speed_mps") is None
                    else max(float(value["speed_mps"]), 0.0)
                ),
            )
            for value in values
        ]
        self.vehicles[name] = sorted(waypoints, key=lambda item: item.time_s)

    def next_waypoint_time(self, name: str, x_m: float, y_m: float) -> float:
        """Calculate the next timestamp with the configured timing mode."""
        waypoints = sorted(self.vehicles[name], key=lambda point: point.time_s)
        if not waypoints:
            return 0.0
        step = max(float(self.settings["time_step_s"]), 0.001)
        last = waypoints[-1]
        if self.settings["waypoint_timing_mode"] != "constant_speed" or len(waypoints) < 2:
            return last.time_s + step
        previous = waypoints[-2]
        previous_duration = last.time_s - previous.time_s
        previous_distance = ((last.x_m - previous.x_m) ** 2 + (last.y_m - previous.y_m) ** 2) ** 0.5
        next_distance = ((x_m - last.x_m) ** 2 + (y_m - last.y_m) ** 2) ** 0.5
        if previous_duration <= 0.0 or previous_distance <= 1e-9 or next_distance <= 1e-9:
            return last.time_s + step
        return last.time_s + max(next_distance / (previous_distance / previous_duration), 0.001)

    def add_waypoint(
        self,
        name: str,
        x_m: float,
        y_m: float,
        snap_distance_m: float = 0.0,
    ) -> None:
        """Append a control point using the configured timing calculation."""
        reference_speed: float | None = None
        if snap_distance_m > 0.0:
            snap = self.map.nearest_compatible_lane(
                x_m,
                y_m,
                self.dimensions[name].actor_type,
                snap_distance_m,
            )
            if snap is not None:
                x_m, y_m = snap.x_m, snap.y_m
                reference_speed = snap.speed_limit_mps
        waypoints = self.vehicles[name]
        if not waypoints:
            waypoints.append(Waypoint(0.0, x_m, y_m, speed_mps=0.0))
            return
        previous = waypoints[-1]
        next_time = self.next_waypoint_time(name, x_m, y_m)
        distance = ((x_m - previous.x_m) ** 2 + (y_m - previous.y_m) ** 2) ** 0.5
        duration = max(next_time - previous.time_s, 0.001)
        previous_speed = float(previous.speed_mps or 0.0)
        if reference_speed is not None:
            speed = float(reference_speed)
        elif self.settings["waypoint_timing_mode"] == "constant_speed" and len(waypoints) >= 2:
            before_previous = waypoints[-2]
            previous_duration = previous.time_s - before_previous.time_s
            previous_distance = math.hypot(
                previous.x_m - before_previous.x_m,
                previous.y_m - before_previous.y_m,
            )
            if previous_duration > 0.0 and previous_distance > 1e-9:
                previous_average_speed = previous_distance / previous_duration
                speed = 2.0 * previous_average_speed - previous_speed
            else:
                speed = 2.0 * distance / duration - previous_speed
        else:
            speed = 2.0 * distance / duration - previous_speed
        # Closely spaced points with a fixed time step can make the endpoint
        # formula negative. A negative physical speed is never useful here, so
        # accept the point at the nearest valid boundary instead of rejecting
        # an otherwise intentional canvas or keyboard edit.
        speed = max(speed, 0.0)
        waypoints.append(Waypoint(next_time, x_m, y_m, speed_mps=speed))

    @staticmethod
    def waypoint_snapshot(
        waypoints: list[Waypoint],
    ) -> list[Waypoint]:
        """Copy mutable trajectory control points before a derived update."""
        return [
            Waypoint(point.time_s, point.x_m, point.y_m, point.speed_mps)
            for point in waypoints
        ]

    def restore_waypoints(self, name: str, snapshot: list[Waypoint]) -> None:
        """Restore a trajectory after an infeasible table edit."""
        self.vehicles[name] = snapshot

    def recalculate_times_from_speeds(self, name: str, changed_index: int) -> None:
        """Propagate a point speed or position edit in the configured direction."""
        waypoints = self.vehicles[name]
        if len(waypoints) <= 1:
            if waypoints:
                waypoints[0].speed_mps = 0.0
            return
        waypoints.sort(key=lambda point: point.time_s)
        Trajectory.validate_explicit_speeds(waypoints)
        trajectory = Trajectory(waypoints=waypoints)
        if self.settings["trajectory_calculation_mode"] == "backward":
            segment_index = min(changed_index, len(waypoints) - 2)
            for index in range(segment_index, -1, -1):
                distance = trajectory.profile_segment_curve_distance(index)
                speed_sum = float(waypoints[index].speed_mps) + float(
                    waypoints[index + 1].speed_mps
                )
                if distance <= trajectory.MIN_DISTANCE_M:
                    raise ValueError("Consecutive trajectory points must not share a position.")
                if speed_sum <= trajectory.MIN_SPEED_SUM_MPS:
                    raise ValueError(
                        "A moving segment cannot have zero speed at both endpoints."
                    )
                waypoints[index].time_s = (
                    waypoints[index + 1].time_s - 2.0 * distance / speed_sum
                )
            if waypoints[0].time_s < 0.0:
                correction = -waypoints[0].time_s
                for point in waypoints:
                    point.time_s += correction
            return
        segment_index = max(changed_index - 1, 0)
        for index in range(segment_index, len(waypoints) - 1):
            distance = trajectory.profile_segment_curve_distance(index)
            speed_sum = float(waypoints[index].speed_mps) + float(
                waypoints[index + 1].speed_mps
            )
            if distance <= trajectory.MIN_DISTANCE_M:
                raise ValueError("Consecutive trajectory points must not share a position.")
            if speed_sum <= trajectory.MIN_SPEED_SUM_MPS:
                raise ValueError("A moving segment cannot have zero speed at both endpoints.")
            waypoints[index + 1].time_s = (
                waypoints[index].time_s + 2.0 * distance / speed_sum
            )

    def synchronize_times_from_speeds(self, name: str) -> None:
        """Apply the current explicit speed-profile time model to one actor."""
        waypoints = self.vehicles[name]
        if len(waypoints) <= 1:
            if waypoints:
                waypoints[0].speed_mps = 0.0
            return
        trajectory = Trajectory(waypoints=waypoints)
        trajectory.synchronize_times_from_speeds(max(0.0, waypoints[0].time_s))

    def set_waypoint_speed(self, name: str, point_index: int, speed_mps: float) -> None:
        """Apply a profile-node speed edit using the active propagation direction."""
        waypoints = self.vehicles[name]
        if not 0 <= point_index < len(waypoints) or speed_mps < 0.0:
            raise ValueError("Trajectory point speed must be nonnegative.")
        snapshot = self.waypoint_snapshot(waypoints)
        waypoints.sort(key=lambda point: point.time_s)
        try:
            waypoints[point_index].speed_mps = speed_mps
            self.recalculate_times_from_speeds(name, point_index)
        except (IndexError, TypeError, ValueError):
            self.restore_waypoints(name, snapshot)
            raise

    def set_waypoint_geometry(
        self, name: str, point_index: int, x_m: float, y_m: float
    ) -> None:
        """Move one control point and preserve its explicit speed profile."""
        waypoints = self.vehicles[name]
        if not 0 <= point_index < len(waypoints):
            raise ValueError("Unknown trajectory point.")
        snapshot = self.waypoint_snapshot(waypoints)
        try:
            waypoints[point_index].x_m = x_m
            waypoints[point_index].y_m = y_m
            self.recalculate_times_from_speeds(name, point_index)
        except (IndexError, TypeError, ValueError):
            self.restore_waypoints(name, snapshot)
            raise

    def set_waypoint_time(self, name: str, point_index: int, time_s: float) -> None:
        """Shift the relevant timestamp block and derive endpoint speeds."""
        waypoints = self.vehicles[name]
        if not 0 <= point_index < len(waypoints):
            raise ValueError("Unknown trajectory point.")
        waypoints.sort(key=lambda point: point.time_s)
        snapshot = self.waypoint_snapshot(waypoints)
        if len(waypoints) <= 1:
            waypoints[0].time_s = max(0.0, time_s)
            return

        try:
            delta = time_s - waypoints[point_index].time_s
            if self.settings["trajectory_calculation_mode"] == "backward":
                for index in range(0, point_index + 1):
                    waypoints[index].time_s += delta
                for index in range(point_index + 1, len(waypoints)):
                    if waypoints[index].time_s <= waypoints[index - 1].time_s:
                        waypoints[index].time_s += delta
                        if waypoints[index].time_s <= waypoints[index - 1].time_s:
                            waypoints[index].time_s = waypoints[index - 1].time_s + 0.001
            else:
                for index in range(point_index, len(waypoints)):
                    waypoints[index].time_s += delta
                for index in range(point_index - 1, -1, -1):
                    if waypoints[index].time_s >= waypoints[index + 1].time_s:
                        waypoints[index].time_s += delta
                        if waypoints[index].time_s >= waypoints[index + 1].time_s:
                            waypoints[index].time_s = max(0.0, waypoints[index + 1].time_s - 0.001)

            if waypoints[0].time_s < 0.0:
                correction = -waypoints[0].time_s
                for point in waypoints:
                    point.time_s += correction

            Trajectory(waypoints=waypoints).validate_moving_waypoints(waypoints)
            trajectory = Trajectory(waypoints=waypoints)
            if self.settings["trajectory_calculation_mode"] == "backward":
                target_segment = min(point_index, len(waypoints) - 2)
                for index in range(target_segment, -1, -1):
                    duration = waypoints[index + 1].time_s - waypoints[index].time_s
                    average = trajectory.profile_segment_curve_distance(index) / duration
                    speed = 2.0 * average - float(waypoints[index + 1].speed_mps)
                    if speed < 0.0:
                        waypoints[index].speed_mps = 0.0
                        speed_sum = float(waypoints[index + 1].speed_mps)
                        if speed_sum > 1e-9:
                            seg_dist = trajectory.profile_segment_curve_distance(index)
                            waypoints[index].time_s = waypoints[index + 1].time_s - 2.0 * seg_dist / speed_sum
                    else:
                        waypoints[index].speed_mps = speed
                if waypoints[0].time_s < 0.0:
                    correction = -waypoints[0].time_s
                    for point in waypoints:
                        point.time_s += correction
            else:
                target_segment = max(point_index - 1, 0)
                for index in range(target_segment, len(waypoints) - 1):
                    duration = waypoints[index + 1].time_s - waypoints[index].time_s
                    average = trajectory.profile_segment_curve_distance(index) / duration
                    speed = 2.0 * average - float(waypoints[index].speed_mps)
                    if speed < 0.0:
                        waypoints[index + 1].speed_mps = 0.0
                        speed_sum = float(waypoints[index].speed_mps)
                        if speed_sum > 1e-9:
                            seg_dist = trajectory.profile_segment_curve_distance(index)
                            waypoints[index + 1].time_s = waypoints[index].time_s + 2.0 * seg_dist / speed_sum
                    else:
                        waypoints[index + 1].speed_mps = speed
            Trajectory(waypoints=waypoints).validate_moving_waypoints(waypoints)
        except (IndexError, TypeError, ValueError):
            self.restore_waypoints(name, snapshot)
            raise

    def insert_waypoint(self, name: str, insert_index: int) -> None:
        """Insert the desktop editor's extrapolated or interpolated control point."""
        waypoints = self.vehicles[name]
        insert_index = min(max(insert_index, 0), len(waypoints))
        step = max(float(self.settings["time_step_s"]), 0.001)
        if not waypoints:
            point = Waypoint(0.0, 0.0, 0.0, 0.0)
        elif insert_index <= 0:
            first = waypoints[0]
            if len(waypoints) >= 2:
                following = waypoints[1]
                x_m, y_m = 2.0 * first.x_m - following.x_m, 2.0 * first.y_m - following.y_m
            else:
                x_m, y_m = first.x_m - 1.0, first.y_m
            point = Waypoint(max(first.time_s - step, 0.0), x_m, y_m, max(0.0, float(first.speed_mps or 0.0)))
        elif insert_index >= len(waypoints):
            last = waypoints[-1]
            if len(waypoints) >= 2:
                previous = waypoints[-2]
                x_m, y_m = 2.0 * last.x_m - previous.x_m, 2.0 * last.y_m - previous.y_m
            else:
                x_m, y_m = last.x_m + 1.0, last.y_m
            speed = max(0.0, float(last.speed_mps or 0.0))
            if speed <= 1e-9:
                speed = max(2.0 * math.hypot(x_m - last.x_m, y_m - last.y_m) / step, 0.001)
            point = Waypoint(last.time_s + step, x_m, y_m, speed)
        else:
            previous, following = waypoints[insert_index - 1], waypoints[insert_index]
            point = Waypoint(
                (previous.time_s + following.time_s) / 2.0,
                (previous.x_m + following.x_m) / 2.0,
                (previous.y_m + following.y_m) / 2.0,
                (float(previous.speed_mps or 0.0) + float(following.speed_mps or 0.0)) / 2.0,
            )
        snapshot = self.waypoint_snapshot(waypoints)
        try:
            waypoints.insert(insert_index, point)
            self.recalculate_times_from_speeds(name, insert_index)
        except (IndexError, TypeError, ValueError):
            self.restore_waypoints(name, snapshot)
            raise

    def delete_waypoint(self, name: str, point_index: int) -> None:
        """Remove one point and recalculate adjacent trajectory timing."""
        waypoints = self.vehicles[name]
        if not 0 <= point_index < len(waypoints):
            raise ValueError("Unknown trajectory point.")
        snapshot = self.waypoint_snapshot(waypoints)
        try:
            del waypoints[point_index]
            if len(waypoints) == 1:
                waypoints[0].speed_mps = 0.0
            elif waypoints:
                self.recalculate_times_from_speeds(
                    name, min(point_index, len(waypoints) - 1)
                )
        except (IndexError, TypeError, ValueError):
            self.restore_waypoints(name, snapshot)
            raise

    def set_segment_speed(self, name: str, segment_index: int, speed_mps: float) -> None:
        """Change one segment speed by shifting timestamps like the desktop editor."""
        if speed_mps <= 0.0:
            raise ValueError("Segment speed must be greater than zero.")
        waypoints = self.vehicles[name]
        ordered = sorted(range(len(waypoints)), key=lambda index: waypoints[index].time_s)
        if not 0 <= segment_index < len(ordered) - 1:
            raise ValueError("Unknown trajectory segment.")
        start = waypoints[ordered[segment_index]]
        end = waypoints[ordered[segment_index + 1]]
        if start.speed_mps is None or end.speed_mps is None:
            raise ValueError("Each trajectory point needs a speed.")
        proposed_start_speed = float(start.speed_mps)
        proposed_end_speed = float(end.speed_mps)
        if self.settings["trajectory_calculation_mode"] == "backward":
            proposed_start_speed = 2.0 * speed_mps - proposed_end_speed
        else:
            proposed_end_speed = 2.0 * speed_mps - proposed_start_speed
        if min(proposed_start_speed, proposed_end_speed) < 0.0:
            raise ValueError("The segment average would require a negative point speed.")
        snapshot = self.waypoint_snapshot(waypoints)
        try:
            start.speed_mps = proposed_start_speed
            end.speed_mps = proposed_end_speed
            self.synchronize_times_from_speeds(name)
        except (IndexError, TypeError, ValueError):
            self.restore_waypoints(name, snapshot)
            raise

    def trajectory_payload(self) -> dict[str, dict[str, object]]:
        """Serialize all actor trajectories and dimensions for scenario exporters."""
        payload: dict[str, dict[str, object]] = {}
        for name, waypoints in self.vehicles.items():
            dimensions = self.dimensions[name]
            if not waypoints:
                continue
            trajectory = (
                Trajectory(
                    waypoints=waypoints, parked_yaw_rad=dimensions.parked_yaw_rad
                ).as_parked_series()
                if len(waypoints) == 1
                else Trajectory(waypoints=waypoints).as_moving_series()
            )
            trajectory["actor_type"] = dimensions.actor_type
            trajectory["detected"] = [
                not any(gap.vehicle_name == name and gap.contains(time_s) for gap in self.detection_gaps)
                for time_s in trajectory["time_s"]
            ]
            trajectory["coordinate_reference"] = "bounding_box_center"
            trajectory["carla_blueprint"] = dimensions.carla_blueprint
            trajectory["xosc_export_mode"] = dimensions.xosc_export_mode
            trajectory["parameter_declarations"] = dimensions.parameter_declarations
            trajectory["controller_name"] = dimensions.controller_name
            trajectory["controller_xml"] = dimensions.controller_xml
            trajectory["dimensions"] = dimensions.as_dict()
            trajectory["dimensions"]["parked_yaw_rad"] = dimensions.parked_yaw_rad
            use_map_elevation = self.map.has_any_roads() and not self.settings["export_2d"]
            elevations = [
                self.map.elevation_at(x_m, y_m) if use_map_elevation else 0.0
                for x_m, y_m in zip(trajectory["x_m"], trajectory["y_m"])
            ]
            trajectory["z_m"] = self.interpolate_short_elevation_gaps(elevations)
            route_elevations = self.interpolate_short_elevation_gaps(
                [self.map.elevation_at(point.x_m, point.y_m) if use_map_elevation else 0.0 for point in waypoints]
            )
            trajectory["route_waypoints"] = [
                {
                    "time_s": point.time_s,
                    "x_m": point.x_m,
                    "y_m": point.y_m,
                    "z_m": route_elevations[index],
                }
                for index, point in enumerate(waypoints)
            ]
            payload[name] = trajectory
        return payload

    @staticmethod
    def interpolate_short_elevation_gaps(elevations: list[float | None]) -> list[float]:
        """Interpolate up to two missing map elevations between valid road samples."""
        result = [0.0 if value is None else float(value) for value in elevations]
        index = 0
        while index < len(elevations):
            if elevations[index] is not None:
                index += 1
                continue
            end = index
            while end < len(elevations) and elevations[end] is None:
                end += 1
            if index > 0 and end < len(elevations) and end - index <= 2:
                start_elevation = result[index - 1]
                end_elevation = result[end]
                for gap_index in range(index, end):
                    fraction = (gap_index - index + 1) / (end - index + 1)
                    result[gap_index] = start_elevation + fraction * (end_elevation - start_elevation)
            index = end
        return result

    @staticmethod
    def lane_point(profile: object, offset_m: float) -> tuple[float, float]:
        """Return a world point at one OpenDRIVE lateral profile offset."""
        return (
            profile.x_m - math.sin(profile.heading_rad) * offset_m,
            profile.y_m + math.cos(profile.heading_rad) * offset_m,
        )

    @staticmethod
    def lane_bounds(profile: object, lane_id: int) -> tuple[float, float]:
        """Return inner and outer offsets for one OpenDRIVE lane."""
        width = max(0.0, float(profile.lane_widths_m.get(lane_id, 0.0)))
        if lane_id > 0:
            inner = profile.lane_offset_m + sum(
                max(0.0, float(profile.lane_widths_m.get(index, 0.0)))
                for index in range(1, lane_id)
            )
            return inner, inner + width
        inner = profile.lane_offset_m - sum(
            max(0.0, float(profile.lane_widths_m.get(-index, 0.0)))
            for index in range(1, abs(lane_id))
        )
        return inner, inner - width

    def road_render_geometry(self, road: MapPolyline) -> dict[str, object]:
        """Serialize cached, precise lane geometry for the browser canvas."""
        profiles = road.lane_cross_sections
        if len(profiles) < 2:
            return {"lanes": [], "centerline": road.points, "labels": []}
        lanes: list[dict[str, object]] = []
        labels: list[dict[str, object]] = []
        lane_ids = sorted(
            set().union(*(profile.lane_widths_m for profile in profiles)),
            key=lambda lane_id: (lane_id < 0, abs(lane_id)),
        )
        for lane_id in lane_ids:
            for lane_type, run_profiles in self.imported_lane_profile_runs(
                profiles, lane_id
            ):
                inner = [
                    self.lane_point(profile, self.lane_bounds(profile, lane_id)[0])
                    for profile in run_profiles
                ]
                outer = [
                    self.lane_point(profile, self.lane_bounds(profile, lane_id)[1])
                    for profile in run_profiles
                ]
                lanes.append(
                    {
                        "id": lane_id,
                        "type": lane_type,
                        "points": inner + list(reversed(outer)),
                    }
                )
                middle = run_profiles[len(run_profiles) // 2]
                bounds = self.lane_bounds(middle, lane_id)
                label_x, label_y = self.lane_point(middle, sum(bounds) / 2.0)
                labels.append({"id": lane_id, "x_m": label_x, "y_m": label_y})
        return {
            "lanes": lanes,
            "centerline": [self.lane_point(profile, profile.lane_offset_m) for profile in profiles],
            "labels": labels,
        }

    @staticmethod
    def imported_lane_profile_runs(
        profiles: list[object], lane_id: int
    ) -> list[tuple[str, list[object]]]:
        """Group visible lane samples into contiguous runs of the same OpenDRIVE type."""
        runs: list[tuple[str, list[object]]] = []
        active_type: str | None = None
        active_profiles: list[object] = []
        for first, second in itertools.pairwise(profiles):
            first_width = max(0.0, float(first.lane_widths_m.get(lane_id, 0.0)))
            second_width = max(0.0, float(second.lane_widths_m.get(lane_id, 0.0)))
            if first_width <= 1e-4 and second_width <= 1e-4:
                active_type = None
                active_profiles = []
                continue
            lane_type = first.lane_types.get(
                lane_id, second.lane_types.get(lane_id, "driving")
            )
            if (
                active_type == lane_type
                and active_profiles
                and active_profiles[-1] is first
            ):
                active_profiles.append(second)
                continue
            active_type = lane_type
            active_profiles = [first, second]
            runs.append((lane_type, active_profiles))
        return runs

    def actor_snapshot(self, name: str, waypoints: list[Waypoint]) -> dict[str, object]:
        """Serialize one actor with the exact curve used by desktop playback."""
        dimensions = self.dimensions[name]
        trajectory = Trajectory(
            waypoints=waypoints, parked_yaw_rad=dimensions.parked_yaw_rad
        )
        curve = trajectory.as_series() if waypoints else {}
        return {
            "name": name,
            "waypoints": [point.__dict__ for point in waypoints],
            "segment_speeds_mps": trajectory.segment_average_speeds()
            if len(waypoints) > 1
            else [],
            "profile_distances_m": trajectory.cumulative_waypoint_distances(),
            "segment_midpoints": [
                trajectory.curve_point_for_segment(index, 0.5)
                for index in range(len(waypoints) - 1)
            ],
            "curve": curve,
            "dimensions": dimensions.as_dict(),
        }

    def snapshot(self) -> dict[str, object]:
        """Serialize the complete current scenario state for the browser client."""
        return {
            "actors": [
                self.actor_snapshot(name, waypoints)
                for name, waypoints in self.vehicles.items()
            ],
            "map": {
                "path": str(self.map.path) if self.map.path else "",
                "edit_enabled": self.map.edit_enabled,
                "roads": [
                    {
                        "name": road.name,
                        "points": road.points,
                        "display_points": self.road_display_points(road),
                        "lane_types": road.lane_types,
                        "width_m": road.width_m,
                        "kind": road.kind,
                        "lane_count": road.lane_count,
                        "lane_width_m": road.lane_width_m,
                        "lane_widths_m": road.lane_widths_m,
                        "predecessor_road": road.predecessor_road,
                        "successor_road": road.successor_road,
                        "predecessor_lane_links": road.predecessor_lane_links,
                        "successor_lane_links": road.successor_lane_links,
                        "render_geometry": self.road_render_geometry(road),
                    }
                    for road in self.map.display_roads
                ],
            },
            "additional_information": self.additional_information,
            "map_load_hint": self.map_load_hint,
            "detection_gaps": [gap.__dict__ for gap in self.detection_gaps],
            "settings": self.settings,
            "carla_blueprints": self.carla_blueprints,
        }


active_scenario: ContextVar[WebScenario | None] = ContextVar(
    "active_web_scenario", default=None
)
active_session_id: ContextVar[str | None] = ContextVar(
    "active_web_session_id", default=None
)


class ScenarioProxy:
    """Expose the scenario bound to the request currently being processed."""

    @staticmethod
    def current() -> WebScenario:
        """Return the session scenario selected by the request middleware."""
        current_scenario = active_scenario.get()
        if current_scenario is None:
            raise RuntimeError("No scenario is bound to the current request.")
        return current_scenario

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute reads to the scenario for the active session."""
        return getattr(self.current(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Delegate attribute writes to the scenario for the active session."""
        setattr(self.current(), name, value)


class ScenarioSession:
    """Pair one session-owned scenario with its request serialization lock."""

    def __init__(self, last_access: float) -> None:
        self.scenario = WebScenario()
        self.lock = asyncio.Lock()
        self.last_access = last_access


class ScenarioStore:
    """Create and retain independent in-memory scenarios by browser session ID."""

    def __init__(
        self,
        export_directory: Path,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        cleanup_interval_seconds: int = SESSION_CLEANUP_INTERVAL_SECONDS,
    ) -> None:
        self.sessions: dict[str, ScenarioSession] = {}
        self.export_directory = export_directory
        self.ttl_seconds = ttl_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self.last_cleanup = 0.0

    def get(self, session_id: str) -> ScenarioSession:
        """Return the existing session state or create it on first access."""
        if not valid_session_id(session_id):
            raise ValueError("Invalid session ID")
        now = time.monotonic()
        self.cleanup_expired(now)
        if session_id not in self.sessions:
            self.sessions[session_id] = ScenarioSession(now)
        session = self.sessions[session_id]
        session.last_access = now
        return session

    def cleanup_expired(self, now: float | None = None, *, force: bool = False) -> None:
        """Remove inactive, unlocked sessions and their temporary files."""
        current_time = time.monotonic() if now is None else now
        if not force and current_time - self.last_cleanup < self.cleanup_interval_seconds:
            return
        self.last_cleanup = current_time
        expired_ids = [
            session_id
            for session_id, session in self.sessions.items()
            if not session.lock.locked()
            and current_time - session.last_access >= self.ttl_seconds
        ]
        for session_id in expired_ids:
            del self.sessions[session_id]
            shutil.rmtree(self.export_directory / session_id, ignore_errors=True)

        self.cleanup_orphaned_directories()

    def delete(self, session_id: str) -> None:
        """Immediately remove one session's in-memory state and temporary files."""
        if not valid_session_id(session_id):
            raise ValueError("Invalid session ID")
        self.sessions.pop(session_id, None)
        shutil.rmtree(self.export_directory / session_id, ignore_errors=True)

    def cleanup_orphaned_directories(self) -> None:
        """Remove expired session directories left behind by earlier processes."""
        if not self.export_directory.is_dir():
            return
        cutoff = time.time() - self.ttl_seconds
        for path in self.export_directory.iterdir():
            if (
                not path.is_dir()
                or not valid_session_id(path.name)
                or path.name in self.sessions
            ):
                continue
            try:
                expired = path.stat().st_mtime <= cutoff
            except OSError:
                continue
            if expired:
                shutil.rmtree(path, ignore_errors=True)

    def touch(self, session_id: str) -> None:
        """Refresh in-memory and on-disk activity timestamps for one session."""
        session = self.sessions.get(session_id)
        if session is not None:
            session.last_access = time.monotonic()
        session_directory = self.export_directory / session_id
        if session_directory.is_dir():
            try:
                os.utime(session_directory)
            except OSError:
                pass


scenario_store = ScenarioStore(EXPORT_DIRECTORY)
scenario = ScenarioProxy()


async def cleanup_sessions_periodically() -> None:
    """Run session cleanup independently of incoming request traffic."""
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)
        scenario_store.cleanup_expired(force=True)


@asynccontextmanager
async def application_lifespan(_app: FastAPI):
    """Own the periodic cleanup task for the application's lifetime."""
    scenario_store.cleanup_expired(force=True)
    cleanup_task = asyncio.create_task(cleanup_sessions_periodically())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(title="scenario.generator", version="0.1.0", lifespan=application_lifespan)
app.mount("/assets", StaticFiles(directory=STATIC_DIRECTORY), name="assets")
app.mount("/branding", StaticFiles(directory=BRANDING_DIRECTORY), name="branding")
app.mount(
    "/docs/images",
    StaticFiles(directory=DOCUMENTATION_DIRECTORY / "images"),
    name="documentation-images",
)
app.mount(
    "/docs/templates",
    StaticFiles(directory=DOCUMENTATION_DIRECTORY / "templates"),
    name="documentation-templates",
)
app.mount(
    "/docs/examples",
    StaticFiles(directory=DOCUMENTATION_DIRECTORY / "examples"),
    name="documentation-examples",
)


def session_export_directory() -> Path:
    """Return the active session's isolated directory for uploads and exports."""
    session_id = active_session_id.get()
    if session_id is None:
        return EXPORT_DIRECTORY
    if not valid_session_id(session_id):
        raise RuntimeError("Invalid active session ID")
    return EXPORT_DIRECTORY / session_id


def valid_session_id(session_id: str | None) -> bool:
    """Return whether a cookie value is one of the server's UUID4 hex IDs."""
    return bool(session_id and SESSION_ID_PATTERN.fullmatch(session_id))


def secure_cookie_for_request(request: Request) -> bool:
    """Select the Secure cookie flag from deployment configuration or HTTPS."""
    configured = os.getenv("SCENARIO_GENERATOR_SECURE_COOKIES", "auto").strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    return request.url.scheme == "https"


async def store_upload(
    file: UploadFile,
    destination: Path,
    max_bytes: int | None = None,
) -> None:
    """Stream an upload to disk and reject files exceeding the configured limit."""
    effective_max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    destination.parent.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                total_bytes += len(chunk)
                if total_bytes > effective_max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds the {effective_max_bytes} byte limit",
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def safe_output_name(name: str) -> str:
    """Normalize an export filename while preserving user-visible hyphens."""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
    return normalized or "scenario"


def safe_upload_name(filename: str | None, default_name: str) -> str:
    """Return a bounded, path-free name for one uploaded file."""
    original_name = Path(filename or default_name).name
    suffix = Path(original_name).suffix.lower()
    stem = safe_output_name(Path(original_name).stem)[:128]
    return f"{stem}{suffix}"


def confined_session_map_path(map_path: Path) -> Path:
    """Resolve a referenced map without allowing access outside this session."""
    session_directory = session_export_directory().resolve(strict=False)
    candidate = map_path.expanduser().resolve(strict=False)
    try:
        candidate.relative_to(session_directory)
    except ValueError as exc:
        raise ValueError(
            "Referenced map paths must stay inside the active session directory."
        ) from exc
    if candidate.suffix.lower() not in {".xodr", ".xml"}:
        raise ValueError("Referenced maps must use the .xodr or .xml extension.")
    if candidate.exists() and not candidate.is_file():
        raise ValueError("Referenced map path is not a regular file.")
    return candidate


def load_validated_map(map_path: Path) -> list[MapPolyline]:
    """Parse and validate one map before exposing it to the session."""
    roads = load_xodr_reference_map(map_path)
    validate_imported_map(roads)
    return roads


def bundled_default_path(
    directory: Path,
    default_name: str,
    suffixes: set[str],
    resource_label: str,
) -> Path:
    """Resolve one named bundled default without allowing path traversal."""
    resolved_directory = directory.resolve()
    candidate = (resolved_directory / default_name).resolve()
    if (
        candidate.parent != resolved_directory
        or candidate.suffix.lower() not in suffixes
        or not candidate.is_file()
    ):
        raise HTTPException(status_code=404, detail=f"Default {resource_label} not found")
    return candidate


def bundled_scenario_map_path(map_path: Path) -> Path:
    """Confine a trusted default's associated map to packaged web resources."""
    resource_root = DEFAULT_SCENARIO_DIRECTORY.parent.resolve()
    candidate = map_path.resolve()
    try:
        candidate.relative_to(resource_root)
    except ValueError as exc:
        raise ValueError(
            "Bundled scenario maps must stay inside the web application resources."
        ) from exc
    if candidate.suffix.lower() not in {".xodr", ".xml"} or not candidate.is_file():
        raise ValueError("Bundled scenario map is not a valid XODR or XML file.")
    return candidate


def install_bundled_map(
    source_path: Path, roads: list[MapPolyline]
) -> Path:
    """Copy a validated packaged map into the active session and display it."""
    map_path = session_export_directory() / "maps" / source_path.name
    map_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source_path, map_path)
    except OSError:
        map_path.unlink(missing_ok=True)
        raise
    scenario.map.load_view_only(roads, map_path)
    scenario.map_load_hint = None
    return map_path


def bundled_default_entries(
    directory: Path, suffixes: set[str]
) -> list[dict[str, str]]:
    """List bundled defaults with stable filenames and readable labels."""
    label_overrides = {
        "Pass_straight_intersecting_vehicle_from_right_passing_straight": (
            "Pass straight intersecting vehicle from right passing straight"
        ),
        "RITA-junction": "RITA junction",
        "VRU_crossing_from_left": "VRU crossing from left",
        "cut_in_from_left": "Cut-in from left",
        "cut_in_from_left_on_curved_road": "Cut-in from left on curved road",
    }
    return [
        {
            "name": path.name,
            "label": (
                label_overrides.get(
                    path.stem,
                    path.stem.replace("_", " ").replace("-", " ").title(),
                )
                + f" ({path.suffix.lower()})"
            ),
        }
        for path in sorted(
            directory.iterdir(), key=lambda candidate: candidate.name.casefold()
        )
        if path.is_file() and path.suffix.lower() in suffixes
    ]


def available_postprocessing_scripts() -> dict[str, Path]:
    """Return approved postprocessing modules exposing the required run entry point."""
    scripts: dict[str, Path] = {}
    for path in POSTPROCESSING_SCRIPT_DIRECTORY.glob("*.py"):
        if path.name != "__init__.py":
            scripts[path.stem] = path
    return scripts


def load_postprocessing_script(name: str) -> object:
    """Load one approved postprocessing module by its selected name."""
    path = available_postprocessing_scripts().get(name)
    if path is None:
        raise ValueError(f"Unsupported postprocessing script: {name}")
    specification = importlib.util.spec_from_file_location(path.stem, path)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load postprocessing script: {name}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def run_postprocessing_scripts(
    names: list[str], parameters: dict[str, object], formats: set[str], export_directory: Path
) -> None:
    """Run selected approved scripts and fail when one reports an unsuccessful result."""
    for name in names:
        module = load_postprocessing_script(name)
        applicable_formats = set(getattr(module, "APPLICABLE_EXPORTS", ()))
        if not applicable_formats.intersection(formats):
            continue
        entry_point = getattr(module, "run", None)
        script_parameters = parameters.get(name, {})
        if not isinstance(script_parameters, dict):
            raise ValueError(f"Invalid parameters for postprocessing script: {name}")
        if not callable(entry_point) or entry_point(export_directory, script_parameters) is not True:
            raise ValueError(f"Postprocessing script failed: {name}")


@contextmanager
def remove_quality_checker_temporary_copy(input_path: Path):
    """Remove the checker's persistent processed XOSC copy after every run."""
    processed_path = Path.cwd() / "results" / "tmp" / f"processed_{input_path.name}"
    try:
        yield
    finally:
        processed_path.unlink(missing_ok=True)


@app.middleware("http")
async def bind_scenario_session(request: Request, call_next: Any) -> Any:
    """Bind each request to a cookie-scoped scenario and serialize its access."""
    cookie_session_id = request.cookies.get(SESSION_COOKIE_NAME)
    new_session = not valid_session_id(cookie_session_id)
    session_id = cookie_session_id
    if new_session:
        session_id = uuid4().hex
    assert session_id is not None
    session = scenario_store.get(session_id)
    async with session.lock:
        scenario_token = active_scenario.set(session.scenario)
        session_token = active_session_id.set(session_id)
        try:
            response = await call_next(request)
        finally:
            scenario_store.touch(session_id)
            active_session_id.reset(session_token)
            active_scenario.reset(scenario_token)
    if new_session:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            httponly=True,
            samesite="lax",
            secure=secure_cookie_for_request(request),
            path=session_cookie_path(),
        )
    return response


@app.middleware("http")
async def reject_oversized_upload_request(request: Request, call_next: Any) -> Any:
    """Reject known oversized multipart bodies before FastAPI parses them."""
    if request.url.path in {"/api/import", "/api/map"}:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                request_bytes = int(content_length)
            except ValueError:
                request_bytes = 0
            if request_bytes > MAX_UPLOAD_BYTES + UPLOAD_CHUNK_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Upload exceeds the {MAX_UPLOAD_BYTES} byte limit"},
                )
    return await call_next(request)


def apply_imported_scenario(
    vehicles: dict[str, list[Waypoint]],
    dimensions: dict[str, VehicleDimensions],
    detection_gaps: list[DetectionGap] | None = None,
) -> None:
    """Replace browser state with data returned by an existing importer."""
    validate_imported_scenario(vehicles, dimensions)
    scenario.vehicles = vehicles
    scenario.dimensions = dimensions
    scenario.detection_gaps = detection_gaps or []
    scenario.actor_counter = scenario.next_actor_counter()
    if not scenario.vehicles:
        scenario.add_actor()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIRECTORY / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Serve the PNG application icon for conventional favicon requests."""
    return FileResponse(BRANDING_DIRECTORY / "logo_icon.png", media_type="image/png")


@app.get("/api/scenario")
def get_scenario() -> dict[str, object]:
    return scenario.snapshot()


@app.delete("/api/session")
def delete_session_data(request: Request) -> JSONResponse:
    """Delete all application data associated with the active browser session."""
    session_id = active_session_id.get()
    if not valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="No valid session is active")
    assert session_id is not None
    scenario_store.delete(session_id)
    response = JSONResponse({"deleted": True})
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path=session_cookie_path(),
        secure=secure_cookie_for_request(request),
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/api/help")
def get_help() -> dict[str, str]:
    """Return the maintained web application help text for the browser dialog."""
    return {
        "text": HELP_PATH.read_text(encoding="utf-8"),
        "html": render_markdown(HELP_PATH),
    }


def render_markdown(path: Path) -> str:
    """Render trusted, packaged Markdown without allowing embedded HTML."""
    return MarkdownIt("commonmark", {"html": False}).enable("table").render(
        path.read_text(encoding="utf-8")
    ).replace(
        "<a href=", '<a target="_blank" rel="noopener noreferrer" href='
    )


def render_about() -> str:
    """Render the shared About content with safe external-link behavior."""
    return render_markdown(ABOUT_PATH)


@app.get("/api/about")
def get_about() -> dict[str, str]:
    """Return the shared information shown in the About dialog."""
    return {"html": render_about()}


@app.get("/docs/{document_name}")
def documentation_page(document_name: str):
    """Render one maintained documentation page for browser use."""
    if Path(document_name).name != document_name or not document_name.endswith(".md"):
        raise HTTPException(status_code=404, detail="Documentation page not found")
    document_path = DOCUMENTATION_DIRECTORY / document_name
    if not document_path.is_file():
        raise HTTPException(status_code=404, detail="Documentation page not found")
    document_source = document_path.read_text(encoding="utf-8")
    rendered = MarkdownIt("commonmark", {"html": False}).enable("table").render(
        document_source
    )
    first_heading = next(
        (line.removeprefix("# ").strip() for line in document_source.splitlines() if line.startswith("# ")),
        document_name.removesuffix(".md").replace("-", " ").title(),
    )
    navigation = ""
    navigation_at_end = ""
    if document_name != "README.md":
        tutorials = sorted(DOCUMENTATION_DIRECTORY.glob("[0-9][0-9]-*.md"))
        tutorial_names = [tutorial.name for tutorial in tutorials]
        navigation_links = ["<a href='/docs/README.md'>&larr; All examples</a>"]
        if document_name in tutorial_names:
            current_index = tutorial_names.index(document_name)
            if current_index:
                previous_name = tutorial_names[current_index - 1]
                navigation_links.append(
                    f"<a href='/docs/{previous_name}'>Previous tutorial</a>"
                )
            if current_index + 1 < len(tutorial_names):
                next_name = tutorial_names[current_index + 1]
                navigation_links.append(
                    f"<a href='/docs/{next_name}'>Next tutorial</a>"
                )
        navigation_content = " &middot; ".join(navigation_links)
        navigation = (
            "<nav aria-label='Tutorial navigation'>"
            + navigation_content
            + "</nav>"
        )
        navigation_at_end = (
            "<nav aria-label='Tutorial navigation at end'>"
            + navigation_content
            + "</nav>"
        )
    about_content = render_about()
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(first_heading)} – scenario.generator</title>"
        "<link rel='icon' href='/branding/logo_icon.png?v=transparent' type='image/png'>"
        "<link rel='stylesheet' href='/assets/style.css'>"
        "<style>.docs-header-spacer{flex:1}.docs-content{display:block;height:auto;"
        "max-width:960px;margin:40px auto;padding:0 24px;color:#13283f;"
        "font:16px/1.6 Helvetica,Arial,sans-serif}.docs-content h1,.docs-content h2{"
        "color:#012a7a;line-height:1.25}.docs-content h2{margin-top:2em}"
        ".docs-content img{display:block;max-width:100%;height:auto;margin:1.5em auto;"
        "border:1px solid #d9e5ee;border-radius:10px;box-shadow:0 4px 16px #1232}"
        ".docs-content a{color:#075a9b}.docs-content nav{margin-bottom:2em}"
        ".docs-content nav:last-child{margin-top:2em;margin-bottom:0}"
        ".docs-content table{width:100%;border-collapse:collapse;"
        "margin:1.5em 0}.docs-content th,.docs-content td{padding:.7em;text-align:left;"
        "border-bottom:1px solid #d9e5ee}.docs-content th{color:#012a7a;"
        "background:#f1f7fa}.docs-content blockquote{margin:1.5em 0;padding:.1em 1em;"
        "border-left:4px solid #86b9da;background:#f1f7fa}.docs-content code{"
        "background:#f1f5f7;"
        "padding:2px 4px;border-radius:3px}</style></head><body>"
        "<a class='skip-link' href='#documentation-main'>Skip to documentation</a>"
        "<header><a class='brand' href='/' aria-label='Back to scenario.generator'>"
        "<img src='/branding/logo.svg' alt='scenario.generator'></a>"
        "<span class='docs-header-spacer'></span>"
        "<button id='docs-about' class='header-legal-action'>About</button>"
        "<a class='header-legal-action' href='https://scenario.center/imprint/' "
        "target='_blank' rel='noopener noreferrer'>Imprint</a>"
        "<button id='docs-data-privacy' class='header-legal-action'>Data privacy</button>"
        "</header><main id='documentation-main' class='docs-content' tabindex='-1'>"
        f"{navigation}{rendered}{navigation_at_end}</main>"
        "<dialog id='docs-about-dialog' aria-label='About scenario.generator'><section class='parameter-dialog panel "
        "about-dialog-content'><div class='about-dialog-actions'>"
        "<button id='close-docs-about'>Close</button></div>"
        f"<div id='about-content'>{about_content}</div></section></dialog>"
        "<dialog id='docs-data-privacy-dialog' aria-labelledby='docs-data-privacy-dialog-title'>"
        "<section class='parameter-dialog panel data-privacy-dialog-content'>"
        "<div class='panel-title'><span id='docs-data-privacy-dialog-title'>Data privacy</span>"
        "<button id='close-docs-data-privacy'>Close</button></div>"
        "<a class='data-privacy-link' href='https://scenario.center/privacy-policy/' "
        "target='_blank' rel='noopener noreferrer'>scenario.center privacy policy</a>"
        "<div class='dialog-actions data-privacy-actions'>"
        "<button id='docs-delete-my-data' class='data-privacy-delete-action'>"
        "Delete my data now</button></div></section></dialog>"
        "<script>"
        "const aboutDialog=document.getElementById('docs-about-dialog');"
        "const privacyDialog=document.getElementById('docs-data-privacy-dialog');"
        "document.getElementById('docs-about').addEventListener('click',"
        "()=>aboutDialog.showModal());"
        "document.getElementById('close-docs-about').addEventListener('click',"
        "()=>aboutDialog.close());"
        "document.getElementById('docs-data-privacy').addEventListener('click',"
        "()=>privacyDialog.showModal());"
        "document.getElementById('close-docs-data-privacy').addEventListener('click',"
        "()=>privacyDialog.close());"
        "privacyDialog.addEventListener('click',(event)=>{"
        "if(event.target===privacyDialog)privacyDialog.close();});"
        "document.getElementById('docs-delete-my-data').addEventListener('click',async(event)=>{"
        "event.currentTarget.disabled=true;"
        "const response=await fetch('/api/session',{method:'DELETE'});"
        "if(response.ok){window.location.reload();return;}"
        "event.currentTarget.disabled=false;window.alert('Your data could not be deleted.');"
        "});</script></body></html>"
    )


@app.get("/docs/download/{template_name}")
def download_documentation_file(template_name: str) -> FileResponse:
    """Download a maintained tutorial file instead of displaying it."""
    if Path(template_name).name != template_name:
        raise HTTPException(status_code=404, detail="Documentation file not found")
    for directory_name in ("templates", "examples"):
        template_path = DOCUMENTATION_DIRECTORY / directory_name / template_name
        if template_path.is_file():
            return FileResponse(template_path, filename=template_name)
    raise HTTPException(status_code=404, detail="Documentation file not found")


@app.get("/api/postprocessing-scripts")
def list_postprocessing_scripts() -> dict[str, list[dict[str, object]]]:
    """List the approved scripts that may be selected for postprocessing."""
    scripts = []
    for name in sorted(available_postprocessing_scripts()):
        module = load_postprocessing_script(name)
        scripts.append({"name": name, "formats": sorted(getattr(module, "APPLICABLE_EXPORTS", ())), "parameters": getattr(module, "PARAMETERS", [])})
    return {"scripts": scripts}


@app.get("/api/environment-templates")
def list_environment_templates() -> dict[str, list[str]]:
    """List bundled environmental-template examples for the web UI."""
    return {
        "templates": sorted(
            template.name for template in ENVIRONMENT_TEMPLATE_DIRECTORY.glob("*.json")
        )
    }


@app.get("/api/environment-templates/{template_name}")
def download_environment_template(template_name: str) -> FileResponse:
    """Download one bundled environmental-template example."""
    template_directory = ENVIRONMENT_TEMPLATE_DIRECTORY.resolve()
    template_path = (template_directory / template_name).resolve()
    if (
        template_path.parent != template_directory
        or template_path.suffix != ".json"
        or not template_path.is_file()
    ):
        raise HTTPException(status_code=404, detail="Environmental template not found")
    return FileResponse(
        template_path,
        media_type="application/json",
        filename=template_path.name,
    )


@app.get("/api/default-scenarios")
def list_default_scenarios() -> dict[str, list[dict[str, str]]]:
    """List scenario files bundled for immediate exploration."""
    return {
        "defaults": bundled_default_entries(
            DEFAULT_SCENARIO_DIRECTORY, {".json", ".xosc"}
        )
    }


@app.post("/api/default-scenarios/{default_name}")
def load_default_scenario(default_name: str) -> dict[str, object]:
    """Replace the active scenario with one bundled JSON or XOSC example."""
    scenario_path = bundled_default_path(
        DEFAULT_SCENARIO_DIRECTORY,
        default_name,
        {".json", ".xosc"},
        "scenario",
    )
    try:
        map_source: Path | None = None
        map_roads: list[MapPolyline] | None = None
        if scenario_path.suffix.lower() == ".xosc":
            vehicles = load_openscenario_xosc(scenario_path)
            dimensions = {name: VehicleDimensions() for name in vehicles}
            gaps: list[DetectionGap] = []
            additional_information: dict[str, object] = {}
        else:
            vehicles, dimensions, configured_map_path, gaps = load_scenario_config(
                scenario_path
            )
            additional_information = (
                load_scenario_config_additional_information(scenario_path) or {}
            )
            if configured_map_path is not None:
                map_source = bundled_scenario_map_path(configured_map_path)
                map_roads = load_validated_map(map_source)
        apply_imported_scenario(vehicles, dimensions, gaps)
        if map_source is not None and map_roads is not None:
            install_bundled_map(map_source, map_roads)
        else:
            scenario.map.clear()
            scenario.settings["map_mode"] = False
    except IMPORT_DATA_ERRORS as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Bundled default scenario is invalid: {exc}",
        ) from exc
    scenario.additional_information = additional_information
    scenario.map_load_hint = None
    return scenario.snapshot()


@app.get("/api/default-maps")
def list_default_maps() -> dict[str, list[dict[str, str]]]:
    """List bundled OpenDRIVE maps available to the active deployment."""
    return {"defaults": bundled_default_entries(DEFAULT_MAP_DIRECTORY, {".xodr", ".xml"})}


@app.post("/api/default-maps/{default_name}")
def load_default_map(default_name: str) -> dict[str, object]:
    """Copy and load a bundled map inside the active session boundary."""
    source_path = bundled_default_path(
        DEFAULT_MAP_DIRECTORY,
        default_name,
        {".xodr", ".xml"},
        "map",
    )
    try:
        roads = load_validated_map(source_path)
        install_bundled_map(source_path, roads)
    except IMPORT_DATA_ERRORS as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Bundled default map is invalid: {exc}",
        ) from exc
    return scenario.snapshot()


@app.get("/api/metrics")
def get_metrics(time_s: float) -> dict[str, str]:
    """Return the current TTC and THW overlay labels using shared metric code."""
    trajectories = scenario.trajectory_payload()
    states = {}
    for name, trajectory in trajectories.items():
        try:
            states[name] = actor_state_from_trajectory(
                name,
                trajectory,
                time_s,
                scenario.dimensions.get(name, VehicleDimensions()),
            )
        except (KeyError, ValueError):
            continue
    labels: dict[str, str] = {}
    if scenario.settings["show_min_ttc"]:
        for name, target in min_ttc_targets_by_actor(states).items():
            labels[name] = "TTC --" if target is None else f"TTC {target[0]}: {target[1]:.2f} s"
    if scenario.settings["show_min_thw"]:
        for name, target in min_thw_targets_by_actor(states).items():
            value = "THW --" if target is None else f"THW {target[0]}: {target[1]:.2f} s"
            labels[name] = f"{labels[name]}\n{value}" if name in labels else value
    return labels


@app.post("/api/actors")
def create_actor(payload: dict[str, Any]) -> dict[str, object]:
    """Create an actor from an optional requested name and return the new state."""
    return {
        "name": scenario.add_actor(payload.get("name")),
        "scenario": scenario.snapshot(),
    }


@app.patch("/api/actors/{name}")
def update_actor(name: str, payload: dict[str, Any]) -> dict[str, object]:
    """Apply actor metadata and dimension changes received from the browser."""
    if name not in scenario.vehicles:
        raise HTTPException(status_code=404, detail="Actor not found")
    dimensions = scenario.dimensions[name]
    for key in ("length_m", "width_m", "height_m"):
        if key in payload:
            value = float(payload[key])
            if not math.isfinite(value):
                raise HTTPException(status_code=400, detail=f"{key} must be finite")
            setattr(dimensions, key, max(value, 0.001))
    if "actor_type" in payload:
        actor_type = str(payload["actor_type"])
        if actor_type not in {"vehicle", "cyclist", "pedestrian"}:
            raise HTTPException(status_code=400, detail="Unsupported actor type")
        if dimensions.actor_type != actor_type:
            defaults = actor_default_dimensions(actor_type)
            dimensions.actor_type = actor_type
            dimensions.length_m = defaults.length_m
            dimensions.width_m = defaults.width_m
            dimensions.height_m = defaults.height_m
            dimensions.carla_blueprint = ""
    for key in (
        "carla_blueprint",
        "xosc_export_mode",
        "parameter_declarations",
        "controller_name",
        "controller_xml",
    ):
        if key in payload:
            setattr(dimensions, key, str(payload[key]))
    if "waypoints" in payload:
        scenario.replace_waypoints(name, payload["waypoints"])
        snap_index = payload.get("snap_waypoint_index")
        snap_distance = max(float(payload.get("snap_distance_m", 0.0)), 0.0)
        if isinstance(snap_index, int) and snap_distance > 0.0:
            waypoints = scenario.vehicles[name]
            if 0 <= snap_index < len(waypoints):
                snap = scenario.map.nearest_compatible_lane(
                    waypoints[snap_index].x_m,
                    waypoints[snap_index].y_m,
                    dimensions.actor_type,
                    snap_distance,
                )
                if snap is not None:
                    waypoints[snap_index].x_m = snap.x_m
                    waypoints[snap_index].y_m = snap.y_m
        if payload.get("recalculate_times"):
            scenario.synchronize_times_from_speeds(name)
    return scenario.snapshot()


@app.post("/api/actors/{name}/waypoints")
def add_actor_waypoint(name: str, payload: dict[str, Any]) -> dict[str, object]:
    """Append one trajectory control point with desktop-equivalent timing."""
    if name not in scenario.vehicles:
        raise HTTPException(status_code=404, detail="Actor not found")
    try:
        scenario.add_waypoint(
            name,
            float(payload["x_m"]),
            float(payload["y_m"]),
            max(float(payload.get("snap_distance_m", 0.0)), 0.0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return scenario.snapshot()


@app.patch("/api/actors/{name}/segments/{segment_index}")
def update_actor_segment(
    name: str,
    segment_index: int,
    payload: dict[str, Any],
) -> dict[str, object]:
    """Update a segment speed without storing non-domain waypoint fields."""
    if name not in scenario.vehicles:
        raise HTTPException(status_code=404, detail="Actor not found")
    try:
        scenario.set_segment_speed(name, segment_index, float(payload["speed_mps"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return scenario.snapshot()


@app.patch("/api/actors/{name}/waypoints/{point_index}/speed")
def update_waypoint_speed(
    name: str,
    point_index: int,
    payload: dict[str, Any],
) -> dict[str, object]:
    """Update one velocity-profile node and recalculate affected timestamps."""
    if name not in scenario.vehicles:
        raise HTTPException(status_code=404, detail="Actor not found")
    try:
        scenario.set_waypoint_speed(name, point_index, float(payload["speed_mps"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return scenario.snapshot()


@app.patch("/api/actors/{name}/waypoints/{point_index}")
def update_actor_waypoint(
    name: str, point_index: int, payload: dict[str, Any]
) -> dict[str, object]:
    """Apply a desktop-equivalent table edit to one trajectory point."""
    if name not in scenario.vehicles:
        raise HTTPException(status_code=404, detail="Actor not found")
    try:
        if "time_s" in payload:
            scenario.set_waypoint_time(name, point_index, float(payload["time_s"]))
        elif "x_m" in payload or "y_m" in payload:
            point = scenario.vehicles[name][point_index]
            x_m = float(payload.get("x_m", point.x_m))
            y_m = float(payload.get("y_m", point.y_m))
            snap_distance = max(float(payload.get("snap_distance_m", 0.0)), 0.0)
            if snap_distance > 0.0:
                snap = scenario.map.nearest_compatible_lane(
                    x_m,
                    y_m,
                    scenario.dimensions[name].actor_type,
                    snap_distance,
                )
                if snap is not None:
                    x_m, y_m = snap.x_m, snap.y_m
            scenario.set_waypoint_geometry(
                name,
                point_index,
                x_m,
                y_m,
            )
        else:
            raise ValueError("Provide time_s, x_m, or y_m.")
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return scenario.snapshot()


@app.post("/api/actors/{name}/waypoints/{point_index}/insert")
def insert_actor_waypoint(name: str, point_index: int) -> dict[str, object]:
    """Insert a trajectory point using the desktop table defaults."""
    if name not in scenario.vehicles:
        raise HTTPException(status_code=404, detail="Actor not found")
    try:
        scenario.insert_waypoint(name, point_index)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return scenario.snapshot()


@app.delete("/api/actors/{name}/waypoints/{point_index}")
def delete_actor_waypoint(name: str, point_index: int) -> dict[str, object]:
    """Remove a trajectory point using the desktop table semantics."""
    if name not in scenario.vehicles:
        raise HTTPException(status_code=404, detail="Actor not found")
    try:
        scenario.delete_waypoint(name, point_index)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return scenario.snapshot()


@app.patch("/api/settings")
def update_settings(payload: dict[str, Any]) -> dict[str, object]:
    """Update browser editor settings persisted with the active session."""
    for key, value in payload.items():
        if key not in scenario.settings:
            raise HTTPException(status_code=400, detail=f"Unknown setting: {key}")
        if key == "time_step_s":
            scenario.settings[key] = max(float(value), 0.001)
        elif key in {"waypoint_timing_mode", "trajectory_calculation_mode"}:
            scenario.settings[key] = str(value)
        else:
            scenario.settings[key] = bool(value)
    if scenario.settings["map_mode"]:
        scenario.settings["show_speed_profile"] = False
    return scenario.snapshot()


@app.post("/api/detection-gaps")
def add_detection_gap(payload: dict[str, Any]) -> dict[str, object]:
    """Add a perception gap for an actor."""
    try:
        gap = DetectionGap(
            vehicle_name=str(payload["vehicle_name"]),
            start_time_s=float(payload["start_time_s"]),
            end_time_s=float(payload["end_time_s"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if gap.vehicle_name not in scenario.vehicles:
        raise HTTPException(status_code=400, detail="Perception gap actor does not exist")
    if gap.end_time_s < gap.start_time_s:
        raise HTTPException(status_code=400, detail="Gap end must not precede its start")
    scenario.detection_gaps.append(gap)
    return scenario.snapshot()


@app.patch("/api/detection-gaps/{index}")
def update_detection_gap(index: int, payload: dict[str, Any]) -> dict[str, object]:
    """Edit one perception gap."""
    if not 0 <= index < len(scenario.detection_gaps):
        raise HTTPException(status_code=404, detail="Perception gap not found")
    current = scenario.detection_gaps[index]
    updated = DetectionGap(
        vehicle_name=str(payload.get("vehicle_name", current.vehicle_name)),
        start_time_s=float(payload.get("start_time_s", current.start_time_s)),
        end_time_s=float(payload.get("end_time_s", current.end_time_s)),
    )
    if updated.vehicle_name not in scenario.vehicles or updated.end_time_s < updated.start_time_s:
        raise HTTPException(status_code=400, detail="Invalid perception gap")
    scenario.detection_gaps[index] = updated
    return scenario.snapshot()


@app.delete("/api/detection-gaps/{index}")
def delete_detection_gap(index: int) -> dict[str, object]:
    """Delete one perception gap."""
    if not 0 <= index < len(scenario.detection_gaps):
        raise HTTPException(status_code=404, detail="Perception gap not found")
    del scenario.detection_gaps[index]
    return scenario.snapshot()


@app.delete("/api/actors/{name}")
def delete_actor(name: str) -> dict[str, object]:
    """Delete one actor while retaining at least one actor in the scenario."""
    if len(scenario.vehicles) <= 1:
        raise HTTPException(status_code=400, detail="At least one actor is required")
    scenario.vehicles.pop(name, None)
    scenario.dimensions.pop(name, None)
    scenario.detection_gaps = [
        gap for gap in scenario.detection_gaps if gap.vehicle_name != name
    ]
    return scenario.snapshot()


@app.post("/api/actors/{name}/rename")
def rename_actor(name: str, payload: dict[str, Any]) -> dict[str, object]:
    """Rename an actor and all references owned by the scenario state."""
    if name not in scenario.vehicles:
        raise HTTPException(status_code=404, detail="Actor not found")
    try:
        new_name = safe_vehicle_name(str(payload.get("name", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if new_name in scenario.vehicles and new_name != name:
        raise HTTPException(status_code=400, detail="Actor name already exists")
    if new_name == name:
        return {"name": name, "scenario": scenario.snapshot()}
    scenario.vehicles[new_name] = scenario.vehicles.pop(name)
    scenario.dimensions[new_name] = scenario.dimensions.pop(name)
    for gap in scenario.detection_gaps:
        if gap.vehicle_name == name:
            gap.vehicle_name = new_name
    return {"name": new_name, "scenario": scenario.snapshot()}


@app.post("/api/map")
async def upload_map(file: UploadFile = File(...)) -> dict[str, object]:
    """Store and load an uploaded OpenDRIVE map into the active scenario."""
    if Path(file.filename or "").suffix.lower() not in {".xodr", ".xml"}:
        raise HTTPException(status_code=400, detail="Upload an OpenDRIVE .xodr file")
    upload_path = (
        session_export_directory()
        / "maps"
        / safe_upload_name(file.filename, "map.xodr")
    )
    await store_upload(file, upload_path, MAX_STRUCTURED_UPLOAD_BYTES)
    try:
        roads = load_validated_map(upload_path)
    except IMPORT_DATA_ERRORS as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid OpenDRIVE file: {exc}") from exc
    scenario.map.load_view_only(roads, upload_path)
    scenario.map_load_hint = None
    return scenario.snapshot()


@app.post("/api/map/editing")
def enable_map_editing() -> dict[str, object]:
    """Enable edits on a detached copy of the loaded OpenDRIVE roads."""
    if not scenario.map.enable_editing():
        raise HTTPException(status_code=400, detail="Load an OpenDRIVE map before editing")
    scenario.settings["map_mode"] = True
    scenario.settings["show_speed_profile"] = False
    return scenario.snapshot()


@app.post("/api/map/blank")
def create_blank_map(payload: dict[str, Any]) -> dict[str, object]:
    """Create an editable map containing one empty road."""
    width_m = max(float(payload.get("width_m", 6.0)), 0.1)
    scenario.map.load_editable(
        [MapPolyline(name="road_1", points=[], width_m=width_m)],
        None,
    )
    scenario.settings["map_mode"] = True
    scenario.settings["show_speed_profile"] = False
    return scenario.snapshot()


@app.delete("/api/map")
def clear_map() -> dict[str, object]:
    """Remove the active map and its editable road state."""
    scenario.map.clear()
    scenario.settings["map_mode"] = False
    return scenario.snapshot()


@app.post("/api/map/roads/{index}/connections/clear")
def clear_map_road_connections(index: int) -> dict[str, object]:
    """Clear predecessor and successor links of an editable road."""
    road = editable_road(index)
    old_predecessor, old_successor = road.predecessor_road, road.successor_road
    road.predecessor_road = ""
    road.successor_road = ""
    road.predecessor_lane_links = ""
    road.successor_lane_links = ""
    for other in scenario.map.roads:
        if other.kind != "reference":
            continue
        if old_predecessor and other.name == old_predecessor and other.successor_road == road.name:
            other.successor_road = ""
            other.successor_lane_links = ""
        if old_successor and other.name == old_successor and other.predecessor_road == road.name:
            other.predecessor_road = ""
            other.predecessor_lane_links = ""
    scenario.map.modified = True
    return scenario.snapshot()


@app.post("/api/map/connections")
def connect_map_roads(payload: dict[str, Any]) -> dict[str, object]:
    """Connect a source lane to a target lane without moving road geometry."""
    try:
        source = editable_road(int(payload["source_index"]))
        target = editable_road(int(payload["target_index"]))
        source_lane_id = int(payload["source_lane_id"])
        target_lane_id = int(payload["target_lane_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid road connection") from exc
    if source is target:
        raise HTTPException(status_code=400, detail="A road cannot connect to itself")
    if source_lane_id not in source.opendrive_lane_ids():
        raise HTTPException(
            status_code=400,
            detail=f"Lane {source_lane_id} does not exist on {source.name}",
        )
    if target_lane_id not in target.opendrive_lane_ids():
        raise HTTPException(
            status_code=400,
            detail=f"Lane {target_lane_id} does not exist on {target.name}",
        )
    for road in scenario.map.roads:
        if road.kind != "reference":
            continue
        if road.name == source.successor_road and road.predecessor_road == source.name:
            road.predecessor_road = ""
            road.predecessor_lane_links = ""
        if road.name == target.predecessor_road and road.successor_road == target.name:
            road.successor_road = ""
            road.successor_lane_links = ""
    source.successor_road = target.name
    target.predecessor_road = source.name
    source.successor_lane_links = f"{source_lane_id}->{target_lane_id}"
    target.predecessor_lane_links = f"{target_lane_id}->{source_lane_id}"
    scenario.map.modified = True
    return scenario.snapshot()


def editable_road(index: int) -> MapPolyline:
    """Return an editable road or turn a client error into an HTTP response."""
    if not scenario.map.edit_enabled:
        raise HTTPException(status_code=400, detail="Enable map editing first")
    if not 0 <= index < len(scenario.map.roads):
        raise HTTPException(status_code=404, detail="Road not found")
    return scenario.map.roads[index]


@app.post("/api/map/roads")
def add_map_road(payload: dict[str, Any]) -> dict[str, object]:
    """Add one editable road, initializing a blank map on first use."""
    if not scenario.map.edit_enabled:
        if scenario.map.view_roads:
            raise HTTPException(status_code=400, detail="Enable map editing first")
        scenario.map.load_editable([], None)
    index = len(scenario.map.roads) + 1
    scenario.map.roads.append(
        MapPolyline(
            name=safe_vehicle_name(str(payload.get("name", f"road_{index}"))),
            points=[],
            width_m=max(float(payload.get("width_m", 6.0)), 0.1),
        ),
    )
    scenario.map.modified = True
    return scenario.snapshot()


def apply_map_road_update(index: int, payload: dict[str, Any]) -> dict[str, object]:
    """Update editable road geometry, lane metadata, or connections."""
    road = editable_road(index)
    previous_points = list(road.points)
    if "points" in payload:
        try:
            road.points = [(float(point[0]), float(point[1])) for point in payload["points"]]
        except (IndexError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid road points") from exc
    for key in (
        "name",
        "kind",
    ):
        if key in payload:
            setattr(road, key, str(payload[key]))
    if "lane_width_m" in payload:
        road.lane_width_m = max(float(payload["lane_width_m"]), 0.1)
        road.lane_widths_m = {}
        road.width_m = road.normalized_lane_count() * road.lane_width_m
    if "width_m" in payload:
        road.width_m = max(float(payload["width_m"]), 0.1)
        road.lane_widths_m = {}
        road.lane_width_m = road.width_m / road.normalized_lane_count()
    if "lane_count" in payload:
        road.lane_count = max(int(payload["lane_count"]), 1)
        valid_ids = set(road.opendrive_lane_ids())
        road.lane_widths_m = {
            lane_id: width
            for lane_id, width in road.lane_widths_m.items()
            if lane_id in valid_ids
        }
        road.lane_types = {
            lane_id: lane_type
            for lane_id, lane_type in road.lane_types.items()
            if lane_id in valid_ids
        }
        road.width_m = road.total_width_m()
    if "lane_width_spec" in payload:
        road.set_lane_width_spec(str(payload["lane_width_spec"]))
    if "lane_type_spec" in payload:
        road.set_lane_type_spec(str(payload["lane_type_spec"]))
    predecessor_name = str(payload.get("predecessor_road", road.predecessor_road))
    successor_name = str(payload.get("successor_road", road.successor_road))
    predecessor_links = str(
        payload.get("predecessor_lane_links", road.predecessor_lane_links)
    )
    successor_links = str(payload.get("successor_lane_links", road.successor_lane_links))
    if (
        "predecessor_road" in payload
        and predecessor_name != road.predecessor_road
        and predecessor_links == road.predecessor_lane_links
    ):
        predecessor_links = ""
    if (
        "successor_road" in payload
        and successor_name != road.successor_road
        and successor_links == road.successor_lane_links
    ):
        successor_links = ""
    scenario.validate_lane_links(road, predecessor_name, predecessor_links)
    scenario.validate_lane_links(road, successor_name, successor_links)
    if "predecessor_road" in payload:
        scenario.set_road_predecessor(road, predecessor_name)
    if "successor_road" in payload:
        scenario.set_road_successor(road, successor_name)
    if "predecessor_lane_links" in payload:
        road.predecessor_lane_links = predecessor_links
    if "successor_lane_links" in payload:
        road.successor_lane_links = successor_links
    if "points" in payload:
        scenario.update_imported_road_geometry(road, previous_points)
        for point_index, (previous, current) in enumerate(
            zip(previous_points, road.points)
        ):
            if previous != current:
                scenario.sync_moved_road_endpoint(road, point_index)
    if {"lane_count", "lane_width_m", "lane_width_spec", "lane_type_spec"} & payload.keys():
        scenario.update_imported_road_lanes(road)
    scenario.map.modified = True
    scenario.map.invalidate_elevation_index()
    return scenario.snapshot()


@app.patch("/api/map/roads/{index}")
def update_map_road(index: int, payload: dict[str, Any]) -> dict[str, object]:
    """Apply a road edit atomically and report invalid editor values to the UI."""
    roads_before = deepcopy(scenario.map.roads)
    modified_before = scenario.map.modified
    try:
        return apply_map_road_update(index, payload)
    except HTTPException:
        raise
    except (IndexError, TypeError, ValueError) as exc:
        scenario.map.roads = roads_before
        scenario.map.modified = modified_before
        scenario.map.invalidate_elevation_index()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/map/roads/{index}/points/{point_index}/insert")
def insert_map_road_point(index: int, point_index: int) -> dict[str, object]:
    """Insert one road-table point using the desktop editor's neighbor rule."""
    road = editable_road(index)
    previous_points = list(road.points)
    point_index = min(max(point_index, 0), len(road.points))
    if not road.points:
        point = (0.0, 0.0)
    elif point_index <= 0:
        point = road.points[0]
    elif point_index >= len(road.points):
        point = road.points[-1]
    else:
        previous, following = road.points[point_index - 1], road.points[point_index]
        point = ((previous[0] + following[0]) / 2.0, (previous[1] + following[1]) / 2.0)
    road.points.insert(point_index, point)
    scenario.update_imported_road_geometry(road, previous_points)
    scenario.map.modified = True
    scenario.map.invalidate_elevation_index()
    return scenario.snapshot()


@app.delete("/api/map/roads/{index}/points/{point_index}")
def delete_map_road_point(index: int, point_index: int) -> dict[str, object]:
    """Delete one road-table point and keep imported lane profiles aligned."""
    road = editable_road(index)
    if not 0 <= point_index < len(road.points):
        raise HTTPException(status_code=404, detail="Road point not found")
    previous_points = list(road.points)
    del road.points[point_index]
    scenario.update_imported_road_geometry(road, previous_points)
    scenario.map.modified = True
    scenario.map.invalidate_elevation_index()
    return scenario.snapshot()


@app.delete("/api/map/roads/{index}")
def delete_map_road(index: int) -> dict[str, object]:
    """Delete one editable road."""
    editable_road(index)
    del scenario.map.roads[index]
    scenario.map.modified = True
    scenario.map.invalidate_elevation_index()
    return scenario.snapshot()


@app.post("/api/map/export")
def export_map(payload: dict[str, Any]) -> FileResponse:
    """Download the current editable map as OpenDRIVE."""
    if not scenario.map.edit_enabled:
        raise HTTPException(status_code=400, detail="Enable map editing first")
    export_directory = session_export_directory()
    export_directory.mkdir(parents=True, exist_ok=True)
    output_path = export_directory / f"{safe_output_name(str(payload.get('base_name', 'map')))}.xodr"
    try:
        write_xodr_map(output_path, scenario.map.roads)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(output_path, filename=output_path.name)


def write_current_map_export(output_path: Path) -> None:
    """Export the active map, preserving an unchanged source file when possible."""
    source_path = scenario.map.path
    if (
        source_path is not None
        and source_path.is_file()
        and not scenario.map.modified
    ):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, output_path)
        return
    roads = scenario.map.roads if scenario.map.edit_enabled else scenario.map.view_roads
    if not roads:
        raise ValueError("An XODR export requires a loaded or created map.")
    write_xodr_map(output_path, roads)


@app.post("/api/import")
async def import_config(file: UploadFile = File(...)) -> dict[str, object]:
    """Import a supported scenario file and replace the active browser state."""
    filename = safe_upload_name(file.filename, "scenario_config.json")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".json", ".mcap", ".xml", ".xodr", ".xosc"}:
        raise HTTPException(status_code=400, detail=f"Unsupported import file type: {suffix}")
    upload_path = session_export_directory() / "imports" / filename
    upload_limit = MAX_UPLOAD_BYTES if suffix == ".mcap" else MAX_STRUCTURED_UPLOAD_BYTES
    await store_upload(file, upload_path, upload_limit)
    try:
        imported_vehicles: dict[str, list[Waypoint]] | None = None
        imported_dimensions: dict[str, VehicleDimensions] = {}
        imported_gaps: list[DetectionGap] | None = None
        imported_roads: list[MapPolyline] | None = None
        imported_map_path: Path | None = None
        additional_information: dict[str, object] | None = None
        update_additional_information = False
        clear_map = False
        map_load_hint: str | None = None

        if suffix == ".mcap":
            (
                imported_vehicles,
                imported_dimensions,
                imported_roads,
                imported_map_path,
                imported_gaps,
            ) = OmegaPrimeAdapter().import_file(upload_path)
            validate_imported_map(imported_roads)
        elif suffix == ".xosc":
            imported_vehicles = load_openscenario_xosc(upload_path)
            imported_dimensions = {
                name: VehicleDimensions() for name in imported_vehicles
            }
            imported_map_path = load_openscenario_map_path(upload_path)
            if imported_map_path:
                imported_map_path = confined_session_map_path(imported_map_path)
                if imported_map_path.exists():
                    imported_roads = load_validated_map(imported_map_path)
        elif suffix == ".xodr":
            imported_roads = load_validated_map(upload_path)
            imported_map_path = upload_path
        elif suffix == ".xml":
            try:
                imported_vehicles = load_openscenario_xosc(upload_path)
            except IMPORT_DATA_ERRORS:
                imported_roads = load_validated_map(upload_path)
                imported_map_path = upload_path
            else:
                imported_dimensions = {
                    name: VehicleDimensions() for name in imported_vehicles
                }
                imported_map_path = load_openscenario_map_path(upload_path)
                if imported_map_path:
                    imported_map_path = confined_session_map_path(imported_map_path)
                    if imported_map_path.exists():
                        imported_roads = load_validated_map(imported_map_path)
        elif suffix == ".json":
            try:
                (
                    imported_vehicles,
                    imported_dimensions,
                    imported_map_path,
                    imported_gaps,
                ) = load_scenario_config(upload_path)
                additional_information = (
                    load_scenario_config_additional_information(upload_path) or {}
                )
                update_additional_information = True
                clear_map = True
                if imported_map_path:
                    imported_map_path = confined_session_map_path(imported_map_path)
                if imported_map_path and imported_map_path.exists():
                    imported_roads = load_validated_map(imported_map_path)
                elif imported_map_path:
                    uploaded_map_path = (
                        session_export_directory()
                        / "maps"
                        / imported_map_path.name
                    )
                    if uploaded_map_path.is_file():
                        imported_map_path = uploaded_map_path
                        imported_roads = load_validated_map(imported_map_path)
                    else:
                        map_load_hint = imported_map_path.name
            except ValueError:
                try:
                    imported_vehicles, imported_dimensions = (
                        SimpleScenarioAdapter().import_file(upload_path)
                    )
                except ValueError:
                    imported_vehicles = load_trajectory_json(upload_path)
                    imported_dimensions = load_trajectory_json_dimensions(upload_path)

        if imported_vehicles is not None:
            apply_imported_scenario(
                imported_vehicles, imported_dimensions, imported_gaps
            )
        if update_additional_information:
            scenario.additional_information = additional_information or {}
        if imported_roads is not None:
            scenario.map.load_view_only(imported_roads, imported_map_path)
            scenario.map_load_hint = None
        elif clear_map:
            scenario.map.clear()
            scenario.settings["map_mode"] = False
            scenario.map_load_hint = map_load_hint
        elif suffix == ".json":
            scenario.map_load_hint = None
    except IMPORT_DATA_ERRORS as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return scenario.snapshot()


@app.post("/api/export/{format_name}")
def export_scenario(format_name: str, payload: dict[str, Any]) -> FileResponse:
    """Generate and download the requested scenario export format."""
    export_directory = session_export_directory()
    export_directory.mkdir(parents=True, exist_ok=True)
    base_name = safe_output_name(str(payload.get("base_name", "scenario")))
    trajectories = scenario.trajectory_payload()
    additional_information = payload.get(
        "additional_information", scenario.additional_information
    )
    if not isinstance(additional_information, dict):
        additional_information = {}
    scenario.additional_information = additional_information
    try:
        if format_name == "xosc":
            output_path = export_directory / f"{base_name}.xosc"
            write_openscenario(
                trajectories,
                output_path,
                road_logic_file=scenario.road_logic_file(output_path),
                additional_scenario_information=additional_information,
            )
        elif format_name == "json":
            output_path = export_directory / f"{base_name}.json"
            write_trajectory_json(trajectories, output_path)
        elif format_name == "config":
            output_path = export_directory / f"{base_name}_config.json"
            write_scenario_config(
                output_path,
                scenario.vehicles,
                scenario.dimensions,
                scenario.map.path,
                scenario.detection_gaps,
                additional_information,
            )
        elif format_name == "mcap":
            output_path = export_directory / f"{base_name}.mcap"
            map_path = scenario.map.path
            if scenario.map.edit_enabled and scenario.map.modified:
                map_path = export_directory / f"{base_name}.xodr"
                write_xodr_map(map_path, scenario.map.roads)
            exporter = exporter_registry.exporter_for(output_path)
            if exporter is None:
                raise ValueError("Omega-Prime exporter is unavailable")
            exporter.export_file(
                output_path,
                trajectories,
                map_polylines=scenario.map.roads,
                map_path=map_path,
                detection_gaps=scenario.detection_gaps,
            )
        else:
            raise HTTPException(status_code=404, detail="Unsupported export format")
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(output_path, filename=output_path.name)


@app.post("/api/export-bundle")
def export_scenario_bundle(payload: dict[str, Any]) -> FileResponse:
    """Generate all selected exports once and return them as one ZIP bundle."""
    requested_formats = payload.get("formats", [])
    if not isinstance(requested_formats, list):
        raise HTTPException(status_code=400, detail="Export formats must be a list.")
    formats = {str(format_name).lower() for format_name in requested_formats}
    supported_formats = {"json", "xosc", "mcap", "xodr"}
    unknown_formats = formats - supported_formats
    if unknown_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export format: {sorted(unknown_formats)[0]}",
        )
    if not formats:
        raise HTTPException(status_code=400, detail="Select at least one export format.")

    base_name = safe_output_name(str(payload.get("base_name", "scenario")))
    additional_information = payload.get(
        "additional_information", scenario.additional_information
    )
    if not isinstance(additional_information, dict):
        additional_information = {}
    scenario.additional_information = additional_information
    export_directory = session_export_directory()
    export_directory.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="scenario.generator-", dir=export_directory) as directory:
            output_directory = Path(directory)
            trajectories = scenario.trajectory_payload()
            written_paths: list[Path] = []
            map_requires_export = scenario.map.has_any_roads()
            write_xodr = "xodr" in formats or map_requires_export
            map_path = scenario.map.path
            if write_xodr:
                map_path = output_directory / f"{base_name}.xodr"
                write_current_map_export(map_path)
                written_paths.append(map_path)

            exported_additional_information = deepcopy(additional_information)
            if map_path is not None and write_xodr:
                exported_additional_information["xosc_map_path"] = map_path.name

            if "json" in formats:
                json_path = output_directory / f"{base_name}.json"
                write_trajectory_json(trajectories, json_path)
                written_paths.append(json_path)
            if "xosc" in formats:
                xosc_path = output_directory / f"{base_name}.xosc"
                configured_map_path = str(
                    exported_additional_information.get("xosc_map_path", "")
                ).strip()
                road_logic_file = (
                    configured_map_path
                    or (map_path.name if map_path is not None and write_xodr else None)
                    or scenario.road_logic_file(xosc_path)
                )
                write_openscenario(
                    trajectories,
                    xosc_path,
                    road_logic_file=road_logic_file,
                    additional_scenario_information=exported_additional_information,
                )
                written_paths.append(xosc_path)
            if "mcap" in formats:
                mcap_path = output_directory / f"{base_name}.mcap"
                exporter = exporter_registry.exporter_for(mcap_path)
                if exporter is None:
                    raise ValueError("Omega-Prime exporter is unavailable.")
                exporter.export_file(
                    mcap_path,
                    trajectories,
                    map_polylines=scenario.map.roads,
                    map_path=map_path,
                    detection_gaps=scenario.detection_gaps,
                )
                written_paths.append(mcap_path)

            config_path = output_directory / f"{base_name}_config.json"
            write_scenario_config(
                config_path,
                scenario.vehicles,
                scenario.dimensions,
                map_path,
                scenario.detection_gaps,
                exported_additional_information,
            )
            written_paths.append(config_path)

            selected_scripts = exported_additional_information.get(
                "postprocessing_scripts", []
            )
            if not isinstance(selected_scripts, list) or not all(
                isinstance(name, str) for name in selected_scripts
            ):
                raise ValueError("Postprocessing scripts must be a list of names.")
            script_parameters = exported_additional_information.get(
                "postprocessing_parameters", {}
            )
            if not isinstance(script_parameters, dict):
                raise ValueError("Postprocessing parameters must be an object.")
            run_postprocessing_scripts(
                selected_scripts, script_parameters, formats, output_directory
            )

            bundle_path = export_directory / f"{base_name}_exports.zip"
            with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
                for path in sorted(output_directory.iterdir()):
                    if not path.is_file():
                        continue
                    bundle.write(path, path.name)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(bundle_path, filename=bundle_path.name)


@app.post("/api/quality-check")
def quality_check(payload: dict[str, Any]) -> dict[str, object]:
    """Export an XOSC temporarily and return Scenario Quality Checker findings."""
    try:
        from quality_checker.quality_checker import DEFAULT_SCHEMA_PATH, quality_check_single
    except Exception as exc:  # noqa: BLE001 - optional third-party validation.
        raise HTTPException(status_code=503, detail="Scenario Quality Checker is unavailable") from exc
    export_directory = session_export_directory()
    export_directory.mkdir(parents=True, exist_ok=True)
    output_path = export_directory / f"{safe_output_name(str(payload.get('base_name', 'scenario')))}.xosc"
    additional_information = payload.get("additional_information", scenario.additional_information)
    if not isinstance(additional_information, dict):
        additional_information = {}
    try:
        write_openscenario(
            scenario.trajectory_payload(),
            output_path,
            road_logic_file=scenario.road_logic_file(output_path),
            additional_scenario_information=additional_information,
        )
        with remove_quality_checker_temporary_copy(output_path):
            result = quality_check_single(
                file_path=output_path,
                out_path=output_path.parent,
                schema_path=DEFAULT_SCHEMA_PATH,
                esmini_path=None,
                out_pdf=False,
                out_csv=False,
                print_log=False,
            )
    except Exception as exc:  # noqa: BLE001 - checker errors must reach the UI.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    warnings, problems = quality_checker_issue_lists(result)
    return {"warnings": warnings, "problems": problems, "xosc_path": output_path.name}


@app.post("/api/quality-check/pdf")
def quality_check_pdf(payload: dict[str, Any]) -> FileResponse:
    """Create and download the Scenario Quality Checker PDF report."""
    try:
        from quality_checker.quality_checker import DEFAULT_SCHEMA_PATH, quality_check_single
    except Exception as exc:  # noqa: BLE001 - optional third-party validation.
        raise HTTPException(status_code=503, detail="Scenario Quality Checker is unavailable") from exc
    export_directory = session_export_directory()
    export_directory.mkdir(parents=True, exist_ok=True)
    base_name = safe_output_name(str(payload.get("base_name", "scenario")))
    output_path = export_directory / f"{base_name}.xosc"
    report_directory = export_directory / "reports"
    report_directory.mkdir(parents=True, exist_ok=True)
    additional_information = payload.get("additional_information", scenario.additional_information)
    if not isinstance(additional_information, dict):
        additional_information = {}
    try:
        write_openscenario(
            scenario.trajectory_payload(), output_path,
            road_logic_file=scenario.road_logic_file(output_path),
            additional_scenario_information=additional_information,
        )
        with remove_quality_checker_temporary_copy(output_path):
            quality_check_single(
                file_path=output_path, out_path=report_directory,
                schema_path=DEFAULT_SCHEMA_PATH, esmini_path=None,
                out_pdf=True, out_csv=False, print_log=False,
            )
        pdf_path = report_directory / f"{base_name}.pdf"
        if not pdf_path.exists():
            pdf_paths = sorted(report_directory.glob("*.pdf"), key=lambda path: path.stat().st_mtime)
            if not pdf_paths:
                raise ValueError("Scenario Quality Checker did not create a PDF report.")
            pdf_path = pdf_paths[-1]
    except Exception as exc:  # noqa: BLE001 - checker errors reach the browser.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(pdf_path, filename=pdf_path.name)


def main():
    """Run the scenario.generator web application."""
    import uvicorn

    uvicorn.run("scenario_generator.webapp.server:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
