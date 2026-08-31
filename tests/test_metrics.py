import pytest

from scenario_generator.metrics import (
    format_thw,
    format_ttc,
    min_thw_targets_by_actor,
    min_ttc,
    min_ttc_by_actor,
    min_ttc_targets_by_actor,
    pairwise_thw,
    pairwise_ttc,
)
from scenario_generator.scenario_elements.road_user import (
    ActorState,
    actor_state_from_trajectory,
)
from scenario_generator.scenario_elements.road_user.road_user import VehicleDimensions


def test_pairwise_ttc_uses_constant_extrapolation():
    """Verify pairwise ttc uses constant extrapolation."""
    ego = ActorState(
        "ego",
        x_m=0.0,
        y_m=0.0,
        speed_mps=5.0,
        yaw_rad=0.0,
        collision_radius_m=1.0,
        length_m=4.0,
        width_m=2.0,
    )
    target = ActorState(
        "target",
        x_m=12.0,
        y_m=0.0,
        speed_mps=0.0,
        yaw_rad=0.0,
        collision_radius_m=1.0,
        length_m=4.0,
        width_m=2.0,
    )

    assert pairwise_ttc(ego, target) == pytest.approx(1.6)


def test_pairwise_ttc_returns_none_when_actors_do_not_close():
    """Verify pairwise ttc returns none when actors do not close."""
    ego = ActorState(
        "ego",
        x_m=0.0,
        y_m=0.0,
        speed_mps=1.0,
        yaw_rad=0.0,
        collision_radius_m=1.0,
        length_m=4.0,
        width_m=2.0,
    )
    target = ActorState(
        "target",
        x_m=12.0,
        y_m=0.0,
        speed_mps=2.0,
        yaw_rad=0.0,
        collision_radius_m=1.0,
        length_m=4.0,
        width_m=2.0,
    )

    assert pairwise_ttc(ego, target) is None


def test_min_ttc_returns_global_minimum_for_timestep():
    """Verify min ttc returns global minimum for timestep."""
    states = {
        "ego": ActorState("ego", 0.0, 0.0, 5.0, 0.0, 1.0, length_m=4.0, width_m=2.0),
        "near": ActorState("near", 12.0, 0.0, 0.0, 0.0, 1.0, length_m=4.0, width_m=2.0),
        "far": ActorState("far", 30.0, 0.0, 0.0, 0.0, 1.0, length_m=4.0, width_m=2.0),
    }

    assert min_ttc(states) == pytest.approx(1.6)
    assert min_ttc_by_actor(states)["ego"] == pytest.approx(1.6)
    assert min_ttc_targets_by_actor(states)["ego"] == ("near", pytest.approx(1.6))
    assert min_ttc_targets_by_actor(states)["near"] == ("ego", pytest.approx(1.6))
    assert format_ttc(min_ttc(states)) == "TTC: 1.60 s"
    assert format_ttc(None) == "TTC: --"


def test_ttc_uses_oriented_boxes_instead_of_collision_radii():
    """Circular bounds may overlap while the actor boxes remain separated."""
    ego = ActorState(
        "ego",
        x_m=0.0,
        y_m=0.0,
        speed_mps=5.0,
        yaw_rad=0.0,
        collision_radius_m=2.5,
        length_m=4.5,
        width_m=1.8,
    )
    side = ActorState(
        "side",
        x_m=0.0,
        y_m=3.0,
        speed_mps=5.0,
        yaw_rad=0.0,
        collision_radius_m=2.5,
        length_m=4.5,
        width_m=1.8,
    )

    assert pairwise_ttc(ego, side) is None


def test_pairwise_thw_extrapolates_only_source_actor():
    """Verify pairwise thw extrapolates only source actor."""
    ego = ActorState(
        "ego",
        x_m=0.0,
        y_m=0.0,
        speed_mps=5.0,
        yaw_rad=0.0,
        collision_radius_m=1.0,
        length_m=4.0,
        width_m=2.0,
    )
    target = ActorState(
        "target",
        x_m=12.0,
        y_m=0.0,
        speed_mps=100.0,
        yaw_rad=0.0,
        collision_radius_m=1.0,
        length_m=4.0,
        width_m=2.0,
    )

    assert pairwise_thw(ego, target) == pytest.approx(1.6)
    assert pairwise_thw(target, ego) is None


def test_min_thw_targets_are_directed():
    """Verify min thw targets are directed."""
    states = {
        "ego": ActorState("ego", 0.0, 0.0, 5.0, 0.0, 1.0, length_m=4.0, width_m=2.0),
        "near": ActorState("near", 12.0, 0.0, 0.0, 0.0, 1.0, length_m=4.0, width_m=2.0),
        "side": ActorState("side", 0.0, 4.0, 0.0, 0.0, 1.0, length_m=4.0, width_m=2.0),
    }

    assert min_thw_targets_by_actor(states)["ego"] == ("near", pytest.approx(1.6))
    assert min_thw_targets_by_actor(states)["near"] is None
    assert format_thw(1.6) == "THW: 1.60 s"
    assert format_thw(None) == "THW: --"


def test_actor_state_from_trajectory_interpolates_current_timestep():
    """Verify actor state from trajectory interpolates current timestep."""
    trajectory = {
        "time_s": [0.0, 1.0],
        "x_m": [0.0, 10.0],
        "y_m": [0.0, 0.0],
        "speed_mps": [2.0, 4.0],
        "yaw_rad": [0.0, 0.0],
    }

    state = actor_state_from_trajectory(
        "ego",
        trajectory,
        0.5,
        VehicleDimensions(length_m=4.0, width_m=2.0),
    )

    assert state.x_m == pytest.approx(5.0)
    assert state.speed_mps == pytest.approx(3.0)
    assert state.collision_radius_m == pytest.approx(2.2360679)
    assert state.length_m == pytest.approx(4.0)
    assert state.width_m == pytest.approx(2.0)
