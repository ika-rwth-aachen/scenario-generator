"""Tests for the bundled postprocessing scripts."""

import xml.etree.ElementTree as ET

from scenario_generator.postprocessing_scripts.set_stop_simulation_time import run


def test_set_stop_simulation_time_changes_only_stop_triggers(tmp_path):
    """Keep start conditions and trajectory timestamps unchanged."""
    xosc_path = tmp_path / "scenario.xosc"
    xosc_path.write_text(
        """\
<OpenSCENARIO>
  <Storyboard>
    <Story>
      <Act>
        <StartTrigger>
          <SimulationTimeCondition value="0" rule="greaterThan" />
        </StartTrigger>
        <ManeuverGroup>
          <Maneuver>
            <Event>
              <Action>
                <PrivateAction>
                  <RoutingAction>
                    <FollowTrajectoryAction>
                      <TrajectoryRef>
                        <Trajectory>
                          <Shape>
                            <Polyline>
                              <Vertex time="5" />
                              <Vertex time="7" />
                            </Polyline>
                          </Shape>
                        </Trajectory>
                      </TrajectoryRef>
                    </FollowTrajectoryAction>
                  </RoutingAction>
                </PrivateAction>
              </Action>
              <StartTrigger>
                <SimulationTimeCondition value="5" rule="greaterThan" />
              </StartTrigger>
            </Event>
          </Maneuver>
        </ManeuverGroup>
        <StopTrigger>
          <SimulationTimeCondition value="7" rule="greaterThan" />
        </StopTrigger>
      </Act>
    </Story>
    <StopTrigger>
      <SimulationTimeCondition value="7" rule="greaterThan" />
    </StopTrigger>
  </Storyboard>
</OpenSCENARIO>
""",
        encoding="utf-8",
    )

    assert run(tmp_path, {"simulation_time_s": 30.0}) is True

    root = ET.parse(xosc_path).getroot()
    assert (
        root.find("./Storyboard/Story/Act/StartTrigger/SimulationTimeCondition").get(
            "value"
        )
        == "0"
    )
    assert (
        root.find(".//Event/StartTrigger/SimulationTimeCondition").get("value") == "5"
    )
    assert (
        root.find("./Storyboard/Story/Act/StopTrigger/SimulationTimeCondition").get(
            "value"
        )
        == "30"
    )
    assert (
        root.find("./Storyboard/StopTrigger/SimulationTimeCondition").get("value")
        == "30"
    )
    assert [vertex.get("time") for vertex in root.findall(".//Vertex")] == ["5", "7"]
