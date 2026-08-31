import asyncio
import json
from pathlib import Path
from tempfile import SpooledTemporaryFile
from xml.etree import ElementTree as ET
import zipfile

import pytest
from fastapi import HTTPException, UploadFile
from starlette.requests import Request

import scenario_generator.webapp.server as web_server
from scenario_generator.map.map import LaneCrossSection, MapPolyline


def request_with_scheme(scheme: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443 if scheme == "https" else 80),
        }
    )


def test_web_session_ids_reject_path_components():
    assert web_server.valid_session_id("a" * 32)
    assert not web_server.valid_session_id("../../app")
    assert not web_server.valid_session_id("A" * 32)

    token = web_server.active_session_id.set("../../app")
    try:
        with pytest.raises(RuntimeError, match="Invalid active session ID"):
            web_server.session_export_directory()
    finally:
        web_server.active_session_id.reset(token)

    store = web_server.ScenarioStore(Path("/tmp"))
    with pytest.raises(ValueError, match="Invalid session ID"):
        store.get("../../app")


def test_web_session_store_removes_expired_state_and_files(tmp_path):
    session_id = "b" * 32
    session_directory = tmp_path / session_id
    session_directory.mkdir()
    (session_directory / "upload.json").write_text("{}", encoding="utf-8")
    store = web_server.ScenarioStore(
        tmp_path, ttl_seconds=10, cleanup_interval_seconds=1
    )
    store.sessions[session_id] = web_server.ScenarioSession(last_access=1.0)

    store.cleanup_expired(now=11.0, force=True)

    assert session_id not in store.sessions
    assert not session_directory.exists()


def test_web_delete_session_data_removes_state_files_and_cookie(tmp_path, monkeypatch):
    session_id = "d" * 32
    session_directory = tmp_path / session_id
    session_directory.mkdir()
    (session_directory / "upload.json").write_text("{}", encoding="utf-8")
    store = web_server.ScenarioStore(tmp_path)
    store.get(session_id).scenario.add_actor()
    monkeypatch.setattr(web_server, "scenario_store", store)
    monkeypatch.setenv("SCENARIO_GENERATOR_SESSION_COOKIE_PATH", "/generator")
    token = web_server.active_session_id.set(session_id)
    try:
        response = web_server.delete_session_data(request_with_scheme("https"))
    finally:
        web_server.active_session_id.reset(token)

    assert json.loads(response.body) == {"deleted": True}
    assert session_id not in store.sessions
    assert not session_directory.exists()
    cookie = response.headers["set-cookie"]
    assert f"{web_server.SESSION_COOKIE_NAME}=" in cookie
    assert "Max-Age=0" in cookie
    assert "Path=/generator" in cookie
    assert "Secure" in cookie


def test_web_quality_checker_temporary_copy_is_removed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "scenario.xosc"
    processed_path = tmp_path / "results" / "tmp" / "processed_scenario.xosc"

    with web_server.remove_quality_checker_temporary_copy(input_path):
        processed_path.parent.mkdir(parents=True)
        processed_path.write_text("scenario data", encoding="utf-8")

    assert not processed_path.exists()


def test_web_upload_limit_removes_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "MAX_UPLOAD_BYTES", 4)
    source = SpooledTemporaryFile()
    source.write(b"12345")
    source.seek(0)
    upload = UploadFile(file=source, filename="large.json")
    destination = tmp_path / "large.json"

    with pytest.raises(web_server.HTTPException) as error:
        asyncio.run(web_server.store_upload(upload, destination))

    assert error.value.status_code == 413
    assert not destination.exists()


def test_web_referenced_maps_must_stay_inside_session_directory(
    tmp_path, monkeypatch
):
    session_id = "c" * 32
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    token = web_server.active_session_id.set(session_id)
    try:
        allowed_path = tmp_path / session_id / "maps" / "map.xodr"
        assert web_server.confined_session_map_path(allowed_path) == allowed_path
        with pytest.raises(ValueError, match="active session directory"):
            web_server.confined_session_map_path(tmp_path / "outside.xodr")
        with pytest.raises(ValueError, match=".xodr or .xml"):
            web_server.confined_session_map_path(
                tmp_path / session_id / "maps" / "map.json"
            )
    finally:
        web_server.active_session_id.reset(token)


def test_web_upload_names_are_path_free_and_bounded():
    upload_name = web_server.safe_upload_name(
        "../../" + "a" * 200 + ".XODR", "map.xodr"
    )

    assert upload_name == "a" * 128 + ".xodr"


def test_web_secure_cookie_uses_https_or_explicit_setting(monkeypatch):
    monkeypatch.delenv("SCENARIO_GENERATOR_SECURE_COOKIES", raising=False)
    assert web_server.secure_cookie_for_request(request_with_scheme("https"))
    assert not web_server.secure_cookie_for_request(request_with_scheme("http"))

    monkeypatch.setenv("SCENARIO_GENERATOR_SECURE_COOKIES", "true")
    assert web_server.secure_cookie_for_request(request_with_scheme("http"))


def test_web_session_cookie_path_supports_reverse_proxy_prefix(monkeypatch):
    monkeypatch.delenv("SCENARIO_GENERATOR_SESSION_COOKIE_PATH", raising=False)
    assert web_server.session_cookie_path() == "/"

    monkeypatch.setenv("SCENARIO_GENERATOR_SESSION_COOKIE_PATH", "/generator/")
    assert web_server.session_cookie_path() == "/generator"

    for invalid_path in ("generator", "/generator; Secure", "/generator path"):
        monkeypatch.setenv("SCENARIO_GENERATOR_SESSION_COOKIE_PATH", invalid_path)
        assert web_server.session_cookie_path() == "/"


def test_web_scenario_serializes_actor_state():
    scenario = web_server.WebScenario()

    snapshot = scenario.snapshot()

    assert snapshot["actors"][0]["name"] == "vehicle_1"
    assert [actor["name"] for actor in snapshot["actors"]] == [
        "vehicle_1",
        "vehicle_2",
    ]
    assert len(snapshot["actors"][0]["waypoints"]) == 3
    assert snapshot["actors"][0]["waypoints"][0]["speed_mps"] == pytest.approx(13.33)
    assert snapshot["actors"][1]["waypoints"][0]["x_m"] == 12.5
    assert (
        snapshot["actors"][1]["waypoints"][0]["x_m"]
        - snapshot["actors"][0]["waypoints"][0]["x_m"]
        == scenario.dimensions["vehicle_1"].length_m + 8.0
    )
    assert snapshot["actors"][0]["profile_distances_m"] == pytest.approx(
        [0.0, 13.33, 26.66]
    )
    assert len(snapshot["actors"][0]["curve"]["time_s"]) > 3
    assert len(snapshot["actors"][0]["segment_midpoints"]) == 2
    assert snapshot["settings"]["show_detection_gaps"] is False
    assert snapshot["settings"]["show_trajectory_waypoints"] is True
    assert snapshot["settings"]["show_sqc_warnings"] is True


def test_web_export_writes_trajectory_json(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", web_server.WebScenario())

    response = web_server.export_scenario("json", {"base_name": "web_export"})

    assert response.path == Path(tmp_path) / "web_export.json"
    assert (tmp_path / "web_export.json").exists()


def test_web_export_reports_xosc_configuration_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", web_server.WebScenario())
    monkeypatch.setattr(
        web_server,
        "write_openscenario",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("missing config")),
    )

    with pytest.raises(web_server.HTTPException) as error:
        web_server.export_scenario("xosc", {"base_name": "web_export"})

    assert error.value.status_code == 400
    assert error.value.detail == "missing config"


def test_web_batch_export_writes_selected_files_and_config(
    tmp_path, monkeypatch
):
    scenario = web_server.WebScenario()
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", scenario)

    response = web_server.export_scenario_bundle(
        {
            "base_name": "web_export",
            "formats": ["json"],
            "additional_information": {
            "postprocessing_scripts": [],
            },
        }
    )

    assert response.path == tmp_path / "web_export_exports.zip"
    with zipfile.ZipFile(response.path) as bundle:
        assert sorted(bundle.namelist()) == [
            "web_export.json",
            "web_export_config.json",
        ]


def test_web_batch_export_includes_required_xodr_and_references_it(tmp_path, monkeypatch):
    scenario = web_server.WebScenario()
    scenario.map.load_editable(
        [
            MapPolyline(
                name="road_1",
                points=[(0.0, 0.0), (10.0, 0.0)],
                lane_count=2,
            )
        ],
        None,
    )
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", scenario)

    response = web_server.export_scenario_bundle(
        {"base_name": "web_export", "formats": ["xosc"]}
    )

    with zipfile.ZipFile(response.path) as bundle:
        assert sorted(bundle.namelist()) == [
            "web_export.xodr",
            "web_export.xosc",
            "web_export_config.json",
        ]
        assert "web_export.xodr" in bundle.read("web_export.xosc").decode()


def test_web_batch_export_preserves_view_only_map_and_uses_bundle_name(
    tmp_path, monkeypatch
):
    source_map = (
        Path(web_server.DOCUMENTATION_DIRECTORY)
        / "examples"
        / "tutorial-straight-road.xodr"
    )
    scenario = web_server.WebScenario()
    scenario.map.load_view_only(web_server.load_validated_map(source_map), source_map)
    scenario.additional_information = {
        "xosc_map_path": "original-map-name.xodr",
        "postprocessing_scripts": [],
        "postprocessing_parameters": {},
    }
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", scenario)

    response = web_server.export_scenario_bundle(
        {
            "base_name": "web_export",
            "formats": ["xosc"],
            "additional_information": scenario.additional_information,
        }
    )

    with zipfile.ZipFile(response.path) as bundle:
        assert sorted(bundle.namelist()) == [
            "web_export.xodr",
            "web_export.xosc",
            "web_export_config.json",
        ]
        assert bundle.read("web_export.xodr") == source_map.read_bytes()
        xosc = bundle.read("web_export.xosc").decode()
        assert "web_export.xodr" in xosc
        assert "original-map-name.xodr" not in xosc
        config = json.loads(bundle.read("web_export_config.json"))
        assert config["map_path"] == "web_export.xodr"
        assert (
            config["additional_scenario_information"]["xosc_map_path"]
            == "web_export.xodr"
        )


def test_web_scenario_calculates_waypoint_timing_and_segment_speed():
    scenario = web_server.WebScenario()

    scenario.settings["waypoint_timing_mode"] = "constant_speed"
    scenario.add_waypoint("vehicle_1", 10.0, 0.0)
    scenario.set_segment_speed("vehicle_1", 0, 10.0)

    waypoints = scenario.vehicles["vehicle_1"]
    assert len(waypoints) == 4
    assert waypoints[1].time_s > waypoints[0].time_s
    assert all(
        following.time_s > previous.time_s
        for previous, following in zip(waypoints, waypoints[1:])
    )


def test_web_rejected_segment_speed_preserves_waypoints():
    scenario = web_server.WebScenario()
    scenario.vehicles["vehicle_1"] = [
        web_server.Waypoint(0.0, 0.0, 0.0, speed_mps=8.0),
        web_server.Waypoint(1.0, 8.0, 0.0, speed_mps=8.0),
    ]
    original = scenario.waypoint_snapshot(scenario.vehicles["vehicle_1"])

    with pytest.raises(ValueError, match="negative point speed"):
        scenario.set_segment_speed("vehicle_1", 0, 1.0)

    assert scenario.vehicles["vehicle_1"] == original


def test_web_new_point_timing_mode_changes_timestamp_and_speed():
    fixed_time = web_server.WebScenario()
    fixed_time.add_waypoint("vehicle_1", 53.32, 0.0)

    constant_speed = web_server.WebScenario()
    constant_speed.settings["waypoint_timing_mode"] = "constant_speed"
    constant_speed.add_waypoint("vehicle_1", 53.32, 0.0)

    fixed_point = fixed_time.vehicles["vehicle_1"][-1]
    constant_point = constant_speed.vehicles["vehicle_1"][-1]
    assert fixed_point.time_s == pytest.approx(3.0)
    assert constant_point.time_s == pytest.approx(4.0)
    assert fixed_point.speed_mps != constant_point.speed_mps


def test_web_close_new_point_clamps_derived_speed_instead_of_rejecting():
    scenario = web_server.WebScenario()

    scenario.add_waypoint("vehicle_1", 27.16, 0.0)

    point = scenario.vehicles["vehicle_1"][-1]
    assert point.time_s == pytest.approx(3.0)
    assert point.speed_mps == pytest.approx(0.0)


def test_web_enables_tooltips_by_default():
    assert web_server.WebScenario().settings["tooltips_enabled"] is True


def test_web_lists_and_loads_bundled_default_scenarios(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)

    defaults = web_server.list_default_scenarios()["defaults"]

    assert defaults == [
        {"name": "cut_in_from_left.xosc", "label": "Cut-in from left (.xosc)"},
        {
            "name": "cut_in_from_left_on_curved_road.json",
            "label": "Cut-in from left on curved road (.json)",
        },
        {
            "name": "Pass_straight_intersecting_vehicle_from_right_passing_straight.json",
            "label": "Pass straight intersecting vehicle from right passing straight (.json)",
        },
        {
            "name": "VRU_crossing_from_left.json",
            "label": "VRU crossing from left (.json)",
        },
    ]
    expected_actors = {
        "cut_in_from_left.xosc": ["ego_vehicle", "cut_in_vehicle"],
        "cut_in_from_left_on_curved_road.json": [
            "ego_vehicle",
            "cut_in_vehicle",
        ],
        "Pass_straight_intersecting_vehicle_from_right_passing_straight.json": [
            "car",
            "cyclist",
        ],
        "VRU_crossing_from_left.json": ["approaching_vehicle", "pedestrian"],
    }
    for default_name, actor_names in expected_actors.items():
        monkeypatch.setattr(web_server, "scenario", web_server.WebScenario())
        snapshot = web_server.load_default_scenario(default_name)
        assert [actor["name"] for actor in snapshot["actors"]] == actor_names
        if default_name.endswith(".json"):
            assert snapshot["additional_information"]["file_header"]["description"]
        if default_name in {
            "cut_in_from_left_on_curved_road.json",
            "VRU_crossing_from_left.json",
        }:
            assert snapshot["map"]["roads"]
            expected_map_name = (
                "synthetic_curve_cut_in.xodr"
                if default_name == "cut_in_from_left_on_curved_road.json"
                else "RITA-junction.xodr"
            )
            assert Path(snapshot["map"]["path"]).name == expected_map_name
            for actor_name, waypoints in web_server.scenario.vehicles.items():
                actor_type = web_server.scenario.dimensions[actor_name].actor_type
                checked_waypoints = waypoints
                if (
                    default_name == "VRU_crossing_from_left.json"
                    and actor_name == "pedestrian"
                ):
                    checked_waypoints = [waypoints[0], waypoints[-1]]
                for point in checked_waypoints:
                    assert web_server.scenario.map.nearest_compatible_lane(
                        point.x_m,
                        point.y_m,
                        actor_type,
                        max_distance_m=2.0,
                    ) is not None
            if default_name == "VRU_crossing_from_left.json":
                vehicle_conflict = web_server.scenario.vehicles[
                    "approaching_vehicle"
                ][2]
                pedestrian_conflict = web_server.scenario.vehicles["pedestrian"][2]
                assert vehicle_conflict.time_s == pedestrian_conflict.time_s == 4.0
                assert vehicle_conflict.x_m == pedestrian_conflict.x_m
                assert vehicle_conflict.y_m == pedestrian_conflict.y_m
        else:
            assert not snapshot["map"]["roads"]


def test_web_mapless_default_scenario_replaces_existing_map(tmp_path, monkeypatch):
    active_scenario = web_server.WebScenario()
    active_scenario.map.load_editable(
        [MapPolyline(name="old_road", points=[(0.0, 0.0), (10.0, 0.0)])],
        tmp_path / "old_map.xodr",
    )
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", active_scenario)

    snapshot = web_server.load_default_scenario(
        "Pass_straight_intersecting_vehicle_from_right_passing_straight.json"
    )

    assert not snapshot["map"]["roads"]
    assert snapshot["map"]["path"] == ""
    assert active_scenario.map.edit_enabled is False
    assert snapshot["settings"]["map_mode"] is False


def test_web_intersecting_default_traverses_crossing_with_two_second_gap(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", web_server.WebScenario())

    web_server.load_default_scenario(
        "Pass_straight_intersecting_vehicle_from_right_passing_straight.json"
    )

    car = web_server.scenario.vehicles["car"]
    cyclist = web_server.scenario.vehicles["cyclist"]
    assert (car[0].x_m, car[-1].x_m) == (-40.0, 40.0)
    assert (cyclist[0].y_m, cyclist[-1].y_m) == (-12.0, 12.0)
    car_conflict = next(point for point in car if point.x_m == point.y_m == 0.0)
    cyclist_conflict = next(
        point for point in cyclist if point.x_m == point.y_m == 0.0
    )
    assert car_conflict.time_s == 4.0
    assert cyclist_conflict.time_s == 6.0


def test_web_mapless_scenario_config_import_replaces_existing_map(
    tmp_path, monkeypatch
):
    active_scenario = web_server.WebScenario()
    active_scenario.map.load_editable(
        [MapPolyline(name="old_road", points=[(0.0, 0.0), (10.0, 0.0)])],
        tmp_path / "old_map.xodr",
    )
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", active_scenario)
    source = SpooledTemporaryFile()
    source.write(
        (
            web_server.DEFAULT_SCENARIO_DIRECTORY
            / "Pass_straight_intersecting_vehicle_from_right_passing_straight.json"
        ).read_bytes()
    )
    source.seek(0)

    snapshot = asyncio.run(
        web_server.import_config(
            UploadFile(
                file=source,
                filename="Pass_straight_intersecting_vehicle_from_right_passing_straight.json",
            )
        )
    )

    assert not snapshot["map"]["roads"]
    assert snapshot["map"]["path"] == ""
    assert active_scenario.map.edit_enabled is False
    assert snapshot["settings"]["map_mode"] is False


def test_web_scenario_config_finds_separately_loaded_map_by_name(
    tmp_path, monkeypatch
):
    source_map = web_server.DEFAULT_MAP_DIRECTORY / "RITA-junction.xodr"
    uploaded_map = tmp_path / "maps" / source_map.name
    uploaded_map.parent.mkdir()
    uploaded_map.write_bytes(source_map.read_bytes())
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path)
    monkeypatch.setattr(web_server, "scenario", web_server.WebScenario())
    source = SpooledTemporaryFile()
    source.write(
        (
            web_server.DEFAULT_SCENARIO_DIRECTORY / "VRU_crossing_from_left.json"
        ).read_bytes()
    )
    source.seek(0)

    snapshot = asyncio.run(
        web_server.import_config(
            UploadFile(file=source, filename="VRU_crossing_from_left.json")
        )
    )

    assert snapshot["map"]["roads"]
    assert Path(snapshot["map"]["path"]) == uploaded_map
    assert snapshot["map_load_hint"] is None


def test_web_rejects_default_resource_path_traversal(monkeypatch):
    monkeypatch.setattr(web_server, "scenario", web_server.WebScenario())

    with pytest.raises(HTTPException) as error:
        web_server.load_default_scenario(
            "../Pass_straight_intersecting_vehicle_from_right_passing_straight.json"
        )

    assert error.value.status_code == 404


def test_web_lists_supplied_default_maps():
    assert web_server.list_default_maps()["defaults"] == [
        {"name": "highway.xodr", "label": "Highway (.xodr)"},
        {"name": "RITA-junction.xodr", "label": "RITA junction (.xodr)"},
        {"name": "roundabout.xodr", "label": "Roundabout (.xodr)"},
    ]


def test_web_lists_and_loads_bundled_default_maps(tmp_path, monkeypatch):
    default_directory = tmp_path / "default_maps"
    default_directory.mkdir()
    source_map = (
        Path(web_server.DOCUMENTATION_DIRECTORY)
        / "examples"
        / "tutorial-straight-road.xodr"
    )
    (default_directory / "straight_road.xodr").write_bytes(source_map.read_bytes())
    monkeypatch.setattr(web_server, "DEFAULT_MAP_DIRECTORY", default_directory)
    monkeypatch.setattr(web_server, "EXPORT_DIRECTORY", tmp_path / "sessions")
    monkeypatch.setattr(web_server, "scenario", web_server.WebScenario())

    assert web_server.list_default_maps()["defaults"] == [
        {"name": "straight_road.xodr", "label": "Straight Road (.xodr)"}
    ]
    snapshot = web_server.load_default_map("straight_road.xodr")

    assert snapshot["map"]["roads"]
    assert Path(snapshot["map"]["path"]).is_relative_to(tmp_path / "sessions")


def test_web_serves_use_case_documentation():
    response = web_server.documentation_page("01-intersection-conflict.md")

    body = response.body.decode()
    assert "<h1>Create an intersecting conflict (no map)</h1>" in body
    assert 'src="images/intersection-conflict.png"' in body
    assert body.count("&larr; All examples") == 2
    assert body.count("Next tutorial") == 2
    assert "aria-label='Tutorial navigation at end'" in body
    assert "src='/branding/logo.svg'" in body
    assert ">Tutorials</a>" not in body
    assert "https://scenario.center/imprint/" in body
    assert "id='docs-about'" in body
    assert "id='docs-about-dialog'" in body
    assert "ika-rwth-aachen/scenario-generator" in body
    assert "id='docs-data-privacy'" in body
    assert "https://scenario.center/privacy-policy/" in body
    assert "id='docs-data-privacy-dialog'" in body
    assert "scenario.center privacy policy" in body
    assert "Delete my data now" in body


def test_web_renders_documentation_overview_table():
    response = web_server.documentation_page("README.md")

    body = response.body.decode()
    assert "<table>" in body
    assert 'href="01-intersection-conflict.md"' in body
    assert "&larr; All examples" not in body


def test_web_openads_tutorial_links_to_controller_download():
    response = web_server.documentation_page("04-openads-scenario.md")

    body = response.body.decode()
    assert 'href="/docs/download/karl-controller-template.json"' in body
    assert 'href="/docs/download/synthetic_curve_cut_in.xodr"' in body
    assert "raw.githubusercontent.com/openads-project/openadsim" in body
    assert "karl-controller-template.json (Download)" in body
    assert "Omega-Prime (.mcap)" in body
    assert "represent route actions as trajectories" in body
    assert "LANELET_RELOAD" not in body


def test_web_import_tutorial_links_to_map_download():
    response = web_server.documentation_page("03-import-adapt-map-scenario.md")

    body = response.body.decode()
    assert 'href="/docs/download/tutorial-straight-road.xodr"' in body
    assert "tutorial-straight-road.xodr (Download)" in body
    assert "VRU crossing from left (.json)" in body
    assert "RITA junction" in body


@pytest.mark.parametrize(
    ("directory_name", "file_name"),
    [
        ("templates", "karl-controller-template.json"),
        ("examples", "tutorial-straight-road.xodr"),
        ("examples", "synthetic_curve_cut_in.xodr"),
    ],
)
def test_web_downloads_documentation_file(directory_name, file_name):
    response = web_server.download_documentation_file(file_name)

    assert response.path == (
        web_server.DOCUMENTATION_DIRECTORY
        / directory_name
        / file_name
    )
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{file_name}"'
    )


def test_web_controller_template_contains_schema_ordered_xml():
    template_path = (
        web_server.DOCUMENTATION_DIRECTORY
        / "templates"
        / "karl-controller-template.json"
    )
    template = json.loads(template_path.read_text(encoding="utf-8"))
    controller_action = ET.fromstring(template["xml"])

    assert template["name"] == "RosRouteController"
    assert [child.tag for child in controller_action] == [
        "AssignControllerAction",
        "OverrideControllerValueAction",
    ]
    controller = controller_action.find("./AssignControllerAction/Controller")
    assert controller is not None
    assert controller.attrib["name"] == "RosRouteController"
    module = controller.find("./Properties/Property[@name='module']")
    assert module is not None
    assert module.attrib["value"] == "ros_vehicle_control_route_action.py"
    overrides = controller_action.find("./OverrideControllerValueAction")
    assert overrides is not None
    assert all(child.attrib["active"] == "false" for child in overrides)


def test_web_rejects_unknown_documentation_file():
    with pytest.raises(HTTPException, match="Documentation file not found"):
        web_server.download_documentation_file("missing.json")


def test_web_help_menu_links_to_tutorial_overview():
    index = (web_server.STATIC_DIRECTORY / "index.html").read_text(encoding="utf-8")

    assert 'id="tutorials"' in index
    assert 'href="/docs/README.md"' in index
    assert 'id="about"' in index
    assert 'id="about-dialog"' in index
    assert '>Upload scenario</button>' in index
    assert '>Upload map</button>' in index


def test_web_serves_about_content():
    response = web_server.get_about()

    assert "browser-based workspace" in response["html"]
    assert "ika-rwth-aachen/scenario-generator" in response["html"]
    assert 'target="_blank" rel="noopener noreferrer"' in response["html"]
    assert "4-CAD" in response["html"]
    assert "SYNERGIES" in response["html"]


def test_web_rejects_documentation_paths_outside_docs():
    with pytest.raises(HTTPException, match="Documentation page not found"):
        web_server.documentation_page("../README.md")


def test_web_waypoint_time_edit_matches_desktop_propagation():
    scenario = web_server.WebScenario()

    scenario.set_waypoint_time("vehicle_1", 1, 1.5)

    waypoints = scenario.vehicles["vehicle_1"]
    assert [point.time_s for point in waypoints] == [0.0, 1.5, 2.5]
    assert waypoints[1].speed_mps == pytest.approx(13.33 / 1.5 * 2.0 - 13.33)
    assert waypoints[2].speed_mps == pytest.approx(26.66 - waypoints[1].speed_mps)


def test_web_actor_type_change_uses_configured_dimensions(monkeypatch):
    scenario = web_server.WebScenario()
    monkeypatch.setattr(web_server, "scenario", scenario)

    web_server.update_actor("vehicle_1", {"actor_type": "pedestrian"})

    defaults = web_server.actor_default_dimensions("pedestrian")
    dimensions = scenario.dimensions["vehicle_1"]
    assert dimensions.actor_type == "pedestrian"
    assert dimensions.length_m == defaults.length_m
    assert dimensions.width_m == defaults.width_m


def test_web_actor_dimensions_match_desktop_minimum(monkeypatch):
    scenario = web_server.WebScenario()
    monkeypatch.setattr(web_server, "scenario", scenario)

    web_server.update_actor("vehicle_1", {"length_m": -4.0, "width_m": 0.0})

    dimensions = scenario.dimensions["vehicle_1"]
    assert dimensions.length_m == pytest.approx(0.001)
    assert dimensions.width_m == pytest.approx(0.001)


def test_web_imported_actor_counter_uses_the_highest_vehicle_suffix():
    scenario = web_server.WebScenario()
    scenario.vehicles = {"vehicle_8": []}
    scenario.dimensions = {"vehicle_8": web_server.VehicleDimensions()}
    scenario.actor_counter = scenario.next_actor_counter()

    assert scenario.add_actor() == "vehicle_9"


def test_web_waypoint_insert_and_delete_keep_a_valid_trajectory():
    scenario = web_server.WebScenario()

    scenario.insert_waypoint("vehicle_1", 1)
    scenario.delete_waypoint("vehicle_1", 1)

    waypoints = scenario.vehicles["vehicle_1"]
    assert len(waypoints) == 3
    assert all(
        following.time_s > previous.time_s
        for previous, following in zip(waypoints, waypoints[1:])
    )


def test_web_scenario_creates_editable_map():
    scenario = web_server.WebScenario()
    scenario.map.load_editable(
        [web_server.MapPolyline(name="road_1", points=[(0.0, 0.0), (1.0, 0.0)])],
        None,
    )

    snapshot = scenario.snapshot()

    assert snapshot["map"]["roads"][0]["name"] == "road_1"


def test_web_creating_map_disables_velocity_profile(monkeypatch):
    scenario = web_server.WebScenario()
    scenario.settings["show_speed_profile"] = True
    monkeypatch.setattr(web_server, "scenario", scenario)

    snapshot = web_server.create_blank_map({})

    assert snapshot["settings"]["map_mode"] is True
    assert snapshot["settings"]["show_speed_profile"] is False


def test_web_adding_first_road_initializes_blank_map(monkeypatch):
    scenario = web_server.WebScenario()
    monkeypatch.setattr(web_server, "scenario", scenario)

    snapshot = web_server.add_map_road({})

    assert snapshot["map"]["edit_enabled"] is True
    assert snapshot["map"]["roads"][0]["name"] == "road_1"


def test_web_lane_geometry_splits_at_lane_type_changes():
    profile = type(
        "Profile",
        (),
        {
            "x_m": 0.0,
            "y_m": 0.0,
            "heading_rad": 0.0,
            "lane_offset_m": 0.0,
            "lane_widths_m": {1: 3.0},
            "lane_types": {1: "driving"},
        },
    )
    biking = type(
        "Profile",
        (),
        {
            "x_m": 1.0,
            "y_m": 0.0,
            "heading_rad": 0.0,
            "lane_offset_m": 0.0,
            "lane_widths_m": {1: 3.0},
            "lane_types": {1: "biking"},
        },
    )
    after = type(
        "Profile",
        (),
        {
            "x_m": 2.0,
            "y_m": 0.0,
            "heading_rad": 0.0,
            "lane_offset_m": 0.0,
            "lane_widths_m": {1: 3.0},
            "lane_types": {1: "biking"},
        },
    )

    assert web_server.WebScenario.imported_lane_profile_runs(
        [profile, biking, after], 1
    ) == [("driving", [profile, biking]), ("biking", [biking, after])]


def test_web_map_edit_deforms_imported_lane_profile():
    scenario = web_server.WebScenario()
    profile = LaneCrossSection(
        s_m=5.0,
        x_m=5.0,
        y_m=1.0,
        heading_rad=0.0,
        lane_offset_m=0.0,
        lane_widths_m={1: 3.0},
        lane_types={1: "driving"},
    )
    road = MapPolyline(
        name="road_1",
        points=[(0.0, 0.0), (10.0, 10.0)],
        lane_cross_sections=[profile],
    )

    scenario.update_imported_road_geometry(road, [(0.0, 0.0), (10.0, 0.0)])

    assert (profile.x_m, profile.y_m) != (5.0, 1.0)
    assert profile.heading_rad == pytest.approx(0.7853981634)


def test_web_road_point_insert_uses_neighbor_interpolation(monkeypatch):
    scenario = web_server.WebScenario()
    scenario.map.load_editable(
        [MapPolyline(name="road_1", points=[(0.0, 0.0), (4.0, 2.0)])], None
    )
    monkeypatch.setattr(web_server, "scenario", scenario)

    snapshot = web_server.insert_map_road_point(0, 1)

    assert snapshot["map"]["roads"][0]["points"] == [
        (0.0, 0.0),
        (2.0, 1.0),
        (4.0, 2.0),
    ]


def test_web_moves_connected_simple_road_endpoint():
    scenario = web_server.WebScenario()
    source = MapPolyline(
        name="source",
        points=[(0.0, 0.0), (2.0, 0.0)],
        successor_road="target",
    )
    target = MapPolyline(
        name="target",
        points=[(2.0, 0.0), (4.0, 0.0)],
        predecessor_road="source",
    )
    scenario.map.load_editable([source, target], None)
    connected_source, connected_target = scenario.map.roads
    connected_source.points[-1] = (3.0, 1.0)

    scenario.sync_moved_road_endpoint(connected_source, 1)

    assert connected_target.points[0] == (3.0, 1.0)


def test_web_quality_checker_messages_name_sideslip_angle():
    class Result:
        dynamic_errors = ([], [], ["vehicle_2"], [])
        position_resolution_warnings = []
        file_errors = ([], [], [], [])
        xsd_errors = []

    warnings, problems = web_server.quality_checker_issue_lists(Result())

    assert warnings == []
    assert problems[0].startswith("Sideslip angle error for vehicle_2")


def test_web_road_connection_creates_reciprocal_lane_links(monkeypatch):
    scenario = web_server.WebScenario()
    source = MapPolyline(name="source", points=[(0.0, 0.0), (1.0, 0.0)])
    target = MapPolyline(name="target", points=[(2.0, 0.0), (3.0, 0.0)])
    scenario.map.load_editable([source, target], None)
    monkeypatch.setattr(web_server, "scenario", scenario)

    web_server.connect_map_roads(
        {
            "source_index": 0,
            "source_lane_id": -1,
            "target_index": 1,
            "target_lane_id": -1,
        }
    )

    connected_source, connected_target = scenario.map.roads
    assert connected_source.successor_road == "target"
    assert connected_source.successor_lane_links == "-1->-1"
    assert connected_target.predecessor_road == "source"
    assert connected_target.predecessor_lane_links == "-1->-1"


def test_web_validates_and_mirrors_manual_road_relations(monkeypatch):
    scenario = web_server.WebScenario()
    scenario.map.load_editable(
        [
            MapPolyline(name="source", points=[(0.0, 0.0), (1.0, 0.0)]),
            MapPolyline(name="target", points=[(2.0, 0.0), (3.0, 0.0)]),
        ],
        None,
    )
    monkeypatch.setattr(web_server, "scenario", scenario)

    web_server.update_map_road(
        0,
        {"successor_road": "target", "successor_lane_links": "-1->-1"},
    )

    source, target = scenario.map.roads
    assert source.successor_road == "target"
    assert target.predecessor_road == "source"
    with pytest.raises(ValueError, match="does not exist"):
        scenario.validate_lane_links(source, "target", "4->-1")


def test_web_rejects_invalid_road_updates_without_partial_mutation(monkeypatch):
    scenario = web_server.WebScenario()
    scenario.map.load_editable(
        [MapPolyline(name="road_1", points=[(0.0, 0.0), (10.0, 0.0)])], None
    )
    monkeypatch.setattr(web_server, "scenario", scenario)

    with pytest.raises(web_server.HTTPException, match="Lane types must use"):
        web_server.update_map_road(0, {"lane_type_spec": "not-a-lane-type"})

    road = scenario.map.roads[0]
    assert road.lane_types == {}
    assert road.points == [(0.0, 0.0), (10.0, 0.0)]


def test_web_timing_edits_forward_and_backward_multi_waypoint():
    scenario = web_server.WebScenario()
    scenario.vehicles["vehicle_2"] = [
        web_server.Waypoint(float(i + 10), float(i * 10), 0.0, speed_mps=10.0)
        for i in range(10)
    ]

    scenario.settings["trajectory_calculation_mode"] = "forward"
    scenario.set_waypoint_time("vehicle_2", 5, 13.0)
    assert scenario.vehicles["vehicle_2"][5].time_s == pytest.approx(13.0)
    assert all(
        following.time_s > previous.time_s
        for previous, following in zip(
            scenario.vehicles["vehicle_2"], scenario.vehicles["vehicle_2"][1:]
        )
    )

    scenario.set_waypoint_time("vehicle_2", 8, 14.5)
    assert scenario.vehicles["vehicle_2"][8].time_s == pytest.approx(14.5)
    assert all(
        following.time_s > previous.time_s
        for previous, following in zip(
            scenario.vehicles["vehicle_2"], scenario.vehicles["vehicle_2"][1:]
        )
    )

    scenario.settings["trajectory_calculation_mode"] = "backward"
    scenario.set_waypoint_time("vehicle_2", 5, 14.0)
    assert scenario.vehicles["vehicle_2"][5].time_s == pytest.approx(14.0)
    assert all(
        following.time_s > previous.time_s
        for previous, following in zip(
            scenario.vehicles["vehicle_2"], scenario.vehicles["vehicle_2"][1:]
        )
    )
    assert all(w.speed_mps >= 0.0 for w in scenario.vehicles["vehicle_2"])


def test_web_single_waypoint_time_edit():
    scenario = web_server.WebScenario()
    scenario.vehicles["vehicle_1"] = [
        web_server.Waypoint(0.0, 0.0, 0.0, speed_mps=0.0)
    ]

    scenario.set_waypoint_time("vehicle_1", 0, 2.5)

    assert scenario.vehicles["vehicle_1"][0].time_s == pytest.approx(2.5)


def test_web_add_waypoint_does_not_fail_with_negative_speed():
    scenario = web_server.WebScenario()
    scenario.add_actor("vehicle_test")
    for i in range(3, 10):
        scenario.add_waypoint("vehicle_test", float(i * 15), 0.0)

    assert len(scenario.vehicles["vehicle_test"]) == 10
    assert all(w.speed_mps >= 0.0 for w in scenario.vehicles["vehicle_test"])


def test_web_tutorial_intersection_conflict_car_and_cyclist():
    scenario = web_server.WebScenario()
    scenario.vehicles["car"] = [
        web_server.Waypoint(0.0, 0.0, 0.0, speed_mps=8.0),
        web_server.Waypoint(1.0, 16.0, 0.0, speed_mps=8.0),
        web_server.Waypoint(2.0, 32.0, 0.0, speed_mps=8.0),
    ]
    scenario.vehicles["cyclist"] = [
        web_server.Waypoint(0.0, 0.0, 0.0, speed_mps=5.0),
        web_server.Waypoint(3.0, 15.0, 0.0, speed_mps=5.0),
        web_server.Waypoint(8.0, 40.0, 0.0, speed_mps=5.0),
    ]

    scenario.settings["trajectory_calculation_mode"] = "forward"
    scenario.set_waypoint_time("car", 1, 4.0)
    assert scenario.vehicles["car"][1].time_s == 4.0

    scenario.settings["trajectory_calculation_mode"] = "backward"
    scenario.set_waypoint_time("cyclist", 1, 6.0)
    assert scenario.vehicles["cyclist"][1].time_s == 6.0
    assert scenario.vehicles["cyclist"][2].time_s == 8.0
    assert scenario.vehicles["cyclist"][0].time_s == pytest.approx(4.5)
