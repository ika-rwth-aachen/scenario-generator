"""Velocity laws must drive the sampled positions, not just speed metadata."""

import math

import pytest

from scenario_generator.scenario_elements.road_user.trajectory import (
    TRAJECTORY_INTERPOLATION_MODES,
    Trajectory,
    Waypoint,
)


def playback_speeds(series):
    return [
        math.hypot(x1 - x0, y1 - y0) / (t1 - t0)
        for x0, x1, y0, y1, t0, t1 in zip(
            series["x_m"], series["x_m"][1:],
            series["y_m"], series["y_m"][1:],
            series["time_s"], series["time_s"][1:],
        )
    ]


def cut_in_waypoints():
    # Regression geometry/timing from the reported cut-in, without map dependencies.
    return [
        Waypoint(0.0, 297.44393897258715, -416.0090096978443, 5.0),
        Waypoint(4.1563755829508064, 297.44393897258715, -436.79088761259834, 5.0),
        Waypoint(7.749940071433756, 297.2789044617326, -463.7421057574734, 10.0),
        Waypoint(8.810994159834227, 296.7394383460629, -476.9873268223912, 15.0),
        Waypoint(9.967949823151521, 294.0202210606875, -494.1225109942886, 15.0),
        Waypoint(10.9561082795422, 293.82671222561686, -507.94488947415914, 13.0),
        Waypoint(16.166313673411207, 293.88823218763423, -567.8622055354057, 10.0),
    ]


@pytest.mark.parametrize("mode", TRAJECTORY_INTERPOLATION_MODES)
def test_cut_in_modes_preserve_waypoints_and_expose_actual_motion(mode):
    waypoints = cut_in_waypoints()
    series = Trajectory(waypoints=waypoints, interpolation_mode=mode).as_moving_series()
    for point in waypoints:
        index = series["time_s"].index(point.time_s)
        assert series["x_m"][index] == pytest.approx(point.x_m)
        assert series["y_m"][index] == pytest.approx(point.y_m)
    speeds = playback_speeds(series)
    assert all(math.isfinite(speed) and speed >= 0.0 for speed in speeds)
    if mode in ("cubic_hermite", "catmull_rom"):
        assert min(speeds) >= 4.0
        assert max(speeds) <= 16.0
    else:
        assert min(speeds) >= 5.0 - 0.01
        assert max(speeds) <= 15.0 + 0.01


@pytest.mark.parametrize("mode", TRAJECTORY_INTERPOLATION_MODES)
def test_constant_target_speed_is_constant_motion_even_with_two_points(mode):
    series = Trajectory(
        [Waypoint(0.0, 0.0, 0.0, 5.0), Waypoint(2.0, 10.0, 0.0, 5.0)],
        interpolation_mode=mode,
    ).as_moving_series()
    assert playback_speeds(series) == pytest.approx([5.0] * (len(series["time_s"]) - 1))


@pytest.mark.parametrize("mode, quarter_distance, quarter_speed", [
    ("cubic_hermite", 0.625, 2.5),
    ("linear_velocity", 0.625, 2.5),
])
def test_minimum_acceleration_for_consistent_timing(mode, quarter_distance, quarter_speed):
    series = Trajectory(
        [Waypoint(0.0, 0.0, 0.0, 0.0), Waypoint(2.0, 10.0, 0.0, 10.0)],
        interpolation_mode=mode,
    ).as_moving_series()
    index = series["time_s"].index(0.5)
    assert series["x_m"][index] == pytest.approx(quarter_distance)
    assert series["speed_mps"][index] == pytest.approx(quarter_speed)


def test_hermite_preserves_imported_times_and_endpoint_speeds():
    series = Trajectory([
        Waypoint(3.0, 0.0, 0.0, 10.0), Waypoint(5.0, 10.0, 0.0, 10.0),
    ]).as_moving_series()
    assert series["time_s"][0] == 3.0
    assert series["time_s"][-1] == 5.0
    assert series["speed_mps"][0] == pytest.approx(10.0)
    assert series["speed_mps"][-1] == pytest.approx(10.0)
    assert series["speed_mps"][series["time_s"].index(4.0)] == pytest.approx(2.5)
    assert series["x_m"][-1] == pytest.approx(10.0)



@pytest.mark.parametrize("mode", TRAJECTORY_INTERPOLATION_MODES)
def test_modes_handle_stationary_geometry(mode):
    series = Trajectory(
        [Waypoint(0.0, 1.0, 2.0, 0.0), Waypoint(1.0, 1.0, 2.0, 0.0)],
        interpolation_mode=mode,
    ).as_moving_series()
    assert playback_speeds(series) == pytest.approx([0.0] * (len(series["time_s"]) - 1))


def test_unknown_interpolation_is_rejected():
    with pytest.raises(ValueError, match="Unsupported trajectory interpolation"):
        Trajectory([Waypoint(0, 0, 0), Waypoint(1, 1, 0)], interpolation_mode="invalid").as_series()


@pytest.mark.parametrize("mode", TRAJECTORY_INTERPOLATION_MODES)
def test_sampling_is_at_least_50_hz_with_exact_knots(mode):
    points = cut_in_waypoints()
    series = Trajectory(points, interpolation_mode=mode).as_series()
    assert max(b - a for a, b in zip(series["time_s"], series["time_s"][1:])) <= 0.02 + 1e-12
    if mode in ("cubic_hermite", "catmull_rom"):
        for point in points:
            assert series["speed_mps"][series["time_s"].index(point.time_s)] == pytest.approx(point.speed_mps)


@pytest.mark.parametrize("speeds", [(10, 10), (0, 20), (20, 0)])
def test_hermite_incompatible_timing_does_not_reverse(speeds):
    series = Trajectory([Waypoint(0, 0, 0, speeds[0]), Waypoint(10, 1, 0, speeds[1])]).as_series()
    assert min(series["speed_mps"]) >= 0
    assert all(b >= a - 1e-12 for a, b in zip(series["x_m"], series["x_m"][1:]))
    assert series["x_m"][-1] == pytest.approx(1)
    assert series["speed_mps"][0] == pytest.approx(speeds[0])
    assert series["speed_mps"][-1] == pytest.approx(speeds[1])


def test_hermite_endpoint_acceleration_is_not_forced_flat():
    series = Trajectory([Waypoint(0, 0, 0, 0), Waypoint(2, 10, 0, 10)]).as_series()
    accelerations = [(b-a)/(t1-t0) for a,b,t0,t1 in zip(
        series["speed_mps"], series["speed_mps"][1:], series["time_s"], series["time_s"][1:])]
    assert accelerations == pytest.approx([5] * len(accelerations))


def test_hermite_acceleration_is_shared_at_interior_waypoints():
    # Constant speed followed by acceleration used to create a derivative jump.
    series = Trajectory([Waypoint(0, 0, 0, 5), Waypoint(2, 10, 0, 5),
                         Waypoint(5, 40, 0, 15)]).as_series()
    index = series["time_s"].index(2)
    v = series["speed_mps"]
    h = series["time_s"][index + 1] - 2
    left = (3 * v[index] - 4 * v[index - 1] + v[index - 2]) / (2 * h)
    right = (-3 * v[index] + 4 * v[index + 1] - v[index + 2]) / (2 * h)
    assert left == pytest.approx(right, abs=0.005)
    assert abs(left) > 0.1  # Continuity is not achieved by flattening every knot.
    assert v[index] == pytest.approx(5)


@pytest.mark.parametrize("lengths,durations,speeds", [
    ([10, 30], [2, 3], [5, 5, 15]),
    ([1, 2, 1], [10, 3, 7], [20, 10, 0, 20]),
    ([10, 10], [1, 2], [0, 0, 0]),
])
@pytest.mark.parametrize("method", [Trajectory.continuous_hermite_profiles, Trajectory.catmull_rom_velocity_profiles])
def test_continuous_profiles_preserve_distance_speeds_and_acceleration(lengths, durations, speeds, method):
    profiles = method(lengths, durations, speeds)
    accelerations = []
    for i, profile in enumerate(profiles):
        duration = durations[i]
        def evaluate(u):
            if profile is None:
                return Trajectory.nonnegative_distance_speed(lengths[i], duration, speeds[i], speeds[i + 1], u)
            return (duration * sum(c * u ** (j + 1) / (j + 1) for j, c in enumerate(profile)),
                    Trajectory.polynomial_value(profile, u))
        assert evaluate(0) == pytest.approx((0, speeds[i]), abs=1e-9)
        assert evaluate(1) == pytest.approx((lengths[i], speeds[i + 1]), abs=1e-9)
        assert min(evaluate(j / 1000)[1] for j in range(1001)) >= -1e-8
        epsilon = 1e-9
        accelerations.append(((evaluate(epsilon)[1] - evaluate(0)[1]) / (epsilon * duration),
                              (evaluate(1)[1] - evaluate(1 - epsilon)[1]) / (epsilon * duration)))
    for left, right in zip(accelerations, accelerations[1:]):
        assert left[1] == pytest.approx(right[0], abs=0.001)


def test_shared_slopes_minimize_acceleration_energy_with_free_endpoints():
    lengths, durations, speeds = [10, 30], [2, 3], [5, 5, 15]
    profiles = Trajectory.continuous_hermite_profiles(lengths, durations, speeds)
    slopes = [profiles[0][1] / durations[0]] + [sum(j*c for j,c in enumerate(p)) / t for p,t in zip(profiles,durations)]
    def energy(values):
        total = 0
        for i, t in enumerate(durations):
            p = Trajectory.continuous_velocity_coefficients(lengths[i], t, speeds[i], speeds[i+1], values[i], values[i+1])
            total += sum(j*k*p[j]*p[k]/(j+k-1)/t for j in range(1,5) for k in range(1,5))
        return total
    minimum = energy(slopes)
    for i in range(len(slopes)):
        for delta in [-0.1, 0.1]:
            altered = slopes[:]
            altered[i] += delta
            assert energy(altered) > minimum


def test_catmull_rom_uses_time_aware_speed_tangents():
    profiles = Trajectory.catmull_rom_velocity_profiles([10, 30], [2, 3], [5, 5, 15])
    expected_slope = (15 - 5) / (2 + 3)
    assert sum(i*c for i,c in enumerate(profiles[0])) / 2 == pytest.approx(expected_slope)
    assert profiles[1][1] / 3 == pytest.approx(expected_slope)


@pytest.mark.parametrize("mode", TRAJECTORY_INTERPOLATION_MODES)
def test_displayed_speed_integrates_to_sampled_motion(mode):
    series = Trajectory([Waypoint(0, 0, 0, 5), Waypoint(2, 10, 0, 5),
                         Waypoint(5, 40, 0, 15)], interpolation_mode=mode).as_series()
    # At 50 Hz the trapezoidal integral of displayed speeds agrees with motion.
    for i, measured in enumerate(playback_speeds(series)):
        displayed_average = (series["speed_mps"][i] + series["speed_mps"][i + 1]) / 2
        assert measured == pytest.approx(displayed_average, abs=0.002)
