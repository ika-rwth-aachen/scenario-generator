import pytest

from scenario_generator.config.settings import (
    load_actor_dimensions,
    load_actor_xosc_defaults,
    load_profile_distance_samples_per_segment,
)
from scenario_generator.scenario_elements.road_user import (
    ACTOR_DEFAULTS,
    DetectionGap,
    VehicleDimensions,
    Waypoint,
    is_ego_vehicle_name,
    safe_vehicle_name,
)
from scenario_generator.scenario_elements.scenario import ScenarioProject


def test_safe_vehicle_name_sanitizes_and_prefixes_digits():
    assert safe_vehicle_name("  12 car name! ") == "vehicle_12_car_name"
    assert safe_vehicle_name("ego.vehicle") == "ego_vehicle"


@pytest.mark.parametrize("raw_name", ["ego", "ego_vehicle", "MyEgoCar", "not_ego"])
def test_is_ego_vehicle_name_matches_ego_like_names(raw_name):
    assert is_ego_vehicle_name(raw_name) is ("ego" in raw_name.lower().replace("_", ""))


@pytest.mark.parametrize("raw_name", ["", "   ", "---"])
def test_safe_vehicle_name_rejects_empty_names(raw_name):
    with pytest.raises(ValueError, match="must not be empty"):
        safe_vehicle_name(raw_name)


def test_actor_defaults_are_loaded_from_config():
    configured = load_actor_dimensions("cyclist")

    assert ACTOR_DEFAULTS["cyclist"].length_m == configured["length_m"]
    assert ACTOR_DEFAULTS["cyclist"].width_m == configured["width_m"]
    assert ACTOR_DEFAULTS["cyclist"].height_m == configured["height_m"]
    assert ACTOR_DEFAULTS["cyclist"].actor_type == "cyclist"


def test_actor_xosc_defaults_are_loaded_from_config():
    defaults = load_actor_xosc_defaults("vehicle")

    assert defaults["attributes"]["vehicleCategory"] == "car"
    assert defaults["performance"]["maxSpeed"] == "69.444"
    assert defaults["front_axle"]["positionX"] == "2.8"
    assert defaults["rear_axle"]["maxSteering"] == "0"


def test_profile_distance_samples_per_segment_is_configurable(tmp_path):
    config_path = tmp_path / "config.yml"
    assert load_profile_distance_samples_per_segment(config_path) == 64
    config_path.write_text(
        "PROFILE_DISTANCE_SAMPLES_PER_SEGMENT: 12\n",
        encoding="utf-8",
    )
    assert load_profile_distance_samples_per_segment(config_path) == 12


def test_vehicle_dimensions_as_dict_includes_actor_and_blueprint():
    """Verify vehicle dimensions as dict includes actor and blueprint."""
    dimensions = VehicleDimensions(
        length_m=4.0,
        width_m=1.7,
        height_m=1.5,
        actor_type="cyclist",
        carla_blueprint="vehicle.bh.crossbike",
        xosc_export_mode="reach_position",
        parameter_declarations='<ParameterDeclaration name="speed" parameterType="double" value="10" />',
        controller_name="lane_follower",
        controller_xml='<Controller name="lane_follower"><Properties /></Controller>',
    )
    assert dimensions.as_dict() == {
        "length_m": 4.0,
        "width_m": 1.7,
        "height_m": 1.5,
        "actor_type": "cyclist",
        "carla_blueprint": "vehicle.bh.crossbike",
        "xosc_export_mode": "reach_position",
        "parameter_declarations": '<ParameterDeclaration name="speed" parameterType="double" value="10" />',
        "controller_name": "lane_follower",
        "controller_xml": '<Controller name="lane_follower"><Properties /></Controller>',
    }


def test_detection_gap_contains_bounds():
    """Verify detection gap contains bounds."""
    gap = DetectionGap("ego", 1.0, 2.0)
    assert not gap.contains(0.99)
    assert gap.contains(1.0)
    assert gap.contains(2.0)
    assert not gap.contains(2.01)


def test_scenario_project_dimensions_and_gap_filtering():
    """Verify scenario project dimensions and gap filtering."""
    project = ScenarioProject(
        dimensions={"ego": VehicleDimensions(length_m=5.0)},
        detection_gaps=[DetectionGap("ego", 0.0, 1.0), DetectionGap("stale", 2.0, 3.0)],
    )

    project.replace_vehicle_names(
        {"ego": [Waypoint(0.0, 0.0, 0.0)], "new": [Waypoint(0.0, 1.0, 0.0)]},
    )

    assert project.dimensions_for("ego").length_m == 5.0
    assert project.dimensions_for("new").actor_type == "vehicle"
    assert project.gaps_for("ego") == [DetectionGap("ego", 0.0, 1.0)]
    assert project.gaps_for("stale") == []
