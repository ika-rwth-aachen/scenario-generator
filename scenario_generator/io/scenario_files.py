from __future__ import annotations

import itertools
import json
import math
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml.ElementTree import parse as safe_xml_parse

from scenario_generator.config.settings import load_map_export_max_lateral_deviation_m
from scenario_generator.io.import_validation import (
    MAX_IMPORTED_ACTORS,
    MAX_IMPORTED_ROADS,
    MAX_POINTS_PER_ROAD,
    MAX_TOTAL_MAP_POINTS,
    MAX_TOTAL_WAYPOINTS,
    MAX_WAYPOINTS_PER_ACTOR,
    finite_number,
    validate_imported_map,
)
from scenario_generator.map.map import CubicRoadProfile, LaneCrossSection, MapPolyline
from scenario_generator.scenario_elements.road_user.detection_gap import DetectionGap
from scenario_generator.scenario_elements.road_user.road_user import (
    VehicleDimensions,
    safe_vehicle_name,
)
from scenario_generator.scenario_elements.road_user.trajectory import Trajectory, Waypoint


def parse_float(value: str, field_name: str) -> float:
    try:
        return finite_number(value.strip(), field_name)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}") from exc


def waypoints_to_text(waypoints: list[Waypoint]) -> str:
	lines = ["time_s,x_m,y_m,speed_mps"]
	for waypoint in sorted(waypoints, key=lambda item: item.time_s):
		speed_text = "" if waypoint.speed_mps is None else f"{waypoint.speed_mps:.6g}"
		lines.append(
			f"{waypoint.time_s:.6g},{waypoint.x_m:.6g},{waypoint.y_m:.6g},{speed_text}"
		)
	return "\n".join(lines)


def parse_waypoint_rows(text: str) -> list[Waypoint]:
	waypoints: list[Waypoint] = []
	for line_number, raw_line in enumerate(text.splitlines(), start=1):
		line = raw_line.strip()
		if not line or line.startswith("#"):
			continue
		if line.lower().replace(" ", "") in {
			"time_s,x_m,y_m",
			"time,x,y",
			"time_s,x_m,y_m,speed_mps",
			"time,x,y,speed",
			"time_s,x_m,y_m,speed_mps,yaw_rad",
			"time,x,y,speed,yaw",
		}:
			continue
		parts = [part.strip() for part in line.split(",")]
		if len(parts) not in (3, 4, 5):
			raise ValueError(f"Line {line_number}: expected time_s, x_m, y_m.")
		speed_mps = (
			parse_float(parts[3], "speed_mps")
			if len(parts) >= 4 and parts[3]
			else None
		)
		waypoints.append(
			Waypoint(
				time_s=parse_float(parts[0], "time_s"),
				x_m=parse_float(parts[1], "x_m"),
				y_m=parse_float(parts[2], "y_m"),
				speed_mps=speed_mps,
			)
		)
	return sorted(waypoints, key=lambda waypoint: waypoint.time_s)


def write_trajectory_json(
    trajectories: dict[str, dict[str, object]],
    output_path: Path,
):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(trajectories, file, indent=2)
        file.write("")


def waypoints_from_trajectory(trajectory: dict[str, list[float]]) -> list[Waypoint]:
	for series_name in ("time_s", "x_m", "y_m"):
		if series_name not in trajectory:
			raise ValueError(f"Trajectory is missing {series_name}.")
	times = trajectory["time_s"]
	xs = trajectory["x_m"]
	ys = trajectory["y_m"]
	if not (len(times) == len(xs) == len(ys)):
		raise ValueError("Trajectory time_s, x_m, and y_m series must have the same length.")
	if len(times) > MAX_WAYPOINTS_PER_ACTOR:
		raise ValueError(
			f"Trajectory exceeds the limit of {MAX_WAYPOINTS_PER_ACTOR} waypoints."
		)
	speeds = trajectory.get("speed_mps")
	if not isinstance(speeds, list) or len(speeds) != len(times):
		speeds = [None] * len(times)
	return [
		Waypoint(
			time_s=float(time_s),
			x_m=float(x_m),
			y_m=float(y_m),
			speed_mps=None if speed_mps is None else float(speed_mps),
		)
		for time_s, x_m, y_m, speed_mps in zip(times, xs, ys, speeds)
	]


def load_trajectory_json(input_path: Path) -> dict[str, list[Waypoint]]:
    with input_path.open(encoding="utf-8") as file:
        raw_trajectories = json.load(file)
    if not isinstance(raw_trajectories, dict):
        raise ValueError(  # noqa: TRY004 - invalid imported data uses the loader error contract.
            "Trajectory JSON must contain a vehicle-object mapping.",
        )
    if len(raw_trajectories) > MAX_IMPORTED_ACTORS:
        raise ValueError(f"Scenario exceeds the limit of {MAX_IMPORTED_ACTORS} actors.")
    vehicles: dict[str, list[Waypoint]] = {}
    total_waypoints = 0
    for raw_name, raw_trajectory in raw_trajectories.items():
        if not isinstance(raw_trajectory, dict):
            raise ValueError(  # noqa: TRY004 - invalid imported data uses the loader error contract.
                f"{raw_name}: trajectory must be an object.",
            )
        waypoints = waypoints_from_trajectory(raw_trajectory)
        total_waypoints += len(waypoints)
        if total_waypoints > MAX_TOTAL_WAYPOINTS:
            raise ValueError(
                f"Scenario exceeds the limit of {MAX_TOTAL_WAYPOINTS} waypoints."
            )
        vehicles[safe_vehicle_name(str(raw_name))] = waypoints
    if not vehicles:
        raise ValueError("No vehicle trajectories found in JSON.")
    return vehicles


def load_trajectory_json_dimensions(input_path: Path) -> dict[str, VehicleDimensions]:
    with input_path.open(encoding="utf-8") as file:
        raw_trajectories = json.load(file)
    if not isinstance(raw_trajectories, dict):
        return {}
    dimensions: dict[str, VehicleDimensions] = {}
    for raw_name, raw_trajectory in raw_trajectories.items():
        if not isinstance(raw_trajectory, dict):
            continue
        raw_dimensions = raw_trajectory.get("dimensions")
        if not isinstance(raw_dimensions, dict):
            continue
        name = safe_vehicle_name(str(raw_name))
        dimensions[name] = VehicleDimensions(
            length_m=float(raw_dimensions.get("length_m", 4.5)),
            width_m=float(raw_dimensions.get("width_m", 1.8)),
            height_m=float(raw_dimensions.get("height_m", 1.8)),
            actor_type=str(
                raw_dimensions.get(
                    "actor_type",
                    raw_trajectory.get("actor_type", "vehicle"),
                ),
            ),
            carla_blueprint=str(
                raw_dimensions.get(
                    "carla_blueprint",
                    raw_trajectory.get("carla_blueprint", ""),
                ),
            ),
            xosc_export_mode=str(
                raw_dimensions.get(
                    "xosc_export_mode",
                    raw_trajectory.get("xosc_export_mode", "trajectory"),
                ),
            ),
            parameter_declarations=str(
                raw_dimensions.get(
                    "parameter_declarations",
                    raw_trajectory.get("parameter_declarations", ""),
                ),
            ),
            controller_name=str(
                raw_dimensions.get(
                    "controller_name",
                    raw_trajectory.get("controller_name", ""),
                ),
            ),
            controller_xml=str(
                raw_dimensions.get(
                    "controller_xml",
                    raw_trajectory.get("controller_xml", ""),
                ),
            ),
            parked_yaw_rad=float(
                raw_dimensions.get(
                    "parked_yaw_rad",
                    raw_trajectory.get("parked_yaw_rad", 0.0),
                ),
            ),
        )
    return dimensions


def xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_by_local_name(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in parent:
        if xml_local_name(child.tag) == name:
            return child
    return None


def load_openscenario_xosc(input_path: Path) -> dict[str, list[Waypoint]]:
	root = safe_xml_parse(input_path).getroot()
	initial_speeds: dict[str, float] = {}
	for private in root.iter():
		if xml_local_name(private.tag) != "Private":
			continue
		entity_ref = private.attrib.get("entityRef")
		if not entity_ref:
			continue
		for element in private.iter():
			if xml_local_name(element.tag) == "AbsoluteTargetSpeed":
				try:
					initial_speeds[entity_ref] = max(
						0.0, float(element.attrib.get("value", "0"))
					)
				except ValueError:
					pass
				break
	vehicles: dict[str, list[Waypoint]] = {}
	total_waypoints = 0
	for maneuver_group in root.iter():
		if xml_local_name(maneuver_group.tag) != "ManeuverGroup":
			continue
		actors = child_by_local_name(maneuver_group, "Actors")
		entity_name: str | None = None
		if actors is not None:
			for actor_child in actors:
				if xml_local_name(actor_child.tag) == "EntityRef":
					entity_name = actor_child.attrib.get("entityRef")
					break
		if not entity_name:
			entity_name = maneuver_group.attrib.get("name", "vehicle").replace("_maneuver_group", "")
		waypoints: list[Waypoint] = []
		for vertex in maneuver_group.iter():
			if xml_local_name(vertex.tag) != "Vertex":
				continue
			position = child_by_local_name(vertex, "Position")
			world_position = child_by_local_name(position, "WorldPosition")
			if world_position is None:
				continue
			if len(waypoints) >= MAX_WAYPOINTS_PER_ACTOR:
				raise ValueError(
					f"OpenSCENARIO actor exceeds the limit of {MAX_WAYPOINTS_PER_ACTOR} waypoints."
				)
			waypoints.append(
				Waypoint(
					time_s=finite_number(vertex.attrib["time"], "vertex time"),
					x_m=finite_number(world_position.attrib["x"], "world x"),
					y_m=finite_number(world_position.attrib["y"], "world y"),
				)
			)
		if waypoints:
			total_waypoints += len(waypoints)
			if total_waypoints > MAX_TOTAL_WAYPOINTS:
				raise ValueError(
					f"OpenSCENARIO exceeds the limit of {MAX_TOTAL_WAYPOINTS} waypoints."
				)
			ordered = sorted(waypoints, key=lambda waypoint: waypoint.time_s)
			ordered[0].speed_mps = initial_speeds.get(entity_name, 0.0)
			vehicles[safe_vehicle_name(entity_name)] = ordered
			if len(vehicles) > MAX_IMPORTED_ACTORS:
				raise ValueError(
					f"OpenSCENARIO exceeds the limit of {MAX_IMPORTED_ACTORS} actors."
				)
	if not vehicles:
		raise ValueError("No FollowTrajectory polyline vertices found in XOSC.")
	return vehicles


def load_openscenario_map_reference(input_path: Path) -> str | None:
    """Return the unmodified OpenSCENARIO LogicFile filepath value."""
    root = safe_xml_parse(input_path).getroot()
    for road_network in root.iter():
        if xml_local_name(road_network.tag) != "RoadNetwork":
            continue
        logic_file = child_by_local_name(road_network, "LogicFile")
        if logic_file is None:
            return None
        filepath = logic_file.attrib.get("filepath")
        if not filepath:
            return None
        return filepath
    return None


def load_openscenario_map_path(input_path: Path) -> Path | None:
    filepath = load_openscenario_map_reference(input_path)
    if filepath:
        map_path = Path(filepath)
        if map_path.is_absolute():
            return map_path
        return input_path.parent / map_path
    return None


XODR_GEOMETRY_SAMPLE_SPACING_M = 0.5


def polynomial_value(
    coefficients: tuple[float, float, float, float],
    parameter: float,
) -> float:
    """Evaluate an OpenDRIVE cubic polynomial."""
    a, b, c, d = coefficients
    return a + parameter * (b + parameter * (c + parameter * d))


def polynomial_derivative(
    coefficients: tuple[float, float, float, float],
    parameter: float,
) -> float:
    """Evaluate the derivative of an OpenDRIVE cubic polynomial."""
    _a, b, c, d = coefficients
    return b + parameter * (2.0 * c + parameter * 3.0 * d)


def spiral_local_position(
    local_s: float,
    curvature_start: float,
    curvature_end: float,
    length: float,
) -> tuple[float, float]:
    """Numerically integrate an OpenDRIVE clothoid in its local coordinate frame."""
    if local_s <= 0.0:
        return 0.0, 0.0
    curvature_change = (
        (curvature_end - curvature_start) / length if length > 0.0 else 0.0
    )
    max_heading_change = (
        abs(curvature_start) * local_s + 0.5 * abs(curvature_change) * local_s**2
    )
    if not math.isfinite(max_heading_change):
        raise ValueError("OpenDRIVE spiral parameters must be finite.")
    interval_count = max(2, math.ceil(max_heading_change / 0.01))
    if interval_count > MAX_POINTS_PER_ROAD:
        raise ValueError("OpenDRIVE spiral exceeds the supported computation limit.")
    if interval_count % 2:
        interval_count += 1
    step = local_s / interval_count

    def heading_offset(distance: float) -> float:
        return curvature_start * distance + 0.5 * curvature_change * distance**2

    x_sum = y_sum = 0.0
    for index in range(interval_count + 1):
        weight = 1.0 if index in {0, interval_count} else 4.0 if index % 2 else 2.0
        angle = heading_offset(index * step)
        x_sum += weight * math.cos(angle)
        y_sum += weight * math.sin(angle)
    return x_sum * step / 3.0, y_sum * step / 3.0


def xodr_geometry_local_sample(
    geometry: ET.Element,
    local_s: float,
    length: float,
) -> tuple[float, float, float]:
    """Return local x, y and heading offset for every OpenDRIVE planView primitive."""
    primitive = next(
        (
            child
            for child in geometry
            if xml_local_name(child.tag)
            in {"line", "arc", "spiral", "poly3", "paramPoly3"}
        ),
        None,
    )
    if primitive is None:
        raise ValueError("OpenDRIVE geometry has no supported planView primitive.")
    primitive_name = xml_local_name(primitive.tag)
    if primitive_name == "line":
        return local_s, 0.0, 0.0
    if primitive_name == "arc":
        curvature = float(primitive.attrib.get("curvature", "0"))
        if abs(curvature) <= 1e-12:
            return local_s, 0.0, 0.0
        return (
            math.sin(curvature * local_s) / curvature,
            (1.0 - math.cos(curvature * local_s)) / curvature,
            curvature * local_s,
        )
    if primitive_name == "spiral":
        curvature_start = float(primitive.attrib["curvStart"])
        curvature_end = float(primitive.attrib["curvEnd"])
        local_x, local_y = spiral_local_position(
            local_s,
            curvature_start,
            curvature_end,
            length,
        )
        curvature_change = (
            (curvature_end - curvature_start) / length if length > 0.0 else 0.0
        )
        return (
            local_x,
            local_y,
            curvature_start * local_s + 0.5 * curvature_change * local_s**2,
        )
    if primitive_name == "poly3":
        coefficients = tuple(
            float(primitive.attrib.get(name, "0")) for name in ("a", "b", "c", "d")
        )
        local_y = polynomial_value(coefficients, local_s)
        return (
            local_s,
            local_y,
            math.atan2(polynomial_derivative(coefficients, local_s), 1.0),
        )
    parameter_range = primitive.attrib.get("pRange", "arcLength")
    if parameter_range == "normalized":
        parameter = local_s / length if length > 0.0 else 0.0
    elif parameter_range == "arcLength":
        parameter = local_s
    else:
        raise ValueError(
            f"Unsupported OpenDRIVE paramPoly3 pRange: {parameter_range!r}",
        )
    u_coefficients = tuple(
        float(primitive.attrib.get(name, "0")) for name in ("aU", "bU", "cU", "dU")
    )
    v_coefficients = tuple(
        float(primitive.attrib.get(name, "0")) for name in ("aV", "bV", "cV", "dV")
    )
    local_x = polynomial_value(u_coefficients, parameter)
    local_y = polynomial_value(v_coefficients, parameter)
    return (
        local_x,
        local_y,
        math.atan2(
            polynomial_derivative(v_coefficients, parameter),
            polynomial_derivative(u_coefficients, parameter),
        ),
    )


def sample_xodr_geometry_samples(
    geometry: ET.Element,
) -> list[tuple[float, float, float, float]]:
    x = finite_number(geometry.attrib["x"], "OpenDRIVE geometry x")
    y = finite_number(geometry.attrib["y"], "OpenDRIVE geometry y")
    heading = finite_number(geometry.attrib["hdg"], "OpenDRIVE geometry heading")
    length = finite_number(geometry.attrib["length"], "OpenDRIVE geometry length")
    geometry_s = finite_number(geometry.attrib.get("s", "0"), "OpenDRIVE geometry s")
    if length < 0.0:
        raise ValueError("OpenDRIVE geometry length must not be negative.")
    sample_count = max(2, math.ceil(length / XODR_GEOMETRY_SAMPLE_SPACING_M) + 1)
    if sample_count > MAX_POINTS_PER_ROAD:
        raise ValueError(
            f"OpenDRIVE geometry exceeds the limit of {MAX_POINTS_PER_ROAD} samples."
        )
    samples: list[tuple[float, float, float, float]] = []
    for index in range(sample_count):
        local_s = length * index / (sample_count - 1)
        local_x, local_y, heading_offset = xodr_geometry_local_sample(
            geometry,
            local_s,
            length,
        )
        world_x = x + local_x * math.cos(heading) - local_y * math.sin(heading)
        world_y = y + local_x * math.sin(heading) + local_y * math.cos(heading)
        samples.append(
            (geometry_s + local_s, world_x, world_y, heading + heading_offset),
        )
    return samples


def sample_xodr_geometry(geometry: ET.Element) -> list[tuple[float, float]]:
    return [(x, y) for _s, x, y, _heading in sample_xodr_geometry_samples(geometry)]


def lane_width_at(lane: ET.Element, section_s: float, road_s: float) -> float:
    ds = max(0.0, road_s - section_s)
    widths = [width for width in lane if xml_local_name(width.tag) == "width"]
    if not widths:
        return 0.0
    width = max(
        (
            width
            for width in widths
            if float(width.attrib.get("sOffset", "0")) <= ds + 1e-9
        ),
        key=lambda item: float(item.attrib.get("sOffset", "0")),
        default=widths[0],
    )
    local_s = ds - float(width.attrib.get("sOffset", "0"))
    a = float(width.attrib.get("a", "0"))
    b = float(width.attrib.get("b", "0"))
    c = float(width.attrib.get("c", "0"))
    d = float(width.attrib.get("d", "0"))
    return a + b * local_s + c * local_s**2 + d * local_s**3


def lane_section_width(section: ET.Element, side_name: str, road_s: float) -> float:
    section_s = float(section.attrib.get("s", "0"))
    side = child_by_local_name(section, side_name)
    if side is None:
        return 0.0
    total_width = 0.0
    for lane in side:
        if xml_local_name(lane.tag) != "lane":
            continue
        lane_id = int(lane.attrib.get("id", "0"))
        if lane_id == 0:
            continue
        total_width += max(0.0, lane_width_at(lane, section_s, road_s))
    return total_width


def lane_section_lane_widths(section: ET.Element, side_name: str) -> list[float]:
    side = child_by_local_name(section, side_name)
    if side is None:
        return []
    section_s = float(section.attrib.get("s", "0"))
    widths: list[tuple[int, float]] = []
    for lane in side:
        if xml_local_name(lane.tag) != "lane":
            continue
        lane_id = int(lane.attrib.get("id", "0"))
        if lane_id == 0:
            continue
        widths.append(
            (abs(lane_id), max(0.0, lane_width_at(lane, section_s, section_s))),
        )
    return [width for _lane_id, width in sorted(widths)]


def lane_section_lane_width_map(section: ET.Element | None) -> dict[int, float]:
    if section is None:
        return {}
    section_s = float(section.attrib.get("s", "0"))
    lane_widths: dict[int, float] = {}
    for side_name in ("left", "right"):
        side = child_by_local_name(section, side_name)
        if side is None:
            continue
        for lane in side:
            if xml_local_name(lane.tag) != "lane":
                continue
            lane_id = int(lane.attrib.get("id", "0"))
            if lane_id == 0:
                continue
            width = max(0.0, lane_width_at(lane, section_s, section_s))
            if width > 0.0:
                lane_widths[lane_id] = width
    return lane_widths


def lane_section_lane_type_map(section: ET.Element | None) -> dict[int, str]:
    """Return OpenDRIVE lane types keyed by lane id for one lane section."""
    if section is None:
        return {}
    lane_types: dict[int, str] = {}
    for side_name in ("left", "right"):
        side = child_by_local_name(section, side_name)
        if side is None:
            continue
        for lane in side:
            if xml_local_name(lane.tag) != "lane":
                continue
            lane_id = int(lane.attrib.get("id", "0"))
            if lane_id:
                lane_types[lane_id] = lane.attrib.get("type", "none")
    return lane_types


def road_lane_offset(road: ET.Element, road_s: float) -> float:
    """Evaluate the active OpenDRIVE ``laneOffset`` polynomial at ``road_s``."""
    lanes = child_by_local_name(road, "lanes")
    if lanes is None:
        return 0.0
    offsets = sorted(
        (element for element in lanes if xml_local_name(element.tag) == "laneOffset"),
        key=lambda element: float(element.attrib.get("s", "0")),
    )
    active = None
    for offset in offsets:
        if float(offset.attrib.get("s", "0")) <= road_s + 1e-9:
            active = offset
        else:
            break
    if active is None:
        return 0.0
    local_s = road_s - float(active.attrib.get("s", "0"))
    return sum(
        float(active.attrib.get(coefficient, "0")) * local_s**power
        for power, coefficient in enumerate(("a", "b", "c", "d"))
    )


def road_profile_value(
    road: ET.Element,
    profile_name: str,
    entry_name: str,
    road_s: float,
) -> float:
    """Evaluate one active OpenDRIVE longitudinal profile polynomial."""
    profile = child_by_local_name(road, profile_name)
    if profile is None:
        return 0.0
    entries = sorted(
        (element for element in profile if xml_local_name(element.tag) == entry_name),
        key=lambda element: float(element.attrib.get("s", "0")),
    )
    active = None
    for entry in entries:
        if float(entry.attrib.get("s", "0")) <= road_s + 1e-9:
            active = entry
        else:
            break
    if active is None:
        return 0.0
    local_s = road_s - float(active.attrib.get("s", "0"))
    coefficients = tuple(
        float(active.attrib.get(coefficient, "0"))
        for coefficient in ("a", "b", "c", "d")
    )
    return polynomial_value(coefficients, local_s)


def road_profile_entries(
    road: ET.Element,
    profile_name: str,
    entry_name: str,
) -> list[CubicRoadProfile]:
    """Return sorted cubic entries from one OpenDRIVE road profile."""
    profile = child_by_local_name(road, profile_name)
    if profile is None:
        return []
    entries = [
        CubicRoadProfile(
            s_m=float(element.attrib.get("s", "0")),
            a=float(element.attrib.get("a", "0")),
            b=float(element.attrib.get("b", "0")),
            c=float(element.attrib.get("c", "0")),
            d=float(element.attrib.get("d", "0")),
        )
        for element in profile
        if xml_local_name(element.tag) == entry_name
    ]
    return sorted(entries, key=lambda entry: entry.s_m)


def speed_limit_mps(speed: ET.Element | None) -> float | None:
	"""Parse one OpenDRIVE speed element and normalize it to m/s."""
	if speed is None:
		return None
	raw_value = str(speed.attrib.get("max", "")).strip().lower()
	if raw_value in {"", "no limit", "nolimit", "undefined"}:
		return None
	try:
		value = float(raw_value)
	except ValueError:
		return None
	unit = str(speed.attrib.get("unit", "m/s")).strip().lower()
	if unit in {"km/h", "kmh", "kph"}:
		value /= 3.6
	elif unit in {"mph", "mi/h"}:
		value *= 0.44704
	elif unit not in {"m/s", "mps", "ms-1"}:
		return None
	return max(0.0, value)


def road_speed_limit_at(road: ET.Element, road_s: float) -> float | None:
	"""Return the road-type speed limit active at one road coordinate."""
	active_type = None
	for element in road:
		if xml_local_name(element.tag) != "type":
			continue
		if float(element.attrib.get("s", "0")) <= road_s + 1e-9:
			active_type = element
		else:
			break
	return speed_limit_mps(child_by_local_name(active_type, "speed"))


def lane_speed_limit_at(
	lane: ET.Element,
	section_s: float,
	road_s: float,
) -> float | None:
	"""Return the lane speed limit active inside one lane section."""
	local_s = road_s - section_s
	active_speed = None
	for element in lane:
		if xml_local_name(element.tag) != "speed":
			continue
		if float(element.attrib.get("sOffset", "0")) <= local_s + 1e-9:
			active_speed = element
		else:
			break
	return speed_limit_mps(active_speed)


def lane_cross_sections_for_road(
    road: ET.Element,
    sections: list[ET.Element],
    samples: list[tuple[float, float, float, float]],
) -> list[LaneCrossSection]:
    """Return the active lane layout at every sampled road position."""
    profiles: list[LaneCrossSection] = []
    for road_s, x_m, y_m, heading_rad in samples:
        section = lane_section_at(sections, road_s)
        if section is None:
            continue
        section_s = float(section.attrib.get("s", "0"))
        lane_widths: dict[int, float] = {}
        lane_types: dict[int, str] = {}
        lane_speed_limits: dict[int, float] = {}
        for side_name in ("left", "right"):
            side = child_by_local_name(section, side_name)
            if side is None:
                continue
            for lane in side:
                if xml_local_name(lane.tag) != "lane":
                    continue
                lane_id = int(lane.attrib.get("id", "0"))
                if lane_id == 0:
                    continue
                lane_widths[lane_id] = max(0.0, lane_width_at(lane, section_s, road_s))
                lane_types[lane_id] = lane.attrib.get("type", "driving").lower()
                lane_speed_limit = lane_speed_limit_at(
                    lane, section_s, road_s
                )
                if lane_speed_limit is not None:
                    lane_speed_limits[lane_id] = lane_speed_limit
        profiles.append(
            LaneCrossSection(
                s_m=road_s,
                x_m=x_m,
                y_m=y_m,
                heading_rad=heading_rad,
                lane_offset_m=road_lane_offset(road, road_s),
                lane_widths_m=lane_widths,
                lane_types=lane_types,
                lane_speed_limits_mps=lane_speed_limits,
                road_speed_limit_mps=road_speed_limit_at(road, road_s),
                elevation_m=road_profile_value(
                    road,
                    "elevationProfile",
                    "elevation",
                    road_s,
                ),
                superelevation_rad=road_profile_value(
                    road,
                    "lateralProfile",
                    "superelevation",
                    road_s,
                ),
            ),
        )
    return profiles


def lane_section_metadata(section: ET.Element | None) -> tuple[int, float, float]:
    if section is None:
        return (2, 3.0, 6.0)
    lane_widths = [
        width for width in lane_section_lane_width_map(section).values() if width > 0.0
    ]
    if not lane_widths:
        return (2, 3.0, 6.0)
    total_width = sum(lane_widths)
    lane_count = len(lane_widths)
    return (lane_count, total_width / lane_count, total_width)


def lane_sections_for_road(road: ET.Element) -> list[ET.Element]:
    lanes = child_by_local_name(road, "lanes")
    if lanes is None:
        return []
    sections = [
        section for section in lanes if xml_local_name(section.tag) == "laneSection"
    ]
    return sorted(sections, key=lambda section: float(section.attrib.get("s", "0")))


def lane_section_at(sections: list[ET.Element], road_s: float) -> ET.Element | None:
    active_section: ET.Element | None = None
    for section in sections:
        if float(section.attrib.get("s", "0")) <= road_s + 1e-9:
            active_section = section
        else:
            break
    return active_section


def offset_point(
    x: float,
    y: float,
    heading: float,
    lateral_offset: float,
) -> tuple[float, float]:
    return (
        x - math.sin(heading) * lateral_offset,
        y + math.cos(heading) * lateral_offset,
    )


def road_link_target(road: ET.Element, link_name: str) -> str:
    link = child_by_local_name(road, "link")
    if link is None:
        return ""
    target = child_by_local_name(link, link_name)
    if target is None or target.attrib.get("elementType") != "road":
        return ""
    return str(target.attrib.get("elementId", ""))


def append_road_link(
    parent: ET.Element,
    tag_name: str,
    target_id: str,
    contact_point: str,
):
    if target_id:
        ET.SubElement(
            parent,
            tag_name,
            elementType="road",
            elementId=target_id,
            contactPoint=contact_point,
        )


def append_lane_link(parent: ET.Element, tag_name: str, target_lane_id: int | None):
    if target_lane_id is not None:
        ET.SubElement(parent, tag_name, id=str(target_lane_id))


def road_lane_ids(polyline: MapPolyline) -> set[int]:
    return set(polyline.opendrive_lane_ids())


def parse_lane_link_spec(
    link_spec: str,
    source_ids: set[int],
    target_ids: set[int],
) -> dict[int, int]:
    links: dict[int, int] = {}
    for part in link_spec.replace(",", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        if "->" in part:
            source_text, target_text = part.split("->", 1)
        else:
            source_text = target_text = part
        source_lane = int(source_text.strip())
        target_lane = int(target_text.strip())
        if source_lane in source_ids and target_lane in target_ids:
            links[source_lane] = target_lane
    return links


def append_polyline(
    polylines: list[MapPolyline],
    name: str,
    points: list[tuple[float, float]],
    kind: str,
    width_m: float = 0.0,
    lane_count: int = 2,
    lane_width_m: float = 3.0,
    predecessor_road: str = "",
    successor_road: str = "",
    lane_widths_m: dict[int, float] | None = None,
    lane_types: dict[int, str] | None = None,
    lane_cross_sections: list[LaneCrossSection] | None = None,
    elevation_profile: list[CubicRoadProfile] | None = None,
    superelevation_profile: list[CubicRoadProfile] | None = None,
    source_length_m: float = 0.0,
):
    if len(points) >= 2:
        polylines.append(
            MapPolyline(
                name=name,
                points=points,
                kind=kind,
                width_m=width_m,
                lane_count=lane_count,
                lane_width_m=lane_width_m,
                predecessor_road=predecessor_road,
                successor_road=successor_road,
                lane_widths_m=dict(lane_widths_m or {}),
                lane_types=dict(lane_types or {}),
                lane_cross_sections=list(lane_cross_sections or []),
                elevation_profile=list(elevation_profile or []),
                superelevation_profile=list(superelevation_profile or []),
                source_length_m=source_length_m,
            ),
        )


def road_plan_view_samples(road: ET.Element) -> list[tuple[float, float, float, float]]:
    plan_view = child_by_local_name(road, "planView")
    if plan_view is None:
        return []
    samples: list[tuple[float, float, float, float]] = []
    for geometry in plan_view:
        if xml_local_name(geometry.tag) != "geometry":
            continue
        geometry_samples = sample_xodr_geometry_samples(geometry)
        if (
            samples
            and geometry_samples
            and abs(samples[-1][0] - geometry_samples[0][0]) <= 1e-9
        ):
            # The following geometry owns a shared boundary according to its explicit
            # x/y/hdg attributes. Two samples create a zero-length connector there.
            samples[-1] = geometry_samples[0]
            samples.extend(geometry_samples[1:])
            continue
        samples.extend(geometry_samples)
    return samples


def sample_road_at_s(
    road: ET.Element,
    road_s: float,
) -> tuple[float, float, float, float] | None:
    """Evaluate the plan-view reference geometry exactly at one road coordinate."""
    plan_view = child_by_local_name(road, "planView")
    if plan_view is None:
        return None
    geometries = [item for item in plan_view if xml_local_name(item.tag) == "geometry"]
    for geometry in reversed(geometries):
        geometry_s = float(geometry.attrib.get("s", "0"))
        length = float(geometry.attrib.get("length", "0"))
        if geometry_s - 1e-9 <= road_s <= geometry_s + length + 1e-9:
            local_s = min(length, max(0.0, road_s - geometry_s))
            x_m = float(geometry.attrib["x"])
            y_m = float(geometry.attrib["y"])
            heading = float(geometry.attrib["hdg"])
            local_x, local_y, heading_offset = xodr_geometry_local_sample(
                geometry,
                local_s,
                length,
            )
            return (
                road_s,
                x_m + local_x * math.cos(heading) - local_y * math.sin(heading),
                y_m + local_x * math.sin(heading) + local_y * math.cos(heading),
                heading + heading_offset,
            )
    return None


def add_lane_transition_samples(
    road: ET.Element,
    samples: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Add exact samples at lane and road-profile polynomial transitions."""
    lanes = child_by_local_name(road, "lanes")
    transition_s: set[float] = set()
    for element in road:
        if xml_local_name(element.tag) == "type":
            transition_s.add(float(element.attrib.get("s", "0")))
    if lanes is not None:
        for element in lanes:
            tag_name = xml_local_name(element.tag)
            if tag_name == "laneOffset":
                transition_s.add(float(element.attrib.get("s", "0")))
            elif tag_name == "laneSection":
                section_s = float(element.attrib.get("s", "0"))
                transition_s.add(section_s)
                for side in element:
                    for lane in side:
                        for lane_property in lane:
                            property_name = xml_local_name(lane_property.tag)
                            if property_name == "width":
                                transition_s.add(
                                    section_s
                                    + float(
                                        lane_property.attrib.get("sOffset", "0")
                                    )
                                )
                            elif property_name == "speed":
                                transition_s.add(
                                    section_s
                                    + float(
                                        lane_property.attrib.get("sOffset", "0")
                                    )
                                )
    for profile_name, entry_name in (
        ("elevationProfile", "elevation"),
        ("lateralProfile", "superelevation"),
    ):
        profile = child_by_local_name(road, profile_name)
        if profile is None:
            continue
        transition_s.update(
            float(element.attrib.get("s", "0"))
            for element in profile
            if xml_local_name(element.tag) == entry_name
        )
    exact_samples = [sample_road_at_s(road, road_s) for road_s in transition_s]
    ordered_samples = sorted(
        samples + [sample for sample in exact_samples if sample is not None],
        key=lambda sample: sample[0],
    )
    unique_samples: list[tuple[float, float, float, float]] = []
    for sample in ordered_samples:
        if unique_samples and abs(unique_samples[-1][0] - sample[0]) <= 1e-9:
            # Exact transition samples are appended last and replace regular samples.
            unique_samples[-1] = sample
        else:
            unique_samples.append(sample)
    return unique_samples


def road_name_for_import(road: ET.Element, existing_count: int) -> str:
    return (
        road.attrib.get("name") or road.attrib.get("id") or f"road_{existing_count + 1}"
    )


def append_imported_reference_polyline(
    polylines: list[MapPolyline],
    road: ET.Element,
    road_name: str,
    samples: list[tuple[float, float, float, float]],
) -> list[ET.Element]:
    reference_points = simplify_polyline(
        [(x, y) for _s, x, y, _heading in samples],
        load_map_export_max_lateral_deviation_m(),
    )
    sections = lane_sections_for_road(road)
    profiles = lane_cross_sections_for_road(road, sections, samples)
    first_lane_section = sections[0] if sections else None
    lane_count, lane_width_m, road_width_m = lane_section_metadata(first_lane_section)
    lane_widths_m = lane_section_lane_width_map(first_lane_section)
    lane_types = lane_section_lane_type_map(first_lane_section)

    append_polyline(
        polylines,
        road_name,
        reference_points,
        "reference",
        width_m=road_width_m,
        lane_count=lane_count,
        lane_width_m=lane_width_m,
        predecessor_road=road_link_target(road, "predecessor"),
        successor_road=road_link_target(road, "successor"),
        lane_widths_m=lane_widths_m,
        lane_types=lane_types,
        lane_cross_sections=profiles,
        elevation_profile=road_profile_entries(road, "elevationProfile", "elevation"),
        superelevation_profile=road_profile_entries(
            road,
            "lateralProfile",
            "superelevation",
        ),
        source_length_m=float(road.attrib.get("length", "0")),
    )
    return sections


def append_imported_outer_polylines(
    polylines: list[MapPolyline],
    road: ET.Element,
    road_name: str,
    sections: list[ET.Element],
    samples: list[tuple[float, float, float, float]],
):
    left_points: list[tuple[float, float]] = []
    right_points: list[tuple[float, float]] = []
    for road_s, x, y, heading in samples:
        section = lane_section_at(sections, road_s)
        if section is None:
            continue
        left_width = lane_section_width(section, "left", road_s)
        right_width = lane_section_width(section, "right", road_s)
        lane_offset = road_lane_offset(road, road_s)
        if left_width > 0.0:
            left_points.append(offset_point(x, y, heading, lane_offset + left_width))
        if right_width > 0.0:
            right_points.append(offset_point(x, y, heading, lane_offset - right_width))

    append_polyline(polylines, f"{road_name} left outer", left_points, "outer")
    append_polyline(polylines, f"{road_name} right outer", right_points, "outer")


def append_imported_section_polylines(
    polylines: list[MapPolyline],
    road_name: str,
    sections: list[ET.Element],
    samples: list[tuple[float, float, float, float]],
):
    for index, section in enumerate(sections, start=1):
        section_s = float(section.attrib.get("s", "0"))
        road_s, x, y, heading = min(
            samples,
            key=lambda sample: abs(sample[0] - section_s),
        )
        left_width = lane_section_width(section, "left", road_s)
        right_width = lane_section_width(section, "right", road_s)
        if left_width <= 0.0 and right_width <= 0.0:
            continue
        section_points = [
            offset_point(x, y, heading, -right_width),
            offset_point(x, y, heading, left_width),
        ]
        append_polyline(
            polylines,
            f"{road_name} section {index}",
            section_points,
            "section",
        )


def append_imported_road(
    polylines: list[MapPolyline],
    road: ET.Element,
    include_helpers: bool = True,
):
    samples = add_lane_transition_samples(road, road_plan_view_samples(road))
    if len(samples) < 2:
        return
    road_name = road_name_for_import(road, len(polylines))
    sections = append_imported_reference_polyline(polylines, road, road_name, samples)
    if not include_helpers or not sections:
        return
    append_imported_outer_polylines(polylines, road, road_name, sections, samples)
    append_imported_section_polylines(polylines, road_name, sections, samples)


def load_xodr_map(input_path: Path, include_helpers: bool = True) -> list[MapPolyline]:
    root = safe_xml_parse(input_path).getroot()
    polylines: list[MapPolyline] = []
    road_count = sum(1 for road in root.iter() if xml_local_name(road.tag) == "road")
    if road_count > MAX_IMPORTED_ROADS:
        raise ValueError(f"Map exceeds the limit of {MAX_IMPORTED_ROADS} roads.")
    total_points = 0
    for road in root.iter():
        if xml_local_name(road.tag) == "road":
            previous_polyline_count = len(polylines)
            append_imported_road(polylines, road, include_helpers=include_helpers)
            total_points += sum(
                len(polyline.points) for polyline in polylines[previous_polyline_count:]
            )
            if total_points > MAX_TOTAL_MAP_POINTS:
                raise ValueError(
                    f"Map exceeds the limit of {MAX_TOTAL_MAP_POINTS} points."
                )
    if not polylines:
        raise ValueError("No road reference lines found in XODR.")
    validate_imported_map(polylines)
    return polylines


def load_xodr_reference_map(input_path: Path) -> list[MapPolyline]:
    """Load only road reference lines for fast map visualization."""
    return load_xodr_map(input_path, include_helpers=False)


def _point_to_segment_distance_m(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    x_m, y_m = point
    x0_m, y0_m = start
    x1_m, y1_m = end
    dx_m = x1_m - x0_m
    dy_m = y1_m - y0_m
    length_squared_m = dx_m * dx_m + dy_m * dy_m
    if length_squared_m <= 1e-12:
        return math.hypot(x_m - x0_m, y_m - y0_m)
    projection = ((x_m - x0_m) * dx_m + (y_m - y0_m) * dy_m) / length_squared_m
    projection = min(1.0, max(0.0, projection))
    closest_x_m = x0_m + projection * dx_m
    closest_y_m = y0_m + projection * dy_m
    return math.hypot(x_m - closest_x_m, y_m - closest_y_m)


def simplify_polyline(
    points: list[tuple[float, float]],
    max_lateral_deviation_m: float,
) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    tolerance_m = max(max_lateral_deviation_m, 0.001)
    keep_indices = {0, len(points) - 1}
    stack: list[tuple[int, int]] = [(0, len(points) - 1)]
    while stack:
        start_index, end_index = stack.pop()
        start = points[start_index]
        end = points[end_index]
        max_distance_m = -1.0
        max_index = -1
        for candidate_index in range(start_index + 1, end_index):
            distance_m = _point_to_segment_distance_m(
                points[candidate_index],
                start,
                end,
            )
            if distance_m > max_distance_m:
                max_distance_m = distance_m
                max_index = candidate_index
        if max_distance_m > tolerance_m and max_index >= 0:
            keep_indices.add(max_index)
            stack.append((start_index, max_index))
            stack.append((max_index, end_index))
    return [points[index] for index in sorted(keep_indices)]


def sampled_map_points(
    polyline: MapPolyline,
    max_lateral_deviation_m: float | None = None,
) -> list[tuple[float, float]]:
    if len(polyline.points) < 2:
        return polyline.points
    waypoints = [
        Waypoint(float(index), x_m, y_m)
        for index, (x_m, y_m) in enumerate(polyline.points)
    ]
    sampled_points = [
        (x_m, y_m)
        for _time_s, x_m, y_m in Trajectory(waypoints=waypoints).sampled_curve_points()
    ]
    tolerance_m = (
        load_map_export_max_lateral_deviation_m()
        if max_lateral_deviation_m is None
        else max_lateral_deviation_m
    )
    return simplify_polyline(sampled_points, tolerance_m)


def polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(
        math.hypot(x1 - x0, y1 - y0)
        for (x0, y0), (x1, y1) in itertools.pairwise(points)
    )


def exportable_map_polylines(polylines: list[MapPolyline]) -> list[MapPolyline]:
    editable_polylines = [
        polyline
        for polyline in polylines
        if polyline.kind == "reference" and len(polyline.points) >= 2
    ]
    if editable_polylines:
        return editable_polylines
    return [polyline for polyline in polylines if len(polyline.points) >= 2]


def road_relation_lookup(polylines: list[MapPolyline]) -> dict[str, MapPolyline]:
    return {
        polyline.name or f"road_{index}": polyline
        for index, polyline in enumerate(polylines, start=1)
    }


def open_drive_road_ids(polylines: list[MapPolyline]) -> dict[str, str]:
    return {
        polyline.name or f"road_{index}": str(index)
        for index, polyline in enumerate(polylines, start=1)
    }


def append_road_links(
    road: ET.Element,
    polyline: MapPolyline,
    road_ids: dict[str, str],
):
    predecessor_id = road_ids.get(polyline.predecessor_road, polyline.predecessor_road)
    successor_id = road_ids.get(polyline.successor_road, polyline.successor_road)
    if not predecessor_id and not successor_id:
        return
    link = ET.SubElement(road, "link")
    append_road_link(link, "predecessor", predecessor_id, "end")
    append_road_link(link, "successor", successor_id, "start")


def append_plan_view(plan_view: ET.Element, points: list[tuple[float, float]]):
    s_offset = 0.0
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        segment_length = math.hypot(x1 - x0, y1 - y0)
        if segment_length <= 1e-9:
            continue
        heading = math.atan2(y1 - y0, x1 - x0)
        geometry = ET.SubElement(
            plan_view,
            "geometry",
            s=f"{s_offset:.6g}",
            x=f"{x0:.6g}",
            y=f"{y0:.6g}",
            hdg=f"{heading:.12g}",
            length=f"{segment_length:.6g}",
        )
        ET.SubElement(geometry, "line")
        s_offset += segment_length


def append_cubic_road_profile(
    parent: ET.Element,
    container_name: str,
    entry_name: str,
    profile: list[CubicRoadProfile],
    length_scale: float,
):
    """Append a cubic road profile scaled to the exported road length."""
    if not profile:
        return
    container = ET.SubElement(parent, container_name)
    safe_scale = length_scale if length_scale > 1e-12 else 1.0
    for entry in profile:
        ET.SubElement(
            container,
            entry_name,
            s=f"{entry.s_m * safe_scale:.12g}",
            a=f"{entry.a:.12g}",
            b=f"{entry.b / safe_scale:.12g}",
            c=f"{entry.c / safe_scale**2:.12g}",
            d=f"{entry.d / safe_scale**3:.12g}",
        )


def lane_link_sets(
    polyline: MapPolyline,
    roads_by_relation_key: dict[str, MapPolyline],
) -> tuple[set[int], set[int], dict[int, int], dict[int, int]]:
    source_lane_ids = road_lane_ids(polyline)
    predecessor = roads_by_relation_key.get(polyline.predecessor_road)
    successor = roads_by_relation_key.get(polyline.successor_road)
    predecessor_lane_ids = road_lane_ids(predecessor) if predecessor else set()
    successor_lane_ids = road_lane_ids(successor) if successor else set()
    predecessor_lane_links = parse_lane_link_spec(
        polyline.predecessor_lane_links,
        source_lane_ids,
        predecessor_lane_ids,
    )
    successor_lane_links = parse_lane_link_spec(
        polyline.successor_lane_links,
        source_lane_ids,
        successor_lane_ids,
    )
    return (
        predecessor_lane_ids,
        successor_lane_ids,
        predecessor_lane_links,
        successor_lane_links,
    )


def append_drive_lane(
    parent: ET.Element,
    polyline: MapPolyline,
    lane_id: int,
    predecessor_lane_ids: set[int],
    successor_lane_ids: set[int],
    predecessor_lane_links: dict[int, int],
    successor_lane_links: dict[int, int],
):
    lane = ET.SubElement(
        parent,
        "lane",
        id=str(lane_id),
        type=polyline.lane_type_for(lane_id),
        level="false",
    )
    if predecessor_lane_ids or successor_lane_ids:
        lane_link = ET.SubElement(lane, "link")
        append_lane_link(
            lane_link,
            "predecessor",
            predecessor_lane_links.get(
                lane_id,
                lane_id
                if lane_id in predecessor_lane_ids and not predecessor_lane_links
                else None,
            ),
        )
        append_lane_link(
            lane_link,
            "successor",
            successor_lane_links.get(
                lane_id,
                lane_id
                if lane_id in successor_lane_ids and not successor_lane_links
                else None,
            ),
        )
    ET.SubElement(
        lane,
        "width",
        sOffset="0",
        a=f"{polyline.lane_width_for(lane_id):.6g}",
        b="0",
        c="0",
        d="0",
    )


def append_lane_section(
    road: ET.Element,
    polyline: MapPolyline,
    roads_by_relation_key: dict[str, MapPolyline],
):
    lane_count = polyline.normalized_lane_count()
    left_lane_count = lane_count // 2
    right_lane_count = lane_count - left_lane_count
    (
        predecessor_lane_ids,
        successor_lane_ids,
        predecessor_lane_links,
        successor_lane_links,
    ) = lane_link_sets(polyline, roads_by_relation_key)
    lanes = ET.SubElement(road, "lanes")
    lane_section = ET.SubElement(lanes, "laneSection", s="0")
    left = ET.SubElement(lane_section, "left")
    for lane_id in range(1, left_lane_count + 1):
        append_drive_lane(
            left,
            polyline,
            lane_id,
            predecessor_lane_ids,
            successor_lane_ids,
            predecessor_lane_links,
            successor_lane_links,
        )
    center = ET.SubElement(lane_section, "center")
    ET.SubElement(center, "lane", id="0", type="none", level="false")
    right = ET.SubElement(lane_section, "right")
    for lane_number in range(1, right_lane_count + 1):
        append_drive_lane(
            right,
            polyline,
            -lane_number,
            predecessor_lane_ids,
            successor_lane_ids,
            predecessor_lane_links,
            successor_lane_links,
        )


def write_xodr_map(
    output_path: Path,
    polylines: list[MapPolyline],
    max_lateral_deviation_m: float | None = None,
):
    editable_polylines = exportable_map_polylines(polylines)
    if not editable_polylines:
        raise ValueError("A map needs at least one line with two points.")
    root = ET.Element("OpenDRIVE")
    ET.SubElement(root, "header", revMajor="1", revMinor="6", name=output_path.stem)
    road_ids = open_drive_road_ids(editable_polylines)
    roads_by_relation_key = road_relation_lookup(editable_polylines)
    tolerance_m = (
        load_map_export_max_lateral_deviation_m()
        if max_lateral_deviation_m is None
        else max_lateral_deviation_m
    )
    for index, polyline in enumerate(editable_polylines, start=1):
        road_name = polyline.name or f"road_{index}"
        points = sampled_map_points(polyline, max_lateral_deviation_m=tolerance_m)
        exported_length_m = polyline_length(points)
        road = ET.SubElement(
            root,
            "road",
            name=road_name,
            length=f"{exported_length_m:.6g}",
            id=str(index),
            junction="-1",
        )
        append_road_links(road, polyline, road_ids)
        append_plan_view(ET.SubElement(road, "planView"), points)
        length_scale = (
            exported_length_m / polyline.source_length_m
            if polyline.source_length_m > 1e-12
            else 1.0
        )
        append_cubic_road_profile(
            road,
            "elevationProfile",
            "elevation",
            polyline.elevation_profile,
            length_scale,
        )
        append_cubic_road_profile(
            road,
            "lateralProfile",
            "superelevation",
            polyline.superelevation_profile,
            length_scale,
        )
        append_lane_section(road, polyline, roads_by_relation_key)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def write_scenario_config(
	output_path: Path,
	vehicles: dict[str, list[Waypoint]],
	vehicle_dimensions: dict[str, VehicleDimensions],
	map_path: Path | None,
	detection_gaps: list[DetectionGap] | None = None,
	additional_scenario_information: dict[str, object] | None = None,
):
	output_path.parent.mkdir(parents=True, exist_ok=True)
	serialized_map_path = None
	if map_path is not None:
		normalized_map_path = map_path.expanduser()
		absolute_map_path = normalized_map_path if normalized_map_path.is_absolute() else normalized_map_path.resolve(strict=False)
		try:
			serialized_map_path = os.path.relpath(absolute_map_path, output_path.parent.resolve(strict=False))
		except ValueError:
			serialized_map_path = str(absolute_map_path)
	config = {
		"version": 4,
		"map_path": serialized_map_path,
		"detection_gaps": [
			{"vehicle_name": gap.vehicle_name, "start_time_s": gap.start_time_s, "end_time_s": gap.end_time_s}
			for gap in (detection_gaps or [])
		],
		"additional_scenario_information": additional_scenario_information,
		"vehicles": [
			{
				"name": name,
				"actor_type": vehicle_dimensions.get(name, VehicleDimensions()).actor_type,
				"dimensions": {
					**vehicle_dimensions.get(name, VehicleDimensions()).as_dict(),
					"parked_yaw_rad": vehicle_dimensions.get(name, VehicleDimensions()).parked_yaw_rad,
				},
				"waypoints": [
					{
						"time_s": waypoint.time_s,
						"x_m": waypoint.x_m,
						"y_m": waypoint.y_m,
						**(
							{"speed_mps": waypoint.speed_mps}
							if waypoint.speed_mps is not None
							else {}
						),
					}
					for waypoint in sorted(
						waypoints, key=lambda item: item.time_s
					)
				],
			}
			for name, waypoints in vehicles.items()
		],
	}
	with output_path.open("w", encoding="utf-8") as file:
		json.dump(config, file, indent=2)
		file.write("")


def load_scenario_config_additional_information(
    input_path: Path,
) -> dict[str, object] | None:
    with input_path.open(encoding="utf-8") as file:
        config = json.load(file)
    if not isinstance(config, dict):
        return None
    raw_additional_information = config.get("additional_scenario_information")
    if isinstance(raw_additional_information, dict):
        return dict(raw_additional_information)
    raw_environment = config.get("environmental_conditions")
    if isinstance(raw_environment, dict):
        return {"environment": dict(raw_environment)}
    return None


def load_scenario_config_environment(input_path: Path) -> dict[str, object] | None:
    additional_information = load_scenario_config_additional_information(input_path)
    if not isinstance(additional_information, dict):
        return None
    raw_environment = additional_information.get("environment")
    if isinstance(raw_environment, dict):
        return dict(raw_environment)
    return None


def load_scenario_config(
	input_path: Path,
) -> tuple[
	dict[str, list[Waypoint]],
	dict[str, VehicleDimensions],
	Path | None,
	list[DetectionGap],
]:
	with input_path.open(encoding="utf-8") as file:
		config = json.load(file)
	if not isinstance(config, dict):
		raise ValueError("Scenario config must be a JSON object.")
	raw_vehicles = config.get("vehicles")
	if not isinstance(raw_vehicles, list) or not raw_vehicles:
		raise ValueError("Scenario config contains no vehicles.")
	if len(raw_vehicles) > MAX_IMPORTED_ACTORS:
		raise ValueError(f"Scenario exceeds the limit of {MAX_IMPORTED_ACTORS} actors.")
	vehicles: dict[str, list[Waypoint]] = {}
	dimensions: dict[str, VehicleDimensions] = {}
	total_waypoints = 0
	for raw_vehicle in raw_vehicles:
		if not isinstance(raw_vehicle, dict):
			raise ValueError("Vehicle config must be an object.")
		name = safe_vehicle_name(str(raw_vehicle.get("name", f"vehicle_{len(vehicles) + 1}")))
		raw_waypoints = raw_vehicle.get("waypoints")
		if not isinstance(raw_waypoints, list):
			raise ValueError(f"{name}: missing waypoints list.")
		if len(raw_waypoints) > MAX_WAYPOINTS_PER_ACTOR:
			raise ValueError(
				f"{name}: exceeds the limit of {MAX_WAYPOINTS_PER_ACTOR} waypoints."
			)
		total_waypoints += len(raw_waypoints)
		if total_waypoints > MAX_TOTAL_WAYPOINTS:
			raise ValueError(
				f"Scenario exceeds the limit of {MAX_TOTAL_WAYPOINTS} waypoints."
			)
		vehicles[name] = sorted(
			[
				Waypoint(
					time_s=float(raw_waypoint["time_s"]),
					x_m=float(raw_waypoint["x_m"]),
					y_m=float(raw_waypoint["y_m"]),
					speed_mps=(
						float(raw_waypoint["speed_mps"])
						if raw_waypoint.get("speed_mps") is not None
						else None
					),
				)
				for raw_waypoint in raw_waypoints
			],
			key=lambda waypoint: waypoint.time_s,
		)
		loaded_waypoints = vehicles[name]
		if len(loaded_waypoints) == 1:
			loaded_waypoints[0].speed_mps = 0.0
		elif len(loaded_waypoints) >= 2:
			trajectory = Trajectory(waypoints=loaded_waypoints)
			fallback_speeds, _adjusted = Trajectory.anchored_waypoint_speeds(
				trajectory.segment_average_speeds(),
				trajectory.initial_speed_mps(),
			)
			for index, waypoint in enumerate(loaded_waypoints):
				if waypoint.speed_mps is None:
					waypoint.speed_mps = fallback_speeds[index]
				waypoint.speed_mps = max(0.0, float(waypoint.speed_mps))
			for index, (start, end) in enumerate(
				zip(loaded_waypoints, loaded_waypoints[1:])
			):
				if float(start.speed_mps) + float(end.speed_mps) <= 1e-9:
					duration = max(end.time_s - start.time_s, 0.001)
					distance = trajectory.profile_segment_curve_distance(index)
					end.speed_mps = max(2.0 * distance / duration, 0.001)
		raw_dimensions = raw_vehicle.get("dimensions", {})
		if not isinstance(raw_dimensions, dict):
			raw_dimensions = {}
		dimensions[name] = VehicleDimensions(
			length_m=float(raw_dimensions.get("length_m", 4.5)),
			width_m=float(raw_dimensions.get("width_m", 1.8)),
			height_m=float(raw_dimensions.get("height_m", 1.8)),
			actor_type=str(raw_dimensions.get("actor_type", raw_vehicle.get("actor_type", "vehicle"))),
			carla_blueprint=str(raw_dimensions.get("carla_blueprint", raw_vehicle.get("carla_blueprint", ""))),
			xosc_export_mode=str(raw_dimensions.get("xosc_export_mode", raw_vehicle.get("xosc_export_mode", "trajectory"))),
			parameter_declarations=str(
				raw_dimensions.get(
					"parameter_declarations",
					raw_vehicle.get("parameter_declarations", ""),
				),
			),
			controller_name=str(raw_dimensions.get("controller_name", raw_vehicle.get("controller_name", ""))),
			controller_xml=str(raw_dimensions.get("controller_xml", raw_vehicle.get("controller_xml", ""))),
			parked_yaw_rad=float(raw_dimensions.get("parked_yaw_rad", raw_vehicle.get("parked_yaw_rad", 0.0))),
		)
	gaps: list[DetectionGap] = []
	raw_gaps = config.get("detection_gaps", [])
	if isinstance(raw_gaps, list):
		for raw_gap in raw_gaps:
			if not isinstance(raw_gap, dict):
				continue
			vehicle_name = safe_vehicle_name(str(raw_gap.get("vehicle_name", "")))
			if vehicle_name in vehicles:
				gaps.append(DetectionGap(vehicle_name=vehicle_name, start_time_s=float(raw_gap.get("start_time_s", 0.0)), end_time_s=float(raw_gap.get("end_time_s", 0.0))))
	raw_map_path = config.get("map_path")
	map_path = None
	if raw_map_path:
		configured_map_path = Path(raw_map_path).expanduser()
		map_path = configured_map_path if configured_map_path.is_absolute() else (input_path.parent / configured_map_path).resolve(strict=False)
	return vehicles, dimensions, map_path, gaps
