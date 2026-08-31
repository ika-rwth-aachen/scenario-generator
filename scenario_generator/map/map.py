from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

OPENDRIVE_LANE_TYPES = frozenset(
    {
        "none", "driving", "stop", "shoulder", "biking", "sidewalk", "border", "restricted",
        "parking", "bidirectional", "median", "special1", "special2", "special3", "roadworks",
        "tram", "rail", "entry", "exit", "onramp", "offramp", "connectingramp", "bus", "taxi",
        "hov", "mwyentry", "mwyexit", "walking", "pedestrian",
        "shared", "sliplane",
    }
)


@dataclass
class LaneCrossSection:
    """One sampled OpenDRIVE lane cross-section used for rendering."""

    s_m: float
    x_m: float
    y_m: float
    heading_rad: float
    lane_offset_m: float
    lane_widths_m: dict[int, float] = field(default_factory=dict)
    lane_types: dict[int, str] = field(default_factory=dict)
    lane_speed_limits_mps: dict[int, float] = field(default_factory=dict)
    road_speed_limit_mps: float | None = None
    elevation_m: float = 0.0
    superelevation_rad: float = 0.0


def imported_lane_profile_runs(
    profiles: list[LaneCrossSection],
    lane_id: int,
) -> list[tuple[str, list[LaneCrossSection]]]:
    """Group visible lane samples into contiguous runs of the same OpenDRIVE type."""
    runs: list[tuple[str, list[LaneCrossSection]]] = []
    active_type: str | None = None
    active_profiles: list[LaneCrossSection] = []
    for first, second in zip(profiles, profiles[1:], strict=False):
        first_width = max(0.0, first.lane_widths_m.get(lane_id, 0.0))
        second_width = max(0.0, second.lane_widths_m.get(lane_id, 0.0))
        if first_width <= 1e-4 and second_width <= 1e-4:
            active_type = None
            active_profiles = []
            continue
        lane_type = first.lane_types.get(lane_id, second.lane_types.get(lane_id, "driving"))
        if active_type == lane_type and active_profiles and active_profiles[-1] is first:
            active_profiles.append(second)
            continue
        active_type = lane_type
        active_profiles = [first, second]
        runs.append((lane_type, active_profiles))
    return runs


@dataclass(frozen=True)
class CubicRoadProfile:
    """One OpenDRIVE cubic profile segment starting at ``s_m``."""

    s_m: float
    a: float
    b: float
    c: float
    d: float

    def value_at(self, road_s_m: float) -> float:
        local_s_m = road_s_m - self.s_m
        return self.a + local_s_m * (self.b + local_s_m * (self.c + local_s_m * self.d))


@dataclass(frozen=True)
class LaneSnapResult:
    """One projected point on a compatible OpenDRIVE lane centerline."""

    x_m: float
    y_m: float
    road_name: str
    lane_id: int
    lane_type: str
    s_m: float
    speed_limit_mps: float | None = None


@dataclass
class MapPolyline:
    """Road polyline with OpenDRIVE lane metadata."""

    name: str
    points: list[tuple[float, float]]
    kind: str = "reference"
    width_m: float = 0.0
    lane_count: int = 2
    lane_width_m: float = 3.0
    predecessor_road: str = ""
    successor_road: str = ""
    predecessor_lane_links: str = ""
    successor_lane_links: str = ""
    lane_widths_m: dict[int, float] = field(default_factory=dict)
    lane_types: dict[int, str] = field(default_factory=dict)
    lane_cross_sections: list[LaneCrossSection] = field(default_factory=list)
    elevation_profile: list[CubicRoadProfile] = field(default_factory=list)
    superelevation_profile: list[CubicRoadProfile] = field(default_factory=list)
    source_length_m: float = 0.0

    @staticmethod
    def profile_value_at(
        profile: list[CubicRoadProfile],
        road_s_m: float,
        fallback: float,
    ) -> float:
        active = None
        for entry in profile:
            if entry.s_m <= road_s_m + 1e-9:
                active = entry
            else:
                break
        return active.value_at(road_s_m) if active is not None else fallback

    def elevation_at_s(self, road_s_m: float, fallback: float = 0.0) -> float:
        return self.profile_value_at(self.elevation_profile, road_s_m, fallback)

    def superelevation_at_s(self, road_s_m: float, fallback: float = 0.0) -> float:
        return self.profile_value_at(self.superelevation_profile, road_s_m, fallback)

    def lane_type_for(self, lane_id: int) -> str:
        """Return the OpenDRIVE type for one lane, defaulting to driving."""
        lane_type = self.lane_types.get(lane_id, "driving").strip().lower() or "driving"
        return "sidewalk" if lane_type == "sideway" else lane_type

    def lane_type_spec(self) -> str:
        """Return editable lane types as ``lane_id:type`` pairs."""
        return "; ".join(
            f"{lane_id}:{self.lane_type_for(lane_id)}"
            for lane_id in self.opendrive_lane_ids()
        )

    def set_lane_type_spec(self, spec: str):
        """Parse editable ``lane_id:type`` pairs, defaulting omitted lanes to driving."""
        valid_ids = set(self.opendrive_lane_ids())
        parsed: dict[int, str] = {}
        for part in spec.replace(",", ";").split(";"):
            part = part.strip()
            if not part:
                continue
            if ":" not in part:
                raise ValueError(
                    "Lane types must use lane_id:type, e.g. -1:driving; 1:sidewalk.",
                )
            lane_text, lane_type = part.split(":", 1)
            lane_id = int(lane_text.strip())
            lane_type = lane_type.strip().lower()
            if lane_id not in valid_ids:
                raise ValueError(f"Lane {lane_id} does not exist on {self.name}.")
            if not lane_type:
                raise ValueError("Lane type must not be empty.")
            if lane_type == "sideway":
                lane_type = "sidewalk"
            if lane_type not in OPENDRIVE_LANE_TYPES:
                allowed_types = ", ".join(sorted(OPENDRIVE_LANE_TYPES))
                raise ValueError(
                    f"Unsupported OpenDRIVE lane type {lane_type!r}. Allowed types: {allowed_types}.",
                )
            parsed[lane_id] = lane_type
        self.lane_types = parsed

    def primary_lane_type(self) -> str:
        """Return a representative type for the road's base surface."""
        lane_types = [
            self.lane_type_for(lane_id) for lane_id in self.opendrive_lane_ids()
        ]
        for lane_type in (
            "driving",
            "biking",
            "sidewalk",
            "walking",
            "pedestrian",
            "parking",
            "restricted",
        ):
            if lane_type in lane_types:
                return lane_type
        return lane_types[0] if lane_types else "driving"

    def is_pedestrian_only(self) -> bool:
        """Return whether the road has pedestrian lanes but no vehicle-driving lane."""
        lane_types = {lane_type.lower() for lane_type in self.lane_types.values()}
        pedestrian_types = {"sidewalk", "walking", "pedestrian"}
        vehicle_types = {
            "driving",
            "entry",
            "exit",
            "onramp",
            "offramp",
            "connectingramp",
            "bidirectional",
        }
        return bool(lane_types & pedestrian_types) and not bool(
            lane_types & vehicle_types,
        )

    def lane_widths_by_id(self) -> dict[int, float]:
        lane_ids = self.opendrive_lane_ids()
        default_width = self.normalized_lane_width_m()
        return {
            lane_id: max(float(self.lane_widths_m.get(lane_id, default_width)), 0.1)
            for lane_id in lane_ids
        }

    def total_width_m(self) -> float:
        if self.lane_widths_m:
            return sum(self.lane_widths_by_id().values())
        if self.width_m > 0.0:
            return self.width_m
        return max(self.lane_count, 1) * max(self.lane_width_m, 0.1)

    def normalized_lane_count(self) -> int:
        return max(int(self.lane_count), 1)

    def normalized_lane_width_m(self) -> float:
        if self.lane_width_m > 0.0:
            return self.lane_width_m
        if self.width_m > 0.0:
            return self.width_m / self.normalized_lane_count()
        return 3.0

    def opendrive_lane_ids(self) -> list[int]:
        lane_count = self.normalized_lane_count()
        left_lane_count = lane_count // 2
        right_lane_count = lane_count - left_lane_count
        return list(range(1, left_lane_count + 1)) + list(
            range(-1, -right_lane_count - 1, -1),
        )

    def lane_width_for(self, lane_id: int) -> float:
        return self.lane_widths_by_id()[lane_id]

    def lane_width_spec(self) -> str:
        widths = self.lane_widths_by_id()
        if not self.lane_widths_m:
            return ""
        return "; ".join(
            f"{lane_id}:{widths[lane_id]:.3f}" for lane_id in self.opendrive_lane_ids()
        )

    def set_lane_width_spec(self, spec: str):
        spec = spec.strip()
        if not spec:
            self.lane_widths_m = {}
            return
        valid_ids = set(self.opendrive_lane_ids())
        parsed: dict[int, float] = {}
        for part in spec.replace(",", ";").split(";"):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                lane_text, width_text = part.split(":", 1)
            elif "=" in part:
                lane_text, width_text = part.split("=", 1)
            else:
                raise ValueError(
                    "Lane widths must use lane_id:width, e.g. -1:3.5; 1:3.2.",
                )
            lane_id = int(lane_text.strip())
            if lane_id not in valid_ids:
                raise ValueError(f"Lane {lane_id} does not exist on {self.name}.")
            width = float(width_text.strip())
            if width <= 0.0:
                raise ValueError("Lane widths must be greater than zero.")
            parsed[lane_id] = width
        self.lane_widths_m = parsed
        self.width_m = self.total_width_m()


@dataclass
class ScenarioMap:
    """Map container holding view-only and editable road state."""

    roads: list[MapPolyline] = field(default_factory=list)
    view_roads: list[MapPolyline] = field(default_factory=list)
    path: Path | None = None
    modified: bool = False
    edit_enabled: bool = False
    _elevation_cells: (
        dict[
            tuple[int, int],
            list[tuple[MapPolyline, LaneCrossSection, LaneCrossSection]],
        ]
        | None
    ) = field(default=None, init=False, repr=False)

    ELEVATION_GRID_SIZE_M = 20.0
    COMPATIBLE_LANE_TYPES = {
        "vehicle": frozenset(
            {
                "driving",
                "entry",
                "exit",
                "onramp",
                "offramp",
                "connectingramp",
                "sliplane",
                "bidirectional",
            }
        ),
        "cyclist": frozenset({"biking", "shared", "driving"}),
        "pedestrian": frozenset(
            {"walking", "sidewalk", "pedestrian", "shared"}
        ),
    }

    @staticmethod
    def clone_polylines(polylines: list[MapPolyline]) -> list[MapPolyline]:
        """Return detached copies so view-only and edit state can diverge safely."""
        return [
            MapPolyline(
                name=polyline.name,
                points=list(polyline.points),
                kind=polyline.kind,
                width_m=polyline.width_m,
                lane_count=polyline.lane_count,
                lane_width_m=polyline.lane_width_m,
                predecessor_road=polyline.predecessor_road,
                successor_road=polyline.successor_road,
                predecessor_lane_links=polyline.predecessor_lane_links,
                successor_lane_links=polyline.successor_lane_links,
                lane_widths_m=dict(polyline.lane_widths_m),
                lane_types=dict(polyline.lane_types),
                lane_cross_sections=[
                    LaneCrossSection(
                        s_m=section.s_m,
                        x_m=section.x_m,
                        y_m=section.y_m,
                        heading_rad=section.heading_rad,
                        lane_offset_m=section.lane_offset_m,
                        lane_widths_m=dict(section.lane_widths_m),
                        lane_types=dict(section.lane_types),
                        lane_speed_limits_mps=dict(
                            section.lane_speed_limits_mps
                        ),
                        road_speed_limit_mps=section.road_speed_limit_mps,
                        elevation_m=section.elevation_m,
                        superelevation_rad=section.superelevation_rad,
                    )
                    for section in polyline.lane_cross_sections
                ],
                elevation_profile=list(polyline.elevation_profile),
                superelevation_profile=list(polyline.superelevation_profile),
                source_length_m=polyline.source_length_m,
            )
            for polyline in polylines
        ]

    @property
    def display_roads(self) -> list[MapPolyline]:
        if self.edit_enabled or not self.view_roads:
            return self.roads
        return self.view_roads

    def has_any_roads(self) -> bool:
        return bool(self.roads or self.view_roads)

    @staticmethod
    def lane_center_offset(
        lane_widths_m: dict[int, float],
        lane_offset_m: float,
        lane_id: int,
    ) -> float:
        """Return the lateral center offset of one signed lane id."""
        if lane_id > 0:
            inner = lane_offset_m + sum(
                max(0.0, float(lane_widths_m.get(index, 0.0)))
                for index in range(1, lane_id)
            )
            return inner + max(
                0.0, float(lane_widths_m.get(lane_id, 0.0))
            ) / 2.0
        inner = lane_offset_m - sum(
            max(0.0, float(lane_widths_m.get(-index, 0.0)))
            for index in range(1, abs(lane_id))
        )
        return inner - max(
            0.0, float(lane_widths_m.get(lane_id, 0.0))
        ) / 2.0

    @staticmethod
    def project_point_to_segment(
        x_m: float,
        y_m: float,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> tuple[float, float, float, float]:
        """Return projected XY, fraction, and squared distance."""
        dx_m = end[0] - start[0]
        dy_m = end[1] - start[1]
        length_squared = dx_m * dx_m + dy_m * dy_m
        if length_squared <= 1e-12:
            fraction = 0.0
        else:
            fraction = min(
                1.0,
                max(
                    0.0,
                    (
                        (x_m - start[0]) * dx_m
                        + (y_m - start[1]) * dy_m
                    )
                    / length_squared,
                ),
            )
        projected_x = start[0] + fraction * dx_m
        projected_y = start[1] + fraction * dy_m
        distance_squared = (
            (projected_x - x_m) ** 2 + (projected_y - y_m) ** 2
        )
        return projected_x, projected_y, fraction, distance_squared

    def nearest_compatible_lane(
        self,
        x_m: float,
        y_m: float,
        actor_type: str,
        max_distance_m: float,
    ) -> LaneSnapResult | None:
        """Project a point to the nearest compatible lane centerline."""
        compatible_types = self.COMPATIBLE_LANE_TYPES.get(
            actor_type, self.COMPATIBLE_LANE_TYPES["vehicle"]
        )
        best: tuple[float, LaneSnapResult] | None = None
        for road in self.display_roads:
            if road.kind != "reference":
                continue
            if len(road.lane_cross_sections) >= 2:
                best = self._nearest_imported_lane(
                    road,
                    x_m,
                    y_m,
                    compatible_types,
                    best,
                )
            else:
                best = self._nearest_synthetic_lane(
                    road,
                    x_m,
                    y_m,
                    compatible_types,
                    best,
                )
        if best is None or best[0] > max_distance_m * max_distance_m:
            return None
        return best[1]

    def _nearest_imported_lane(
        self,
        road: MapPolyline,
        x_m: float,
        y_m: float,
        compatible_types: frozenset[str],
        best: tuple[float, LaneSnapResult] | None,
    ) -> tuple[float, LaneSnapResult] | None:
        """Return the closest compatible imported lane centerline on one road."""
        for start, end in zip(road.lane_cross_sections, road.lane_cross_sections[1:]):
            lane_ids = set(start.lane_widths_m) & set(end.lane_widths_m)
            for lane_id in lane_ids:
                lane_type = start.lane_types.get(
                    lane_id, road.lane_type_for(lane_id)
                ).lower()
                if lane_type not in compatible_types:
                    continue
                start_offset = self.lane_center_offset(
                    start.lane_widths_m, start.lane_offset_m, lane_id
                )
                end_offset = self.lane_center_offset(
                    end.lane_widths_m, end.lane_offset_m, lane_id
                )
                start_point = (
                    start.x_m - math.sin(start.heading_rad) * start_offset,
                    start.y_m + math.cos(start.heading_rad) * start_offset,
                )
                end_point = (
                    end.x_m - math.sin(end.heading_rad) * end_offset,
                    end.y_m + math.cos(end.heading_rad) * end_offset,
                )
                projected_x, projected_y, fraction, distance_squared = (
                    self.project_point_to_segment(
                        x_m, y_m, start_point, end_point
                    )
                )
                if best is not None and distance_squared >= best[0]:
                    continue
                source = end if fraction >= 1.0 - 1e-9 else start
                speed_limit = source.lane_speed_limits_mps.get(
                    lane_id, source.road_speed_limit_mps
                )
                result = LaneSnapResult(
                    x_m=projected_x,
                    y_m=projected_y,
                    road_name=road.name,
                    lane_id=lane_id,
                    lane_type=lane_type,
                    s_m=start.s_m + fraction * (end.s_m - start.s_m),
                    speed_limit_mps=speed_limit,
                )
                best = distance_squared, result
        return best

    def _nearest_synthetic_lane(
        self,
        road: MapPolyline,
        x_m: float,
        y_m: float,
        compatible_types: frozenset[str],
        best: tuple[float, LaneSnapResult] | None,
    ) -> tuple[float, LaneSnapResult] | None:
        """Return the closest compatible synthetic lane centerline on one road."""
        if len(road.points) < 2:
            return best
        widths = road.lane_widths_by_id()
        traversed = 0.0
        for start, end in zip(road.points, road.points[1:]):
            dx_m = end[0] - start[0]
            dy_m = end[1] - start[1]
            length = math.hypot(dx_m, dy_m)
            if length <= 1e-9:
                continue
            heading = math.atan2(dy_m, dx_m)
            for lane_id in road.opendrive_lane_ids():
                lane_type = road.lane_type_for(lane_id)
                if lane_type not in compatible_types:
                    continue
                offset = self.lane_center_offset(widths, 0.0, lane_id)
                start_point = (
                    start[0] - math.sin(heading) * offset,
                    start[1] + math.cos(heading) * offset,
                )
                end_point = (
                    end[0] - math.sin(heading) * offset,
                    end[1] + math.cos(heading) * offset,
                )
                projected_x, projected_y, fraction, distance_squared = (
                    self.project_point_to_segment(
                        x_m, y_m, start_point, end_point
                    )
                )
                if best is not None and distance_squared >= best[0]:
                    continue
                best = (
                    distance_squared,
                    LaneSnapResult(
                        x_m=projected_x,
                        y_m=projected_y,
                        road_name=road.name,
                        lane_id=lane_id,
                        lane_type=lane_type,
                        s_m=traversed + fraction * length,
                    ),
                )
            traversed += length
        return best

    def load_view_only(self, roads: list[MapPolyline], path: Path | None):
        self.view_roads = self.clone_polylines(roads)
        self.roads = []
        self.path = path
        self.modified = False
        self.edit_enabled = False
        self._elevation_cells = None

    def load_editable(self, roads: list[MapPolyline], path: Path | None):
        self.roads = self.clone_polylines(roads)
        self.view_roads = []
        self.path = path
        self.modified = False
        self.edit_enabled = True
        self._elevation_cells = None

    def enable_editing(self) -> bool:
        if self.edit_enabled:
            return False
        if self.view_roads:
            self.roads = self.clone_polylines(self.view_roads)
            self.edit_enabled = True
            self.modified = False
            self._elevation_cells = None
            return True
        return False

    def clear(self):
        self.roads = []
        self.view_roads = []
        self.path = None
        self.modified = False
        self.edit_enabled = False
        self._elevation_cells = None

    @staticmethod
    def section_lateral_bounds(section: LaneCrossSection) -> tuple[float, float]:
        """Return right and left road-surface offsets from the reference line."""
        left_width = sum(
            width for lane_id, width in section.lane_widths_m.items() if lane_id > 0
        )
        right_width = sum(
            width for lane_id, width in section.lane_widths_m.items() if lane_id < 0
        )
        return section.lane_offset_m - right_width, section.lane_offset_m + left_width

    def rebuild_elevation_index(self):
        """Build a spatial index over imported road-surface samples."""
        cells: dict[
            tuple[int, int],
            list[tuple[MapPolyline, LaneCrossSection, LaneCrossSection]],
        ] = {}
        for road in self.display_roads:
            for start, end in zip(
                road.lane_cross_sections,
                road.lane_cross_sections[1:],
            ):
                start_right, start_left = self.section_lateral_bounds(start)
                end_right, end_left = self.section_lateral_bounds(end)
                lateral_extent = max(
                    abs(start_right),
                    abs(start_left),
                    abs(end_right),
                    abs(end_left),
                )
                min_x = min(start.x_m, end.x_m) - lateral_extent
                max_x = max(start.x_m, end.x_m) + lateral_extent
                min_y = min(start.y_m, end.y_m) - lateral_extent
                max_y = max(start.y_m, end.y_m) + lateral_extent
                min_cell_x = math.floor(min_x / self.ELEVATION_GRID_SIZE_M)
                max_cell_x = math.floor(max_x / self.ELEVATION_GRID_SIZE_M)
                min_cell_y = math.floor(min_y / self.ELEVATION_GRID_SIZE_M)
                max_cell_y = math.floor(max_y / self.ELEVATION_GRID_SIZE_M)
                for cell_x in range(min_cell_x, max_cell_x + 1):
                    for cell_y in range(min_cell_y, max_cell_y + 1):
                        cells.setdefault((cell_x, cell_y), []).append(
                            (road, start, end),
                        )
        self._elevation_cells = cells

    def invalidate_elevation_index(self):
        """Discard cached road-surface segments after map edits."""
        self._elevation_cells = None

    def elevation_at(self, x_m: float, y_m: float) -> float | None:
        """Return the nearest road-surface elevation covering one XY position."""
        if self._elevation_cells is None:
            self.rebuild_elevation_index()
        cell = (
            math.floor(x_m / self.ELEVATION_GRID_SIZE_M),
            math.floor(y_m / self.ELEVATION_GRID_SIZE_M),
        )
        candidates = (
            self._elevation_cells.get(cell, []) if self._elevation_cells else []
        )
        best: tuple[float, float] | None = None
        for road, start, end in candidates:
            dx_m = end.x_m - start.x_m
            dy_m = end.y_m - start.y_m
            length_squared_m = dx_m * dx_m + dy_m * dy_m
            if length_squared_m <= 1e-12:
                continue
            fraction = (
                (x_m - start.x_m) * dx_m + (y_m - start.y_m) * dy_m
            ) / length_squared_m
            if fraction < 0.0 or fraction > 1.0:
                continue
            reference_x_m = start.x_m + fraction * dx_m
            reference_y_m = start.y_m + fraction * dy_m
            segment_length_m = math.sqrt(length_squared_m)
            lateral_offset_m = (
                dx_m * (y_m - reference_y_m) - dy_m * (x_m - reference_x_m)
            ) / segment_length_m
            start_right, start_left = self.section_lateral_bounds(start)
            end_right, end_left = self.section_lateral_bounds(end)
            right_bound = start_right + fraction * (end_right - start_right)
            left_bound = start_left + fraction * (end_left - start_left)
            if lateral_offset_m < right_bound or lateral_offset_m > left_bound:
                continue
            lane_offset_m = start.lane_offset_m + fraction * (
                end.lane_offset_m - start.lane_offset_m
            )
            distance_to_lane_center = abs(lateral_offset_m - lane_offset_m)
            road_s_m = start.s_m + fraction * (end.s_m - start.s_m)
            interpolated_elevation_m = start.elevation_m + fraction * (
                end.elevation_m - start.elevation_m
            )
            interpolated_superelevation_rad = start.superelevation_rad + fraction * (
                end.superelevation_rad - start.superelevation_rad
            )
            elevation_m = road.elevation_at_s(road_s_m, interpolated_elevation_m)
            superelevation_rad = road.superelevation_at_s(
                road_s_m,
                interpolated_superelevation_rad,
            )
            surface_elevation_m = elevation_m + lateral_offset_m * math.sin(
                superelevation_rad,
            )
            if best is None or distance_to_lane_center < best[0]:
                best = distance_to_lane_center, surface_elevation_m
        return best[1] if best is not None else None

    @property
    def reference_roads(self) -> list[MapPolyline]:
        return [road for road in self.roads if road.kind == "reference"]

    def road_by_name(self, name: str) -> MapPolyline | None:
        for road in self.roads:
            if road.name == name:
                return road
        return None

    def relation_lookup(self) -> dict[str, MapPolyline]:
        return {
            road.name or f"road_{index}": road
            for index, road in enumerate(self.reference_roads, start=1)
        }
