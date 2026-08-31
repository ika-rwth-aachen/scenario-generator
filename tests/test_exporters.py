import json
import types

import pytest

from scenario_generator.config.settings import load_omega_prime_interpolation_hz
from scenario_generator.io.importer_exporter.omega_prime import OmegaPrimeAdapter
from scenario_generator.io.importer_exporter.openscenario import (
    OpenScenarioExporter,
    build_openscenario_xml,
    format_float,
)
from scenario_generator.io.importer_exporter.simple_scenario import SimpleScenarioAdapter
from scenario_generator.map.map import MapPolyline
from scenario_generator.scenario_elements.road_user.carla_blueprints import (
    blueprint_entries_for_actor_type,
    blueprint_label,
    load_carla_blueprint_catalog,
    normalize_entry,
)
from scenario_generator.scenario_elements.road_user.trajectory import Trajectory, Waypoint


def test_format_float_trims_trailing_zeroes():
    assert format_float(1.230000) == "1.23"
    assert format_float(5.0) == "5"


def test_openscenario_adds_actor_parameter_declarations_to_trajectory():
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
            "parameter_declarations": (
                '<ParameterDeclaration name="target_speed" '
                'parameterType="double" value="10" />'
            ),
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    assert root.find("./ParameterDeclarations/ParameterDeclaration") is None
    declaration = root.find(
        ".//Trajectory[@name='ego_trajectory']/ParameterDeclarations/ParameterDeclaration"
    )
    assert declaration is not None
    assert declaration.attrib == {
        "name": "target_speed",
        "parameterType": "double",
        "value": "10",
    }


def test_openscenario_adds_actor_parameter_declarations_to_route():
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
            "xosc_export_mode": "route",
            "parameter_declarations": (
                '<ParameterDeclaration name="route_speed" '
                'parameterType="double" value="10" />'
            ),
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    declaration = root.find(
        ".//Route[@name='ego_route']/ParameterDeclarations/ParameterDeclaration"
    )
    assert declaration is not None
    assert declaration.attrib["name"] == "route_speed"


def test_openscenario_actor_type_dimensions_and_blueprint_resolution():
    """Verify openscenario actor type dimensions and blueprint resolution."""
    exporter = OpenScenarioExporter(author="tester")
    trajectory = {
        "actor_type": "bike",
        "carla_blueprint": "vehicle.bh.crossbike",
        "dimensions": {"length": 2.0, "width": 0.8, "height": 1.9},
        "xosc_export_mode": "reach-position",
        "controller_name": "bike_controller",
    }

    assert exporter.actor_type_for_trajectory(trajectory) == "cyclist"
    assert exporter.carla_blueprint_for_trajectory(trajectory) == "vehicle.bh.crossbike"
    assert exporter.vehicle_dimensions(trajectory) == {
        "length_m": 2.0,
        "width_m": 0.8,
        "height_m": 1.9,
    }
    assert exporter.xosc_export_mode_for_trajectory(trajectory) == "reach_position"
    assert exporter.controller_name_for_trajectory(trajectory) == "bike_controller"
    assert (
        exporter.xosc_export_mode_for_trajectory(
            {"xosc_export_mode": "clear_trajectory"},
        )
        == "clear_trajectory"
    )
    assert (
        exporter.xosc_export_mode_for_trajectory({"xosc_export_mode": "route"})
        == "route"
    )


def test_openscenario_builds_cyclist_and_pedestrian_entities():
    """Verify openscenario builds cyclist and pedestrian entities."""
    trajectories = {
        "bike": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
            "actor_type": "cyclist",
            "controller_xml": '<Controller name="bike_ctrl"><Properties><Property name="mode" value="lane" /></Properties></Controller>',
        },
        "walker": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 0.0],
            "y_m": [0.0, 1.0],
            "yaw_rad": [1.57, 1.57],
            "speed_mps": [1.0, 1.0],
            "actor_type": "pedestrian",
            "xosc_export_mode": "reach_position",
            "controller_name": "walk_ctrl",
        },
    }

    root = build_openscenario_xml(trajectories, road_logic_file="map.xodr").getroot()

    assert root.find("./RoadNetwork/LogicFile").attrib["filepath"] == "map.xodr"
    bicycle = root.find(".//Vehicle[@vehicleCategory='bicycle']")
    assert bicycle is not None
    assert bicycle.attrib["name"] == "bike"
    assert bicycle.find("./BoundingBox/Center") is not None
    assert bicycle.find("./BoundingBox/Dimensions") is not None
    assert bicycle.find("./Performance") is not None
    assert bicycle.find("./Axles/FrontAxle") is not None
    assert bicycle.find("./Axles/RearAxle") is not None
    assert bicycle.find("./Performance").attrib == {
        "maxSpeed": "69.444",
        "maxAcceleration": "8",
        "maxDeceleration": "8",
    }
    assert bicycle.find("./Axles/FrontAxle").attrib["trackWidth"] == "0.6"
    assert bicycle.find("./Axles/RearAxle").attrib["wheelDiameter"] == "0.7"
    assert bicycle.find("./Properties") is not None
    pedestrian = root.find(".//Pedestrian")
    assert pedestrian is not None
    assert pedestrian.attrib["name"] == "walker"
    assert pedestrian.attrib["mass"] == "75"
    assert pedestrian.attrib["pedestrianCategory"] == "pedestrian"
    assert "model" not in pedestrian.attrib
    assert pedestrian.attrib["model3d"] == "pedestrian.adult"
    assert pedestrian.find("./BoundingBox/Center") is not None
    assert pedestrian.find("./BoundingBox/Dimensions") is not None
    assert pedestrian.find("./Properties") is not None
    assert root.find(".//ObjectController/Controller[@name='bike_ctrl']") is not None
    assert root.find(".//ObjectController/Controller[@name='walk_ctrl']") is not None
    assert root.find(".//AcquirePositionAction") is not None
    assert len(root.findall(".//Vertex")) == 2


def test_openscenario_marks_ego_like_vehicle_names_in_properties():
    """Verify openscenario marks ego like vehicle names in properties."""
    trajectories = {
        "ego_car": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
        },
        "vehicle_2": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    assert (
        root.find(
            ".//ScenarioObject[@name='ego_car']//Properties/Property[@name='type'][@value='ego_vehicle']",
        )
        is not None
    )
    assert (
        root.find(
            ".//ScenarioObject[@name='vehicle_2']//Properties/Property[@name='type'][@value='ego_vehicle']",
        )
        is None
    )


def test_openscenario_teleports_actors_present_at_time_zero():
    trajectories = {
        "initial_vehicle": {
            "time_s": [0.0, 1.0],
            "x_m": [12.5, 13.5],
            "y_m": [-3.0, -3.0],
            "z_m": [244.25, 244.5],
            "yaw_rad": [1.25, 1.25],
            "speed_mps": [1.0, 1.0],
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    world_position = root.find(
        "./Storyboard/Init/Actions/Private[@entityRef='initial_vehicle']"
        "/PrivateAction/TeleportAction/Position/WorldPosition",
    )
    assert world_position is not None
    assert world_position.attrib == {
        "x": "12.5",
        "y": "-3",
        "z": "244.25",
        "h": "0",
    }
    initial_speed = root.find(
        "./Storyboard/Init/Actions/Private[@entityRef='initial_vehicle']"
        "/PrivateAction/LongitudinalAction/SpeedAction/SpeedActionTarget/AbsoluteTargetSpeed",
    )
    assert initial_speed is not None
    assert initial_speed.attrib["value"] == "1"
    assert root.find(".//AddEntityAction") is None


def test_openscenario_applies_simulation_time_condition_factor_to_stop_triggers():
    """Verify an additional-info factor extends Act and Storyboard stop times."""
    trajectories = {
        "vehicle": {
            "time_s": [0.0, 2.0],
            "x_m": [0.0, 2.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
        },
    }

    root = build_openscenario_xml(
        trajectories,
        additional_scenario_information={"simulation_time_condition_factor": 1.5},
    ).getroot()

    assert (
        root.find(".//Act/StopTrigger//SimulationTimeCondition").attrib["value"] == "3"
    )
    assert (
        root.find("./Storyboard/StopTrigger//SimulationTimeCondition").attrib["value"]
        == "3"
    )


def test_openscenario_converts_gui_bounding_box_centers_to_actor_references():
    """Verify GUI center points use the OpenSCENARIO actor reference conventions."""
    trajectories = {
        "car": {
            "time_s": [0.0, 1.0],
            "x_m": [10.0, 10.0],
            "y_m": [5.0, 6.0],
            "z_m": [100.0, 100.0],
            "yaw_rad": [1.5707963267948966, 1.5707963267948966],
            "speed_mps": [1.0, 1.0],
            "coordinate_reference": "bounding_box_center",
            "dimensions": {"length_m": 4.0, "width_m": 2.0, "height_m": 1.6},
        },
        "walker": {
            "time_s": [0.0, 1.0],
            "x_m": [2.0, 2.0],
            "y_m": [3.0, 4.0],
            "z_m": [100.0, 100.0],
            "yaw_rad": [1.5707963267948966, 1.5707963267948966],
            "speed_mps": [1.0, 1.0],
            "actor_type": "pedestrian",
            "coordinate_reference": "bounding_box_center",
            "dimensions": {"length_m": 0.6, "width_m": 0.6, "height_m": 1.8},
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    car_position = root.find(
        "./Storyboard/Init/Actions/Private[@entityRef='car']"
        "/PrivateAction/TeleportAction/Position/WorldPosition",
    )
    walker_position = root.find(
        "./Storyboard/Init/Actions/Private[@entityRef='walker']"
        "/PrivateAction/TeleportAction/Position/WorldPosition",
    )
    assert car_position is not None
    assert car_position.attrib == {"x": "10", "y": "3", "z": "100", "h": "1.570796"}
    assert walker_position is not None
    assert walker_position.attrib == {"x": "2", "y": "3", "z": "100.9", "h": "1.570796"}

    car_center = root.find(".//ScenarioObject[@name='car']//BoundingBox/Center")
    walker_center = root.find(".//ScenarioObject[@name='walker']//BoundingBox/Center")
    assert car_center is not None
    assert car_center.attrib == {"x": "2", "y": "0", "z": "0.8"}
    assert walker_center is not None
    assert walker_center.attrib == {"x": "0", "y": "0", "z": "0"}


def test_openscenario_adds_late_actor_at_its_spawn_time():
    trajectories = {
        "late_vehicle": {
            "time_s": [2.5, 3.5],
            "x_m": [20.0, 21.0],
            "y_m": [4.0, 4.0],
            "z_m": [101.0, 102.0],
            "yaw_rad": [0.5, 0.5],
            "speed_mps": [1.0, 1.0],
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    assert (
        root.find("./Storyboard/Init/Actions/Private[@entityRef='late_vehicle']")
        is None
    )
    world_position = root.find(
        ".//EntityAction[@entityRef='late_vehicle']"
        "/AddEntityAction/Position/WorldPosition",
    )
    assert world_position is not None
    assert world_position.attrib == {
        "x": "20",
        "y": "4",
        "z": "101",
        "h": "0",
    }
    spawn_condition = root.find(
        ".//Event[@name='late_vehicle_spawn_event']"
        "/StartTrigger//SimulationTimeCondition",
    )
    assert spawn_condition is not None
    assert spawn_condition.attrib["value"] == "2.5"
    motion_condition = root.find(
        ".//Event[@name='late_vehicle_event']"
        "/StartTrigger//StoryboardElementStateCondition",
    )
    assert motion_condition is not None
    assert motion_condition.attrib["storyboardElementRef"] == "late_vehicle_spawn_event"
    assert motion_condition.attrib["state"] == "completeState"
    vertices = root.findall(".//Event[@name='late_vehicle_event']//Vertex")
    assert [
        vertex.find("./Position/WorldPosition").attrib["z"] for vertex in vertices
    ] == ["101", "102"]
    assert [
        vertex.find("./Position/WorldPosition").attrib["h"] for vertex in vertices
    ] == ["0", "0"]


def test_openscenario_spawns_late_static_actor_without_motion_action():
    trajectories = {
        "late_static": {
            "time_s": [3.0],
            "x_m": [7.0],
            "y_m": [8.0],
            "yaw_rad": [0.0],
            "speed_mps": [0.0],
            "xosc_export_mode": "clear_trajectory",
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    assert (
        root.find(".//Event[@name='late_static_spawn_event']//AddEntityAction")
        is not None
    )
    assert root.find(".//Event[@name='late_static_event']") is None


def test_openscenario_assigns_late_actor_controller_after_spawn():
    trajectories = {
        "late_controlled": {
            "time_s": [2.0, 3.0],
            "x_m": [1.0, 2.0],
            "y_m": [3.0, 3.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
            "controller_xml": (
                "<ControllerAction><AssignControllerAction>"
                '<Controller name="runtime_controller"><Properties />'
                "</Controller></AssignControllerAction></ControllerAction>"
            ),
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    assert root.find(".//Storyboard/Init//ControllerAction") is None
    controller_event = root.find(".//Event[@name='late_controlled_controller_event']")
    assert controller_event is not None
    assert (
        controller_event.find(
            ".//AssignControllerAction/Controller[@name='runtime_controller']",
        )
        is not None
    )
    condition = controller_event.find(".//StoryboardElementStateCondition")
    assert condition is not None
    assert condition.attrib["storyboardElementRef"] == "late_controlled_spawn_event"


def test_openscenario_headings_eliminate_quality_checker_sideslip_angle(tmp_path):
    from quality_checker.quality_checker import DEFAULT_SCHEMA_PATH, FileQualityChecker

    trajectory = Trajectory(
        [
            Waypoint(0.0, 0.0, 0.0),
            Waypoint(2.0, 5.0, 0.0),
            Waypoint(4.0, 5.0, 5.0),
            Waypoint(6.0, 10.0, 5.0),
        ],
    ).as_series()
    output_path = tmp_path / "curved.xosc"

    OpenScenarioExporter().export({"vehicle_2": trajectory}, output_path)
    checker = FileQualityChecker(output_path, DEFAULT_SCHEMA_PATH, None, False)

    assert checker.dynamic_errors[2] == []
    positions, times = checker.dynamic_data["vehicle_2"]
    dataframe = checker._calculate_acceleration_swimangle(
        checker._build_dynamic_data_df(positions, times),
    )
    assert float(dataframe.swimangle.abs().max()) < 1e-5


def test_openscenario_rejects_empty_trajectories():
    with pytest.raises(ValueError, match="At least one"):
        build_openscenario_xml({})


def test_openscenario_uses_requested_file_header_version():
    """Verify openscenario uses requested file header version."""
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
        },
    }

    root = build_openscenario_xml(
        trajectories,
        additional_scenario_information={"file_header": {"revMinor": 0}},
    ).getroot()

    file_header = root.find("./FileHeader")
    assert file_header is not None
    assert file_header.attrib["revMajor"] == "1"
    assert file_header.attrib["revMinor"] == "0"


def test_openscenario_rejects_unsupported_file_header_version():
    """Verify openscenario rejects unsupported file header version."""
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
        },
    }

    with pytest.raises(ValueError, match="Choose another version"):
        build_openscenario_xml(
            trajectories,
            additional_scenario_information={"file_header": {"revMinor": 2}},
        )


def test_openscenario_rejects_invalid_controller_template():
    """Verify openscenario rejects invalid controller template."""
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
            "controller_xml": "<Properties />",
        },
    }

    with pytest.raises(ValueError, match="Controller XML template"):
        build_openscenario_xml(trajectories)


def test_openscenario_accepts_controller_action_templates_in_init():
    """Verify openscenario accepts controller action templates in init."""
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
            "controller_xml": '<ControllerAction>\n  <AssignControllerAction>\n     <Controller name="EgoAEBController">\n        <Properties>\n          <Property name="module" value="aeb_implementation"/>\n          <Property name="initial_speed" value="7.6"/>\n          <Property name="lateral_aeb" value="true"/>\n        </Properties>\n     </Controller>\n  </AssignControllerAction>\n  <OverrideControllerValueAction>\n    <Throttle value="0" active="false"/>\n    <Brake value="0" active="false"/>\n    <Clutch value="0" active="false"/>\n    <ParkingBrake value="0" active="false"/>\n    <SteeringWheel value="0" active="false"/>\n    <Gear number="0" active="false"/>\n  </OverrideControllerValueAction>\n</ControllerAction>',
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    assert root.find(".//Entities//ObjectController") is None
    assert root.find(".//Storyboard/Init//ControllerAction") is not None
    assert (
        root.find(
            ".//Storyboard/Init//AssignControllerAction/Controller[@name='EgoAEBController']",
        )
        is not None
    )
    assert (
        root.find(
            ".//Storyboard/Init//OverrideControllerValueAction/Throttle[@active='false']",
        )
        is not None
    )


def test_openscenario_skips_motion_actions_for_clear_trajectory():
    """Verify openscenario skips motion actions for clear trajectory."""
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 1.0],
            "y_m": [0.0, 0.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
            "xosc_export_mode": "clear_trajectory",
            "controller_xml": '<ControllerAction><AssignControllerAction><Controller name="EgoAEBController"><Properties /></Controller></AssignControllerAction></ControllerAction>',
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    assert root.find(".//Storyboard/Story") is None
    assert root.find(".//Storyboard//ManeuverGroup") is None
    assert root.find(".//Storyboard/Init//ControllerAction") is not None


def test_openscenario_exports_route_waypoints_as_assign_route_action():
    """Verify Route mode emits all trajectory points as OpenSCENARIO route waypoints."""
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0, 2.0],
            "x_m": [1.0, 5.0, 8.0],
            "y_m": [2.0, 2.0, 4.0],
            "z_m": [10.0, 11.0, 12.0],
            "yaw_rad": [0.0, 0.0, 0.5],
            "speed_mps": [4.0, 4.0, 4.0],
            "xosc_export_mode": "route",
            "route_waypoints": [
                {"time_s": 0.0, "x_m": 1.0, "y_m": 2.0, "z_m": 10.0},
                {"time_s": 2.0, "x_m": 8.0, "y_m": 4.0, "z_m": 12.0},
            ],
        },
    }

    root = build_openscenario_xml(trajectories).getroot()

    route = root.find(".//AssignRouteAction/Route[@name='ego_route']")
    assert route is not None
    assert route.attrib["closed"] == "false"
    waypoints = route.findall("./Waypoint")
    assert len(waypoints) == 2
    assert [waypoint.attrib["routeStrategy"] for waypoint in waypoints] == [
        "fastest",
    ] * 2
    positions = [waypoint.find("./Position/WorldPosition") for waypoint in waypoints]
    assert [position.attrib["x"] for position in positions] == ["1", "8"]
    assert [position.attrib["y"] for position in positions] == ["2", "4"]
    assert [position.attrib["z"] for position in positions] == ["10", "12"]
    assert root.find(".//FollowTrajectoryAction") is None


def test_omega_prime_interpolation_hz_config(tmp_path):
    config_path = tmp_path / "config.yml"

    assert load_omega_prime_interpolation_hz(config_path) == 10

    config_path.write_text("omega_prime:\n  interpolation_hz: 25\n", encoding="utf-8")

    assert load_omega_prime_interpolation_hz(config_path) == 25


def test_carla_blueprint_catalog_helpers(tmp_path):
    """Verify carla blueprint catalog helpers.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "blueprints.json"
    path.write_text(
        json.dumps(
            {
                "vehicles": [
                    {
                        "id": "vehicle.foo",
                        "label": "Foo",
                        "category": "car",
                        "dimensions": {
                            "length_m": 4.8,
                            "width_m": 1.9,
                            "height_m": 1.5,
                        },
                    },
                ],
                "cyclists": [{"id": "vehicle.bike"}],
                "pedestrians": [{"id": "walker.pedestrian.0001", "label": "Adult"}],
            },
        ),
        encoding="utf-8",
    )

    catalog = load_carla_blueprint_catalog(path)

    assert (
        blueprint_entries_for_actor_type("vehicle", catalog)[0]["id"] == "vehicle.foo"
    )
    assert (
        blueprint_entries_for_actor_type("cyclist", catalog)[0]["label"]
        == "vehicle.bike"
    )
    assert (
        blueprint_entries_for_actor_type("pedestrian", catalog)[0]["id"]
        == "walker.pedestrian.0001"
    )
    assert catalog["vehicles"][0]["dimensions"] == {
        "length_m": 4.8,
        "width_m": 1.9,
        "height_m": 1.5,
    }
    assert (
        blueprint_label(catalog["vehicles"][0])
        == "Foo (vehicle.foo) [car] - 4.80 x 1.90 x 1.50 m"
    )
    assert "dimensions" not in normalize_entry(
        {"id": "vehicle.bar", "dimensions": {"length_m": 4.8}},
    )
    with pytest.raises(ValueError, match="missing id"):
        normalize_entry({"label": "No id"})


def test_bundled_carla_blueprints_match_ue5_catalogues():
    catalog = load_carla_blueprint_catalog()

    vehicle_ids = [entry["id"] for entry in catalog["vehicles"]]
    assert vehicle_ids == [
        "vehicle.ue4.audi.tt",
        "vehicle.ue4.bmw.grantourer",
        "vehicle.ue4.chevrolet.impala",
        "vehicle.dodge.charger",
        "vehicle.dodgecop.charger",
        "vehicle.taxi.ford",
        "vehicle.ue4.ford.mustang",
        "vehicle.lincoln.mkz",
        "vehicle.ue4.mercedes.ccc",
        "vehicle.mini.cooper",
        "vehicle.nissan.patrol",
        "vehicle.carlacola.actors",
        "vehicle.firetruck.actors",
        "vehicle.ambulance.ford",
        "vehicle.sprinter.mercedes",
        "vehicle.fuso.mitsubishi",
        "vehicle.miningtruck.miningtruck",
    ]

    pedestrian_ids = [entry["id"] for entry in catalog["pedestrians"]]
    assert len(pedestrian_ids) == len(set(pedestrian_ids)) == 37
    assert set(pedestrian_ids) == {
        f"walker.pedestrian.{index:04d}" for index in range(15, 52)
    }
    assert catalog["cyclists"] == []


def test_simple_scenario_adapter_imports_ego_and_vehicle():
    """Verify simple scenario adapter imports ego and vehicle."""
    adapter = SimpleScenarioAdapter(dt_s=0.5)
    config = {
        "duration": 1.0,
        "dt": 0.5,
        "metadata": {"actor_names": ["ego car", "bike 1"]},
        "road": {
            "lane_width": 4.0,
            "x0": 1.0,
            "y0": 2.0,
            "segments": [{"heading": 0.0}],
        },
        "ego_configuration": {
            "start_lanelet_id": 1000,
            "start_s": 0.0,
            "v0": 2.0,
            "vehicle_type_name": "small",
        },
        "vehicles": [
            {
                "vehicle_id": 7,
                "start_lanelet_id": 1001,
                "start_s": 5.0,
                "v0": 1.0,
                "lc_direction": -1,
                "lc_duration": 1.0,
                "vehicle_type_name": "bicycle",
            },
        ],
    }

    vehicles, dimensions = adapter.from_config(config)

    assert sorted(vehicles) == ["bike_1", "ego_car"]
    assert vehicles["ego_car"][-1].x_m == pytest.approx(3.0)
    assert vehicles["bike_1"][0].y_m == pytest.approx(6.0)
    assert vehicles["bike_1"][-1].y_m == pytest.approx(2.0)
    assert dimensions["ego_car"].length_m == 4.0
    assert dimensions["bike_1"].actor_type == "cyclist"


def test_simple_scenario_adapter_validation_errors():
    """Verify simple scenario adapter validation errors."""
    adapter = SimpleScenarioAdapter()
    with pytest.raises(ValueError, match="road and ego_configuration"):
        adapter.from_config({})
    with pytest.raises(ValueError, match="duration and dt"):
        adapter.from_config({"road": {}, "ego_configuration": {}, "duration": 0})
    with pytest.raises(ValueError, match="vehicles must be a list"):
        adapter.from_config({"road": {}, "ego_configuration": {}, "vehicles": {}})
    with pytest.raises(ValueError, match="100000 waypoints per actor"):
        adapter.from_config(
            {
                "road": {},
                "ego_configuration": {},
                "duration": 100_000,
                "dt": 0.5,
            }
        )
    with pytest.raises(ValueError, match="duration must be finite"):
        adapter.from_config(
            {"road": {}, "ego_configuration": {}, "duration": float("nan")}
        )


def test_omega_prime_validates_suffixes_before_importing_dependencies(tmp_path):
    """Verify omega prime validates suffixes before importing dependencies.

    Args:
        tmp_path: tmp path used by this operation.

    """
    adapter = OmegaPrimeAdapter()
    with pytest.raises(ValueError, match="must use a .mcap"):
        adapter.export_file(tmp_path / "bad.json", {}, [])
    with pytest.raises(ValueError, match="expects a .mcap"):
        adapter.import_file(tmp_path / "bad.json")


def test_omega_prime_series_helpers_and_classification():
    """Verify omega prime series helpers and classification."""
    adapter = OmegaPrimeAdapter()
    trajectory = {"time_s": [0, 1], "x_m": [0, 1]}

    assert adapter.required_series(trajectory, "time_s") == [0.0, 1.0]
    assert adapter.series_or_default(trajectory, "yaw_rad", 2, 0.5) == [0.5, 0.5]
    with pytest.raises(ValueError, match="missing y_m"):
        adapter.required_series(trajectory, "y_m")
    with pytest.raises(ValueError, match="same length"):
        adapter.validate_series_lengths("ego", [1.0], [1.0, 2.0])
    with pytest.raises(ValueError, match="trajectory is empty"):
        adapter.validate_series_lengths("ego", [], [])
    assert adapter.acceleration_at(1, [0.0, 2.0], [1.0, 5.0]) == pytest.approx(2.0)
    assert adapter.acceleration_at(0, [0.0], [1.0]) == 0.0

    betterosi = types.SimpleNamespace(
        MovingObjectType=types.SimpleNamespace(TYPE_PEDESTRIAN=1, TYPE_VEHICLE=2),
        MovingObjectVehicleClassificationRole=types.SimpleNamespace(ROLE_OTHER=3),
        MovingObjectVehicleClassificationType=types.SimpleNamespace(
            TYPE_BICYCLE=4,
            TYPE_SMALL_CAR=5,
        ),
    )
    assert adapter.osi_classification("pedestrian", betterosi) == (1, -1, -1)
    assert adapter.osi_classification("cyclist", betterosi) == (2, 3, 4)
    assert adapter.osi_classification("vehicle", betterosi) == (2, 3, 5)
    assert adapter.actor_type_from_osi(1, betterosi) == "pedestrian"
    assert adapter.actor_type_from_osi(2, betterosi) == "vehicle"


def test_omega_prime_exports_trajectory_height_and_vertical_motion():
    adapter = OmegaPrimeAdapter()
    betterosi = types.SimpleNamespace(
        MovingObjectType=types.SimpleNamespace(TYPE_PEDESTRIAN=1, TYPE_VEHICLE=2),
        MovingObjectVehicleClassificationRole=types.SimpleNamespace(ROLE_OTHER=3),
        MovingObjectVehicleClassificationType=types.SimpleNamespace(
            TYPE_BICYCLE=4,
            TYPE_SMALL_CAR=5,
        ),
    )
    trajectories = {
        "ego": {
            "time_s": [0.0, 2.0],
            "x_m": [0.0, 2.0],
            "y_m": [0.0, 0.0],
            "z_m": [100.0, 102.0],
            "yaw_rad": [0.0, 0.0],
            "speed_mps": [1.0, 1.0],
        },
    }

    rows = adapter.trajectories_to_dataframe(trajectories, betterosi).to_dicts()

    assert [row["z"] for row in rows] == [100.0, 102.0]
    assert [row["vel_z"] for row in rows] == [1.0, 1.0]
    assert [row["acc_z"] for row in rows] == [0.0, 0.0]


def test_omega_prime_extracts_map_polylines_from_recording():
    """Verify omega prime extracts map polylines from recording."""
    adapter = OmegaPrimeAdapter()
    lane = types.SimpleNamespace(
        centerline=types.SimpleNamespace(coords=[(0, 0), (1, 2)]),
    )
    recording = types.SimpleNamespace(
        map=types.SimpleNamespace(
            lanes={"lane_1": lane, "empty": types.SimpleNamespace(centerline=None)},
        ),
    )

    assert adapter.map_polylines_from_recording(recording) == [
        MapPolyline("lane_1", [(0.0, 0.0), (1.0, 2.0)]),
    ]


def test_omega_prime_resolves_host_vehicle_from_ego_trajectory():
    """Verify omega prime picks the ego-like trajectory as the host vehicle."""
    adapter = OmegaPrimeAdapter()
    trajectories = {
        "vehicle_1": {"time_s": [0.0], "x_m": [0.0], "y_m": [0.0]},
        "ego_car": {"time_s": [0.0], "x_m": [1.0], "y_m": [1.0]},
    }

    assert adapter.host_vehicle_idx_for_trajectories(trajectories) == 2
