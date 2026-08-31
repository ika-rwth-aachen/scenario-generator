import math

import pytest

from scenario_generator.map.map import LaneCrossSection, imported_lane_profile_runs
from scenario_generator.scenario_elements.road_user.trajectory import (
    Trajectory,
    Waypoint,
    interpolate_trajectory,
)


def test_sample_count_enforces_increasing_times():
    with pytest.raises(ValueError, match="unique and increasing"):
        Trajectory.sample_count_for_segment(1.0, 1.0)


def test_trajectory_from_waypoints_samples_straight_path():
    trajectory = Trajectory(
        waypoints=[Waypoint(0.0, 0.0, 0.0), Waypoint(1.0, 10.0, 0.0)],
    ).as_moving_series()

    assert trajectory["time_s"][0] == 0.0
    assert trajectory["time_s"][-1] == 1.0
    assert trajectory["x_m"][0] == pytest.approx(0.0)
    assert trajectory["x_m"][-1] == pytest.approx(10.0)
    assert all(abs(y) < 1e-9 for y in trajectory["y_m"])
    assert trajectory["speed_mps"][0] == 0.0
    assert trajectory["speed_mps"][-1] == pytest.approx(20.0, rel=0.05)
    assert trajectory["yaw_rad"][0] == pytest.approx(0.0)


def test_trajectory_rejects_duplicate_times_and_too_few_points():
    with pytest.raises(ValueError, match="at least two"):
        Trajectory(waypoints=[Waypoint(0.0, 0.0, 0.0)]).as_moving_series()
    with pytest.raises(ValueError, match="unique and increasing"):
        Trajectory(
            waypoints=[Waypoint(0.0, 0.0, 0.0), Waypoint(0.0, 1.0, 1.0)],
        ).as_moving_series()


def test_interpolate_trajectory_clamps_and_interpolates_yaw_wrap():
    trajectory = {
        "time_s": [0.0, 1.0],
        "x_m": [0.0, 10.0],
        "y_m": [1.0, 3.0],
        "yaw_rad": [math.radians(170.0), math.radians(-170.0)],
    }

    assert interpolate_trajectory(trajectory, -1.0) == (0.0, 1.0, math.radians(170.0))
    assert interpolate_trajectory(trajectory, 2.0) == (10.0, 3.0, math.radians(-170.0))
    x_m, y_m, yaw = interpolate_trajectory(trajectory, 0.5)
    assert x_m == pytest.approx(5.0)
    assert y_m == pytest.approx(2.0)
    assert abs(abs(yaw) - math.pi) < 1e-9


def test_sampled_points_and_speeds_cover_multi_segment_curve():
    waypoints = [
        Waypoint(0.0, 0.0, 0.0),
        Waypoint(1.0, 5.0, 5.0),
        Waypoint(2.0, 10.0, 0.0),
    ]
    trajectory = Trajectory(waypoints=waypoints)
    points = trajectory.sampled_curve_points()
    averages = trajectory.segment_average_speeds()
    speeds = Trajectory.smooth_waypoint_speeds(averages)

    assert points[0][0] == 0.0
    assert points[-1][0] == 2.0
    assert len(averages) == 2
    assert len(speeds) == 3
    assert all(speed >= 0.0 for speed in speeds)


def test_explicit_point_speeds_derive_times_and_reject_zero_speed_segment():
    waypoints = [
        Waypoint(9.0, 0.0, 0.0, speed_mps=0.0),
        Waypoint(10.0, 10.0, 0.0, speed_mps=10.0),
    ]
    trajectory = Trajectory(waypoints=waypoints)
    trajectory.synchronize_times_from_speeds()
    series = trajectory.as_moving_series()

    assert waypoints[0].time_s == 0.0
    assert waypoints[1].time_s == pytest.approx(2.0)
    assert series["speed_mps"][0] == 0.0
    assert series["speed_mps"][-1] == pytest.approx(10.0)

    waypoints[1].speed_mps = 0.0
    with pytest.raises(ValueError, match="zero speed"):
        trajectory.synchronize_times_from_speeds()


def test_speed_profile_time_sync_keeps_spatial_waypoint_order():
    waypoints = [
        Waypoint(0.0, 0.0, 0.0, speed_mps=0.0),
        Waypoint(1.0, 20.0, 0.0, speed_mps=2.0),
        Waypoint(2.0, 20.0, 5.0, speed_mps=2.0),
    ]

    Trajectory(waypoints=waypoints).synchronize_times_from_speeds()

    assert [(point.x_m, point.y_m) for point in waypoints] == [
        (0.0, 0.0),
        (20.0, 0.0),
        (20.0, 5.0),
    ]
    assert waypoints[1].time_s > 2.0
    assert waypoints[2].time_s > waypoints[1].time_s


def test_anchored_speed_profile_clamps_negative_following_speed():
    speeds, adjusted = Trajectory.anchored_waypoint_speeds([2.0], 10.0)

    assert speeds == [10.0, 0.0]
    assert adjusted is True


def test_imported_lane_runs_do_not_bridge_sections_where_lane_is_absent():
    profiles = [
        LaneCrossSection(0.0, 0.0, 0.0, 0.0, 0.0, {1: 2.0}, {1: "driving"}),
        LaneCrossSection(1.0, 1.0, 0.0, 0.0, 0.0, {}, {}),
        LaneCrossSection(2.0, 2.0, 0.0, 0.0, 0.0, {}, {}),
        LaneCrossSection(3.0, 3.0, 0.0, 0.0, 0.0, {1: 2.0}, {1: "driving"}),
        LaneCrossSection(4.0, 4.0, 0.0, 0.0, 0.0, {1: 2.0}, {1: "driving"}),
    ]

    runs = imported_lane_profile_runs(profiles, 1)

    assert [[profile.s_m for profile in run] for _lane_type, run in runs] == [
        [0.0, 1.0],
        [2.0, 3.0, 4.0],
    ]
