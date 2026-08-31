import math

import pytest

import scenario_generator.io.import_validation as validation
from scenario_generator.map.map import MapPolyline
from scenario_generator.scenario_elements.road_user import VehicleDimensions, Waypoint


def test_import_validation_rejects_non_finite_waypoint_values():
    vehicles = {
        "ego": [Waypoint(time_s=0.0, x_m=math.nan, y_m=0.0, speed_mps=0.0)]
    }

    with pytest.raises(ValueError, match="must be finite"):
        validation.validate_imported_scenario(
            vehicles, {"ego": VehicleDimensions()}
        )


def test_import_validation_rejects_excessive_total_waypoints(monkeypatch):
    monkeypatch.setattr(validation, "MAX_TOTAL_WAYPOINTS", 2)
    vehicles = {
        "ego": [
            Waypoint(time_s=0.0, x_m=0.0, y_m=0.0),
            Waypoint(time_s=1.0, x_m=1.0, y_m=0.0),
            Waypoint(time_s=2.0, x_m=2.0, y_m=0.0),
        ]
    }

    with pytest.raises(ValueError, match="limit of 2 waypoints"):
        validation.validate_imported_scenario(
            vehicles, {"ego": VehicleDimensions()}
        )


def test_import_validation_rejects_excessive_map_points(monkeypatch):
    monkeypatch.setattr(validation, "MAX_TOTAL_MAP_POINTS", 1)
    roads = [MapPolyline(name="road", points=[(0.0, 0.0), (1.0, 0.0)])]

    with pytest.raises(ValueError, match="limit of 1 points"):
        validation.validate_imported_map(roads)
