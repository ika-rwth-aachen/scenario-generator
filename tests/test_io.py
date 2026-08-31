import itertools
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import scenario_generator.io.scenario_files as scenario_files
from scenario_generator.config.settings import (
    DEFAULT_IMPORT_LIMITS,
    load_import_limits,
    load_map_export_max_lateral_deviation_m,
)
from scenario_generator.io.importer_exporter import (
    SimpleScenarioAdapter,
    importer_registry,
)
from scenario_generator.io.importer_exporter.openscenario import write_openscenario
from scenario_generator.io.scenario_files import (
    _point_to_segment_distance_m,
    load_openscenario_map_path,
    load_openscenario_map_reference,
    load_openscenario_xosc,
    load_scenario_config,
    load_trajectory_json,
    load_trajectory_json_dimensions,
    load_xodr_map,
    load_xodr_reference_map,
    parse_float,
    parse_waypoint_rows,
    road_plan_view_samples,
    sample_xodr_geometry_samples,
    sampled_map_points,
    waypoints_from_trajectory,
    waypoints_to_text,
    write_scenario_config,
    write_trajectory_json,
    write_xodr_map,
)
from scenario_generator.map.map import MapPolyline, ScenarioMap
from scenario_generator.scenario_elements.road_user import (
    DetectionGap,
    VehicleDimensions,
    Waypoint,
)


def test_importer_registry_contains_simple_scenario_adapter():
    assert isinstance(
        importer_registry.importer_for(Path("scenario.json")),
        SimpleScenarioAdapter,
    )
    assert importer_registry.importer_for(Path("scenario.csv")) is None


def test_xodr_geometry_sampling_rejects_excessive_output(monkeypatch):
    geometry = ET.fromstring(
        '<geometry x="0" y="0" hdg="0" length="2" s="0"><line /></geometry>'
    )
    monkeypatch.setattr(scenario_files, "MAX_POINTS_PER_ROAD", 2)

    with pytest.raises(ValueError, match="limit of 2 samples"):
        sample_xodr_geometry_samples(geometry)


def test_xodr_loader_rejects_xml_entities(tmp_path):
    path = tmp_path / "entity.xodr"
    path.write_text(
        '<!DOCTYPE OpenDRIVE [<!ENTITY injected "road">]>'
        '<OpenDRIVE><road name="&injected;" /></OpenDRIVE>',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="EntitiesForbidden"):
        load_xodr_reference_map(path)


def test_parse_waypoint_rows_accepts_headers_comments_and_extra_columns():
    """Verify parse waypoint rows accepts headers comments and extra columns."""
    rows = parse_waypoint_rows(
        """
        # comment
        time_s,x_m,y_m,speed_mps,yaw_rad
        1,10,20,3,0
        0,0,0
        """,
    )

    assert rows == [
        Waypoint(0.0, 0.0, 0.0),
        Waypoint(1.0, 10.0, 20.0, speed_mps=3.0),
    ]
    assert waypoints_to_text(rows).splitlines()[0] == "time_s,x_m,y_m,speed_mps"


def test_parse_float_and_waypoint_validation_errors():
    """Verify parse float and waypoint validation errors."""
    with pytest.raises(ValueError, match="Invalid time_s"):
        parse_float("abc", "time_s")
    with pytest.raises(ValueError, match="expected time_s"):
        parse_waypoint_rows("1,2")
    with pytest.raises(ValueError, match="missing x_m"):
        waypoints_from_trajectory({"time_s": [0.0], "y_m": [0.0]})
    with pytest.raises(ValueError, match="same length"):
        waypoints_from_trajectory({"time_s": [0.0], "x_m": [0.0, 1.0], "y_m": [0.0]})


def test_trajectory_json_roundtrip_and_dimensions(tmp_path):
    """Verify trajectory json roundtrip and dimensions.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "trajectories.json"
    trajectories = {
        "ego car": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 2.0],
            "y_m": [1.0, 1.0],
            "dimensions": {
                "length_m": 4.2,
                "width_m": 1.9,
                "height_m": 1.6,
                "actor_type": "vehicle",
                "carla_blueprint": "vehicle.test",
                "xosc_export_mode": "reach_position",
                "parameter_declarations": '<ParameterDeclaration name="speed" parameterType="double" value="10" />',
                "controller_name": "controller_a",
                "controller_xml": '<Controller name="controller_a"><Properties /></Controller>',
            },
        },
    }

    write_trajectory_json(trajectories, path)

    assert load_trajectory_json(path) == {
        "ego_car": [Waypoint(0.0, 0.0, 1.0), Waypoint(1.0, 2.0, 1.0)],
    }
    dimensions = load_trajectory_json_dimensions(path)
    assert dimensions["ego_car"].length_m == 4.2
    assert dimensions["ego_car"].carla_blueprint == "vehicle.test"
    assert dimensions["ego_car"].xosc_export_mode == "reach_position"
    assert "ParameterDeclaration" in dimensions["ego_car"].parameter_declarations
    assert dimensions["ego_car"].controller_name == "controller_a"


def test_load_trajectory_json_rejects_invalid_top_level(tmp_path):
    """Verify load trajectory json rejects invalid top level.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "invalid.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="vehicle-object mapping"):
        load_trajectory_json(path)


def test_scenario_config_roundtrip_preserves_dimensions_map_and_gaps(tmp_path):
    """Verify scenario config roundtrip preserves dimensions map and gaps.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "scenario_config.json"
    vehicles = {
        "ego": [
            Waypoint(1.0, 10.0, 0.0, speed_mps=20.0),
            Waypoint(0.0, 0.0, 0.0, speed_mps=0.0),
        ]
    }
    dimensions = {
        "ego": VehicleDimensions(
            length_m=4.4,
            width_m=1.8,
            height_m=1.7,
            actor_type="vehicle",
            carla_blueprint="vehicle.foo",
            xosc_export_mode="reach_position",
            parameter_declarations='<ParameterDeclaration name="speed" parameterType="double" value="10" />',
            controller_name="controller_a",
            controller_xml='<Controller name="controller_a"><Properties /></Controller>',
        ),
    }
    gaps = [DetectionGap("ego", 0.2, 0.4)]

    write_scenario_config(
        path,
        vehicles,
        dimensions,
        tmp_path / "map.xodr",
        gaps,
    )
    (
        loaded_vehicles,
        loaded_dimensions,
        loaded_map,
        loaded_gaps,
    ) = load_scenario_config(path)

    assert loaded_vehicles["ego"] == [
        Waypoint(0.0, 0.0, 0.0, speed_mps=0.0),
        Waypoint(1.0, 10.0, 0.0, speed_mps=20.0),
    ]
    assert loaded_dimensions["ego"].carla_blueprint == "vehicle.foo"
    assert loaded_dimensions["ego"].xosc_export_mode == "reach_position"
    assert "ParameterDeclaration" in loaded_dimensions["ego"].parameter_declarations
    assert loaded_dimensions["ego"].controller_name == "controller_a"
    assert loaded_map == tmp_path / "map.xodr"
    assert loaded_gaps == gaps
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["version"] == 4
    assert "trajectory_creation_mode" not in config


def test_scenario_config_roundtrip_preserves_non_derived_timestamps(tmp_path):
    path = tmp_path / "scenario_config.json"
    vehicles = {
        "ego": [
            Waypoint(0.0, 0.0, 0.0, speed_mps=10.0),
            Waypoint(2.0, 10.0, 0.0, speed_mps=10.0),
        ]
    }

    write_scenario_config(
        path,
        vehicles,
        {"ego": VehicleDimensions()},
        None,
        [],
    )
    loaded_vehicles, _dimensions, _map_path, _gaps = load_scenario_config(path)

    assert [point.time_s for point in loaded_vehicles["ego"]] == [0.0, 2.0]
    assert [point.speed_mps for point in loaded_vehicles["ego"]] == [10.0, 10.0]


def test_load_scenario_config_rejects_missing_vehicles(tmp_path):
    """Verify load scenario config rejects missing vehicles.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "scenario_config.json"
    path.write_text(json.dumps({"vehicles": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="contains no vehicles"):
        load_scenario_config(path)


def test_load_legacy_config_fills_speeds_and_ignores_creation_mode(tmp_path):
    path = tmp_path / "legacy_config.json"
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "trajectory_creation_mode": "time_based",
                "vehicles": [
                    {
                        "name": "ego",
                        "waypoints": [
                            {"time_s": 0.0, "x_m": 0.0, "y_m": 0.0},
                            {"time_s": 2.0, "x_m": 10.0, "y_m": 0.0},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    vehicles, _dimensions, _map_path, _gaps = load_scenario_config(path)

    assert [point.speed_mps for point in vehicles["ego"]] == pytest.approx(
        [0.0, 10.0]
    )
    assert vehicles["ego"][1].time_s == pytest.approx(2.0)


def test_xodr_speed_limits_feed_actor_compatible_lane_snapping(tmp_path):
    path = tmp_path / "speed_map.xodr"
    path.write_text(
        """<?xml version="1.0"?>
<OpenDRIVE>
  <header revMajor="1" revMinor="6" />
  <road name="main" length="20" id="1" junction="-1">
    <type s="0" type="town"><speed max="36" unit="km/h" /></type>
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry>
    </planView>
    <lanes>
      <laneSection s="0">
        <left>
          <lane id="1" type="biking" level="false">
            <width sOffset="0" a="3" b="0" c="0" d="0" />
          </lane>
        </left>
        <center><lane id="0" type="none" level="false" /></center>
        <right>
          <lane id="-1" type="driving" level="false">
            <width sOffset="0" a="3.5" b="0" c="0" d="0" />
            <speed sOffset="0" max="72" unit="km/h" />
          </lane>
        </right>
      </laneSection>
    </lanes>
  </road>
</OpenDRIVE>
""",
        encoding="utf-8",
    )

    roads = load_xodr_reference_map(path)
    scenario_map = ScenarioMap()
    scenario_map.load_view_only(roads, path)

    vehicle_snap = scenario_map.nearest_compatible_lane(
        5.0, -1.7, "vehicle", 2.0
    )
    cyclist_snap = scenario_map.nearest_compatible_lane(
        5.0, 1.4, "cyclist", 2.0
    )
    pedestrian_snap = scenario_map.nearest_compatible_lane(
        5.0, 1.4, "pedestrian", 2.0
    )

    assert vehicle_snap is not None
    assert vehicle_snap.lane_id == -1
    assert vehicle_snap.y_m == pytest.approx(-1.75)
    assert vehicle_snap.speed_limit_mps == pytest.approx(20.0)
    assert cyclist_snap is not None
    assert cyclist_snap.lane_id == 1
    assert cyclist_snap.speed_limit_mps == pytest.approx(10.0)
    assert pedestrian_snap is None


def test_openscenario_write_and_load_roundtrip_with_map_reference(tmp_path):
    """Verify openscenario write and load roundtrip with map reference.

    Args:
        tmp_path: tmp path used by this operation.

    """
    xosc_path = tmp_path / "scenario.xosc"
    trajectories = {
        "ego": {
            "time_s": [0.0, 1.0],
            "x_m": [0.0, 3.0],
            "y_m": [0.0, 1.0],
            "yaw_rad": [0.0, 0.1],
            "speed_mps": [2.0, 2.0],
            "actor_type": "pedestrian",
            "dimensions": {
                "actor_type": "pedestrian",
                "length_m": 0.5,
                "width_m": 0.5,
                "height_m": 1.8,
            },
        },
    }

    write_openscenario(trajectories, xosc_path, road_logic_file="maps/test.xodr")

    ET.parse(xosc_path)
    assert load_openscenario_xosc(xosc_path)["ego"] == [
        Waypoint(0.0, 0.0, 0.0, speed_mps=2.0),
        Waypoint(1.0, 3.0, 1.0),
    ]
    assert load_openscenario_map_reference(xosc_path) == "maps/test.xodr"
    assert load_openscenario_map_path(xosc_path) == tmp_path / "maps" / "test.xodr"


def test_load_openscenario_xosc_rejects_files_without_vertices(tmp_path):
    """Verify load openscenario OpenSCENARIO rejects files without vertices.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "empty.xosc"
    path.write_text("<OpenSCENARIO />", encoding="utf-8")
    with pytest.raises(ValueError, match="No FollowTrajectory"):
        load_openscenario_xosc(path)


def test_load_xodr_map_samples_line_arc_outer_edges_and_sections(tmp_path):
    """Verify load OpenDRIVE map samples line arc outer edges and sections.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "map.xodr"
    path.write_text(
        """
        <OpenDRIVE>
          <road name="line" id="1" length="10">
            <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
            <lanes>
              <laneSection s="0">
                <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
                <center><lane id="0" type="none" /></center>
                <right><lane id="-1" type="driving"><width sOffset="0" a="2" b="0" c="0" d="0" /></lane></right>
              </laneSection>
              <laneSection s="5">
                <left><lane id="1" type="driving"><width sOffset="0" a="4" b="0" c="0" d="0" /></lane></left>
                <center><lane id="0" type="none" /></center>
                <right><lane id="-1" type="driving"><width sOffset="0" a="2.5" b="0" c="0" d="0" /></lane></right>
              </laneSection>
            </lanes>
          </road>
          <road name="arc" id="2"><planView><geometry x="0" y="0" hdg="0" length="4"><arc curvature="0.1" /></geometry></planView></road>
        </OpenDRIVE>
        """,
        encoding="utf-8",
    )

    polylines = load_xodr_map(path)

    assert [
        polyline.name for polyline in polylines if polyline.kind == "reference"
    ] == ["line", "arc"]
    assert polylines[0].points[0] == pytest.approx((0.0, 0.0))
    assert len([polyline for polyline in polylines if polyline.kind == "outer"]) == 2
    assert len([polyline for polyline in polylines if polyline.kind == "section"]) == 2
    assert len(polylines[-1].points) >= 2


def test_write_xodr_map_roundtrip(tmp_path):
    """Verify write OpenDRIVE map roundtrip.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "created.xodr"
    write_xodr_map(
        path,
        [MapPolyline("created", [(0.0, 0.0), (5.0, 0.0), (5.0, 4.0)], width_m=8.0)],
    )

    ET.parse(path)
    polylines = load_xodr_map(path)

    assert [
        polyline.name for polyline in polylines if polyline.kind == "reference"
    ] == ["created"]
    assert polylines[0].points[0] == pytest.approx((0.0, 0.0))
    assert polylines[0].points[-1] == pytest.approx((5.0, 4.0))
    assert len([polyline for polyline in polylines if polyline.kind == "outer"]) == 2


def test_load_map_export_max_lateral_deviation_m_reads_nested_config(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "map_export:\n  max_lateral_deviation_m: 0.25\n",
        encoding="utf-8",
    )

    assert load_map_export_max_lateral_deviation_m(config_path) == pytest.approx(0.25)


def test_load_import_limits_reads_all_configured_values(tmp_path):
    config_path = tmp_path / "config.yml"
    configured = {
        "max_imported_actors": 12,
        "max_waypoints_per_actor": 13,
        "max_total_waypoints": 14,
        "max_imported_roads": 15,
        "max_points_per_road": 16,
        "max_total_map_points": 17,
        "max_text_field_chars": 18,
        "max_actor_name_chars": 19,
        "max_abs_coordinate_m": 20.5,
        "max_time_s": 21.5,
        "max_speed_mps": 22.5,
        "max_actor_dimension_m": 23.5,
    }
    config_path.write_text(
        "import_limits:\n"
        + "".join(f"  {key}: {value}\n" for key, value in configured.items()),
        encoding="utf-8",
    )

    assert load_import_limits(config_path) == configured


def test_load_import_limits_uses_defaults_for_invalid_values(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "import_limits:\n"
        "  max_imported_actors: invalid\n"
        "  max_speed_mps: invalid\n",
        encoding="utf-8",
    )

    assert load_import_limits(config_path) == DEFAULT_IMPORT_LIMITS


def test_sampled_map_points_adaptively_respects_lateral_deviation():
    polyline = MapPolyline(
        "curved",
        [(0.0, 0.0), (3.0, 4.0), (6.0, -4.0), (9.0, 0.0)],
        width_m=8.0,
    )

    dense_points = sampled_map_points(polyline, max_lateral_deviation_m=0.01)
    simplified_points = sampled_map_points(polyline, max_lateral_deviation_m=0.5)

    assert len(simplified_points) < len(dense_points)
    assert simplified_points[0] == pytest.approx(dense_points[0])
    assert simplified_points[-1] == pytest.approx(dense_points[-1])

    max_deviation_m = max(
        min(
            _point_to_segment_distance_m(point, segment_start, segment_end)
            for segment_start, segment_end in itertools.pairwise(simplified_points)
        )
        for point in dense_points
    )
    assert max_deviation_m <= 0.5


def test_write_xodr_map_preserves_road_links(tmp_path):
    """Verify write OpenDRIVE map preserves road links.

    Args:
        tmp_path: tmp path used by this operation.

    """
    path = tmp_path / "linked.xodr"
    roads = [
        MapPolyline(
            "road_a",
            [(0.0, 0.0), (5.0, 0.0)],
            width_m=6.0,
            successor_road="road_b",
        ),
        MapPolyline(
            "road_b",
            [(5.0, 0.0), (10.0, 0.0)],
            width_m=6.0,
            predecessor_road="road_a",
        ),
    ]

    write_xodr_map(path, roads)

    root = ET.parse(path).getroot()
    road_a, road_b = [road for road in root.iter() if road.tag == "road"]
    successor = road_a.find("link/successor")
    predecessor = road_b.find("link/predecessor")
    assert successor is not None
    assert successor.attrib["elementType"] == "road"
    assert successor.attrib["elementId"] == "2"
    assert successor.attrib["contactPoint"] == "start"
    assert predecessor is not None
    assert predecessor.attrib["elementType"] == "road"
    assert predecessor.attrib["elementId"] == "1"
    assert predecessor.attrib["contactPoint"] == "end"

    loaded = [
        polyline for polyline in load_xodr_map(path) if polyline.kind == "reference"
    ]
    assert loaded[0].successor_road == "2"
    assert loaded[1].predecessor_road == "1"


def test_load_xodr_reference_map_keeps_curved_geometry_within_tolerance(tmp_path):
    path = tmp_path / "curve.xodr"
    path.write_text(
        """
        <OpenDRIVE>
          <road name="arc" id="1" length="10">
            <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><arc curvature="0.1" /></geometry></planView>
            <lanes>
              <laneSection s="0">
                <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
                <center><lane id="0" type="none" /></center>
                <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
              </laneSection>
            </lanes>
          </road>
        </OpenDRIVE>
        """,
        encoding="utf-8",
    )

    polylines = load_xodr_reference_map(path)
    points = polylines[0].points

    assert len(points) >= 3

    dense_points = [
        (x, y)
        for _s, x, y, _heading in road_plan_view_samples(
            ET.parse(path).getroot().find("road"),
        )
    ]
    max_deviation_m = max(
        min(
            _point_to_segment_distance_m(point, segment_start, segment_end)
            for segment_start, segment_end in itertools.pairwise(points)
        )
        for point in dense_points
    )
    assert max_deviation_m <= 0.5


def test_load_xodr_reference_map_only_returns_centerlines(tmp_path):
    """Verify fast OpenDRIVE view-only loading keeps only reference roads."""
    path = tmp_path / "map.xodr"
    path.write_text(
        """
        <OpenDRIVE>
          <road name="line" id="1" length="10">
            <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
            <lanes>
              <laneSection s="0">
                <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
                <center><lane id="0" type="none" /></center>
                <right><lane id="-1" type="driving"><width sOffset="0" a="2" b="0" c="0" d="0" /></lane></right>
              </laneSection>
            </lanes>
          </road>
        </OpenDRIVE>
        """,
        encoding="utf-8",
    )

    polylines = load_xodr_reference_map(path)

    assert [polyline.kind for polyline in polylines] == ["reference"]
    assert polylines[0].name == "line"
    assert polylines[0].lane_count == 2
    assert polylines[0].points == pytest.approx([(0.0, 0.0), (10.0, 0.0)])


def test_loaded_xodr_map_exposes_elevation_and_superelevation(tmp_path):
    path = tmp_path / "elevated.xodr"
    path.write_text(
        """
        <OpenDRIVE>
          <road name="slope" id="1" length="10">
            <planView>
              <geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry>
            </planView>
            <elevationProfile>
              <elevation s="0" a="100" b="1" c="0.1" d="0" />
            </elevationProfile>
            <lateralProfile>
              <superelevation s="0" a="0.1" b="0" c="0" d="0" />
            </lateralProfile>
            <lanes>
              <laneSection s="0">
                <left>
                  <lane id="1" type="driving">
                    <width sOffset="0" a="5" b="0" c="0" d="0" />
                  </lane>
                </left>
                <center><lane id="0" type="none" /></center>
                <right>
                  <lane id="-1" type="driving">
                    <width sOffset="0" a="5" b="0" c="0" d="0" />
                  </lane>
                </right>
              </laneSection>
            </lanes>
          </road>
        </OpenDRIVE>
        """,
        encoding="utf-8",
    )

    scenario_map = ScenarioMap()
    scenario_map.load_view_only(load_xodr_reference_map(path), path)

    expected_center_elevation = 100.0 + 5.0 + 0.1 * 5.0**2
    assert scenario_map.elevation_at(5.0, 0.0) == pytest.approx(
        expected_center_elevation,
    )
    assert scenario_map.elevation_at(5.0, 2.0) == pytest.approx(
        expected_center_elevation + 2.0 * math.sin(0.1),
    )
    assert scenario_map.elevation_at(5.0, 6.0) is None

    exported_path = tmp_path / "exported.xodr"
    write_xodr_map(exported_path, scenario_map.view_roads)
    exported_map = ScenarioMap()
    exported_map.load_view_only(load_xodr_reference_map(exported_path), exported_path)

    assert exported_map.elevation_at(5.0, 0.0) == pytest.approx(
        expected_center_elevation,
    )


def test_sample_xodr_geometry_supports_poly3_param_poly3_and_spiral():
    """Verify every OpenDRIVE planView curve primitive is interpreted geometrically."""

    def endpoint(primitive: str):
        geometry = ET.fromstring(
            f'<geometry s="0" x="0" y="0" hdg="0" length="10">{primitive}</geometry>',
        )
        return sample_xodr_geometry_samples(geometry)[-1]

    poly3_end = endpoint('<poly3 a="0" b="0" c="0.1" d="0" />')
    assert poly3_end[1:3] == pytest.approx((10.0, 10.0))
    assert poly3_end[3] == pytest.approx(math.atan2(2.0, 1.0))

    normalized_end = endpoint(
        '<paramPoly3 aU="0" bU="10" cU="0" dU="0" aV="0" bV="5" cV="0" dV="0" pRange="normalized" />',
    )
    assert normalized_end[1:3] == pytest.approx((10.0, 5.0))
    assert normalized_end[3] == pytest.approx(math.atan2(5.0, 10.0))

    arc_length_end = endpoint(
        '<paramPoly3 aU="0" bU="1" cU="0" dU="0" aV="0" bV="0.1" cV="0" dV="0" pRange="arcLength" />',
    )
    assert arc_length_end[1:3] == pytest.approx((10.0, 1.0))

    spiral_end = endpoint('<spiral curvStart="0" curvEnd="0.1" />')
    assert spiral_end[1] < 10.0
    assert spiral_end[2] > 0.0
    assert spiral_end[3] == pytest.approx(0.5)


def test_xodr_transition_samples_are_unique_and_use_following_geometry(tmp_path):
    path = tmp_path / "geometry_boundary.xodr"
    path.write_text(
        """
        <OpenDRIVE>
          <road name="boundary" id="1" length="10">
            <planView>
              <geometry s="0" x="0" y="0" hdg="0" length="5"><line /></geometry>
              <geometry s="5" x="5.001" y="0" hdg="0" length="5"><line /></geometry>
            </planView>
            <lanes>
              <laneOffset s="5" a="1" b="0.5" c="0" d="0" />
              <laneSection s="0">
                <center><lane id="0" type="none" /></center>
                <right>
                  <lane id="-1" type="driving">
                    <width sOffset="0" a="2" b="0" c="0" d="0" />
                    <width sOffset="5" a="3" b="0" c="0" d="0" />
                  </lane>
                </right>
              </laneSection>
            </lanes>
          </road>
        </OpenDRIVE>
        """,
        encoding="utf-8",
    )

    road = load_xodr_reference_map(path)[0]
    profiles_at_boundary = [
        profile
        for profile in road.lane_cross_sections
        if profile.s_m == pytest.approx(5.0)
    ]

    assert len(profiles_at_boundary) == 1
    assert profiles_at_boundary[0].x_m == pytest.approx(5.001)
    assert profiles_at_boundary[0].lane_offset_m == pytest.approx(1.0)
    assert profiles_at_boundary[0].lane_widths_m[-1] == pytest.approx(3.0)


def test_load_xodr_reference_map_marks_pedestrian_only_roads(tmp_path):
    """Verify a sidewalk-only OpenDRIVE road remains visible to the map renderer."""
    path = tmp_path / "footpath.xodr"
    path.write_text(
        """
        <OpenDRIVE>
          <road name="footpath" id="1" length="10">
            <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
            <lanes><laneSection s="0"><left>
              <lane id="1" type="sidewalk"><width sOffset="0" a="2" b="0" c="0" d="0" /></lane>
            </left><center><lane id="0" type="none" /></center></laneSection></lanes>
          </road>
        </OpenDRIVE>
        """,
        encoding="utf-8",
    )

    road = load_xodr_reference_map(path)[0]

    assert road.lane_types == {1: "sidewalk"}
    assert road.is_pedestrian_only() is True
