from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from scenario_generator.map.map import ScenarioMap
from scenario_generator.scenario_elements.road_user.detection_gap import DetectionGap
from scenario_generator.scenario_elements.road_user.road_user import (
    RoadUser,
    VehicleDimensions,
)
from scenario_generator.scenario_elements.road_user.trajectory import Trajectory, Waypoint


@dataclass(init=False)
class ScenarioProject:
    """Scenario container: road users, map reference and perception gaps."""

    road_users: dict[str, RoadUser] = field(default_factory=dict)
    map: ScenarioMap = field(default_factory=ScenarioMap)
    detection_gaps: list[DetectionGap] = field(default_factory=list)

    def __init__(
        self,
        road_users: dict[str, RoadUser] | None = None,
        vehicles: dict[str, list[Waypoint]] | None = None,
        dimensions: dict[str, VehicleDimensions] | None = None,
        map_path: Path | None = None,
        map: ScenarioMap | None = None,
        detection_gaps: list[DetectionGap] | None = None,
    ):
        self.road_users = {}
        self.map = map or ScenarioMap(path=map_path)
        if map_path is not None:
            self.map.path = map_path
        self.detection_gaps = list(detection_gaps or [])
        if road_users:
            self.road_users = {
                road_user.name: road_user for road_user in road_users.values()
            }
        if vehicles is not None:
            self.vehicles = vehicles
        if dimensions is not None:
            self.dimensions = dimensions

    @property
    def map_path(self) -> Path | None:
        """Compatibility view for the OpenDRIVE source path."""
        return self.map.path

    @map_path.setter
    def map_path(self, value: Path | None):
        self.map.path = value

    @property
    def roads(self):
        """Compatibility view of editable/imported map roads."""
        return self.map.roads

    @roads.setter
    def roads(self, value):
        self.map.roads = value

    @property
    def vehicles(self) -> dict[str, list[Waypoint]]:
        """Compatibility view of road-user waypoints keyed by actor name."""
        return {
            name: road_user.waypoints for name, road_user in self.road_users.items()
        }

    @vehicles.setter
    def vehicles(self, vehicles: dict[str, list[Waypoint]]):
        existing_dimensions = self.dimensions
        self.road_users = {
            RoadUser.safe_name(name): RoadUser(
                name,
                trajectory=Trajectory(waypoints=waypoints),
                dimensions=existing_dimensions.get(
                    RoadUser.safe_name(name),
                    VehicleDimensions(),
                ),
            )
            for name, waypoints in vehicles.items()
        }
        self._filter_detection_gaps()

    @property
    def dimensions(self) -> dict[str, VehicleDimensions]:
        """Compatibility view of road-user dimensions keyed by actor name."""
        return {
            name: road_user.dimensions for name, road_user in self.road_users.items()
        }

    @dimensions.setter
    def dimensions(self, dimensions: dict[str, VehicleDimensions]):
        for raw_name, vehicle_dimensions in dimensions.items():
            name = RoadUser.safe_name(raw_name)
            if name not in self.road_users:
                self.road_users[name] = RoadUser(name=name)
            self.road_users[name].dimensions = vehicle_dimensions

    def road_user_for(self, vehicle_name: str) -> RoadUser:
        name = RoadUser.safe_name(vehicle_name)
        if name not in self.road_users:
            self.road_users[name] = RoadUser(name=name)
        return self.road_users[name]

    def dimensions_for(self, vehicle_name: str) -> VehicleDimensions:
        return self.road_user_for(vehicle_name).dimensions

    def gaps_for(self, vehicle_name: str) -> list[DetectionGap]:
        name = RoadUser.safe_name(vehicle_name)
        return [gap for gap in self.detection_gaps if gap.vehicle_name == name]

    def replace_vehicle_names(self, vehicles: dict[str, list[Waypoint]]):
        existing_dimensions = self.dimensions
        self.road_users = {}
        for raw_name, waypoints in vehicles.items():
            name = RoadUser.safe_name(raw_name)
            self.road_users[name] = RoadUser(
                name=name,
                trajectory=Trajectory(waypoints=waypoints),
                dimensions=existing_dimensions.get(name, VehicleDimensions()),
            )
        self._filter_detection_gaps()

    def _filter_detection_gaps(self):
        valid_names = set(self.road_users)
        self.detection_gaps = [
            gap for gap in self.detection_gaps if gap.vehicle_name in valid_names
        ]
