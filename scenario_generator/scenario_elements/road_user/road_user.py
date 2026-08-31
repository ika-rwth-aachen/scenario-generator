from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from scenario_generator.config.settings import load_actor_dimensions
from scenario_generator.geometry_utils import interpolate_angle, interpolate_series
from scenario_generator.scenario_elements.road_user.trajectory import Trajectory, Waypoint


@dataclass
class VehicleDimensions:
    """Physical road-user dimensions and per-actor export metadata."""

    length_m: float = 4.5
    width_m: float = 1.8
    height_m: float = 1.8
    actor_type: str = "vehicle"
    carla_blueprint: str = ""
    xosc_export_mode: str = "trajectory"
    parameter_declarations: str = ""
    controller_name: str = ""
    controller_xml: str = ""
    parked_yaw_rad: float = 0.0

    def as_dict(self) -> dict[str, float | str]:
        """Serialize dimensions and export metadata into config-compatible keys."""
        return {
            "length_m": self.length_m,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "actor_type": self.actor_type,
            "carla_blueprint": self.carla_blueprint,
            "xosc_export_mode": self.xosc_export_mode,
            "parameter_declarations": self.parameter_declarations,
            "controller_name": self.controller_name,
            "controller_xml": self.controller_xml,
        }


ACTOR_LABELS = {
    "Vehicle": "vehicle",
    "Cyclist": "cyclist",
    "Pedestrian": "pedestrian",
}
ACTOR_LABEL_BY_TYPE = {value: key for key, value in ACTOR_LABELS.items()}


def actor_default_dimensions(actor_type: str) -> VehicleDimensions:
    """Return configured default dimensions for one actor type."""
    dimensions = load_actor_dimensions(actor_type)
    return VehicleDimensions(
        length_m=float(dimensions["length_m"]),
        width_m=float(dimensions["width_m"]),
        height_m=float(dimensions["height_m"]),
        actor_type=actor_type,
    )


ACTOR_DEFAULTS = {
    actor_type: actor_default_dimensions(actor_type)
    for actor_type in ACTOR_LABEL_BY_TYPE
}


@dataclass(frozen=True)
class ActorState:
    """Current kinematic and geometric state of a road user.

    ``collision_radius_m`` describes the footprint's conservative bounding
    circle. Precise box-overlap metrics instead use ``length_m``, ``width_m``,
    and ``yaw_rad``.
    """

    name: str
    x_m: float
    y_m: float
    speed_mps: float
    yaw_rad: float
    collision_radius_m: float
    length_m: float = 2.0
    width_m: float = 2.0

    @property
    def vx_mps(self) -> float:
        """Longitudinal velocity projected onto the world x-axis."""
        return self.speed_mps * math.cos(self.yaw_rad)

    @property
    def vy_mps(self) -> float:
        """Longitudinal velocity projected onto the world y-axis."""
        return self.speed_mps * math.sin(self.yaw_rad)

    @property
    def longitudinal_axis(self) -> tuple[float, float]:
        """Unit vector pointing along the actor's heading."""
        return math.cos(self.yaw_rad), math.sin(self.yaw_rad)

    @property
    def lateral_axis(self) -> tuple[float, float]:
        """Unit vector perpendicular to the actor's heading."""
        return -math.sin(self.yaw_rad), math.cos(self.yaw_rad)


def collision_radius(dimensions: VehicleDimensions) -> float:
    """Return a conservative circular radius around the actor footprint."""
    return 0.5 * math.hypot(dimensions.length_m, dimensions.width_m)


def actor_state_from_trajectory(
    name: str,
    trajectory: dict[str, list[float]],
    time_s: float,
    dimensions: VehicleDimensions,
) -> ActorState:
    """Interpolate a trajectory into an :class:`ActorState` at ``time_s``."""
    time_values = [float(value) for value in trajectory["time_s"]]
    return ActorState(
        name=name,
        x_m=interpolate_series(
            time_values,
            [float(value) for value in trajectory["x_m"]],
            time_s,
        ),
        y_m=interpolate_series(
            time_values,
            [float(value) for value in trajectory["y_m"]],
            time_s,
        ),
        speed_mps=interpolate_series(
            time_values,
            [float(value) for value in trajectory["speed_mps"]],
            time_s,
        ),
        yaw_rad=interpolate_angle(
            time_values,
            [float(value) for value in trajectory["yaw_rad"]],
            time_s,
        ),
        collision_radius_m=collision_radius(dimensions),
        length_m=dimensions.length_m,
        width_m=dimensions.width_m,
    )


@dataclass
class RoadUser:
    """Named actor with trajectory and export dimensions."""

    name: str
    trajectory: Trajectory = field(default_factory=Trajectory)
    dimensions: VehicleDimensions = field(default_factory=VehicleDimensions)

    def __post_init__(self):
        self.name = self.safe_name(self.name)

    @staticmethod
    def safe_name(name: str) -> str:
        """Return a sanitized actor name that is safe for generated files/XML ids."""
        clean_name = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
        clean_name = clean_name.strip("_")
        if not clean_name:
            raise ValueError("Vehicle name must not be empty.")
        if clean_name[0].isdigit():
            clean_name = f"vehicle_{clean_name}"
        return clean_name

    @staticmethod
    def is_ego_name(name: str) -> bool:
        """Return whether a name should be treated as ego-like for exporters."""
        normalized_name = re.sub(r"[^A-Za-z0-9]+", "", name).lower()
        return "ego" in normalized_name

    @property
    def waypoints(self) -> list[Waypoint]:
        return self.trajectory.waypoints

    @waypoints.setter
    def waypoints(self, value: list[Waypoint]):
        self.trajectory.waypoints = value

    @property
    def detected(self) -> list[bool] | None:
        return self.trajectory.detected

    @detected.setter
    def detected(self, value: list[bool] | None):
        self.trajectory.detected = value

    def as_trajectory_series(self) -> dict[str, list[float] | list[bool]]:
        return self.trajectory.as_series()

    def state_at(self, time_s: float) -> ActorState:
        """Return this road user's interpolated state at ``time_s``."""
        return actor_state_from_trajectory(
            self.name,
            self.as_trajectory_series(),
            time_s,
            self.dimensions,
        )


def safe_vehicle_name(name: str) -> str:
    return RoadUser.safe_name(name)


def is_ego_vehicle_name(name: str) -> bool:
    return RoadUser.is_ego_name(name)
