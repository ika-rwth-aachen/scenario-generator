"""OpenSCENARIO XML exporter for trajectory-based scenario projects.

The exporter converts sampled actor trajectories plus optional map, controller and environment metadata into an OpenSCENARIO 1.0/1.1 document. It keeps XML
construction in small helper methods so actor/entity creation, Init actions and
Storyboard motion actions can evolve independently.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from defusedxml.ElementTree import fromstring as safe_xml_fromstring

from scenario_generator.config.settings import (
    load_actor_dimensions,
    load_actor_xosc_defaults,
    load_default_xosc_rev_minor,
    load_simulation_time_condition_factor,
)
from scenario_generator.geometry_utils import heading_between
from scenario_generator.io.importer_exporter.base import Exporter
from scenario_generator.scenario_elements.road_user.road_user import is_ego_vehicle_name

AUTHOR = "Author"


def current_datetime_string() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat(timespec="seconds")


def format_float(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


class OpenScenarioExporter(Exporter):
    """Build and write OpenSCENARIO file from sampled trajectories."""

    default_suffix = ".xosc"
    format_name = "OpenSCENARIO"

    def __init__(self, author: str = AUTHOR):
        self.author = author
        self.current_rev_minor = load_default_xosc_rev_minor()

    def export_file(
        self,
        output_path: Path,
        trajectories: dict[str, dict[str, Any]],
        **options: Any,
    ):
        """Write a complete OpenSCENARIO XML file to ``output_path``."""
        self.export(
            trajectories,
            output_path,
            road_logic_file=options.get("road_logic_file"),
            additional_scenario_information=options.get(
                "additional_scenario_information",
            ),
        )

    def file_header_for_additional_information(
        self,
        additional_scenario_information: dict[str, object] | None,
    ) -> tuple[dict[str, str], int]:
        """Resolve FileHeader metadata and validate the OSC minor version.

        The GUI stores optional metadata under ``file_header``. Only supported
        OpenSCENARIO versions are accepted so users get an actionable error
        before a file with an unsupported schema is written.
        """
        file_header = {
            "date": "1970-01-01T00:00:00",
            "description": "Scenario created with scenario.generator",
            "author": self.author,
        }
        rev_minor = load_default_xosc_rev_minor()
        if additional_scenario_information:
            raw_file_header = additional_scenario_information.get("file_header")
            if isinstance(raw_file_header, dict):
                for key in ("date", "description", "author"):
                    value = raw_file_header.get(key)
                    if value:
                        file_header[key] = str(value)
                raw_rev_minor = raw_file_header.get(
                    "revMinor",
                    raw_file_header.get("rev_minor", load_default_xosc_rev_minor()),
                )
                try:
                    rev_minor = int(raw_rev_minor)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Unsupported OpenSCENARIO version 1.{raw_rev_minor}. Choose another version in Additional scenario information.",
                    ) from exc
        if rev_minor not in (0, 1):
            raise ValueError(
                f"Unsupported OpenSCENARIO version 1.{rev_minor}. Choose another version in Additional scenario information.",
            )
        return file_header, rev_minor

    def export(
        self,
        trajectories: dict[str, dict[str, object]],
        output_path: Path,
        road_logic_file: str | None = None,
        additional_scenario_information: dict[str, object] | None = None,
    ):
        """Write a complete OpenSCENARIO XML file to ``output_path``."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tree = self.build_xml(
            trajectories,
            road_logic_file=road_logic_file,
            additional_scenario_information=additional_scenario_information,
        )
        tree.write(output_path, encoding="utf-8", xml_declaration=True)

    def build_xml(
        self,
        trajectories: dict[str, dict[str, object]],
        road_logic_file: str | None = None,
        additional_scenario_information: dict[str, object] | None = None,
    ) -> ET.ElementTree:
        """Build the complete OpenSCENARIO XML document.

        Args:
                trajectories: Actor trajectory dictionaries keyed by entity name.
                road_logic_file: Optional OpenDRIVE file path referenced by RoadNetwork.
                additional_scenario_information: Optional header and environment metadata.

        """
        if not trajectories:
            raise ValueError("At least one vehicle trajectory is required.")

        root = self.create_scenario_root(trajectories, additional_scenario_information)
        self.add_road_network(root, road_logic_file)
        self.add_entities(root, trajectories)
        self.add_storyboard(root, trajectories, additional_scenario_information)

        ET.indent(root, space="  ")
        return ET.ElementTree(root)

    def create_scenario_root(
        self,
        trajectories: dict[str, dict[str, object]],
        additional_scenario_information: dict[str, object] | None,
    ) -> ET.Element:
        """Create the OpenSCENARIO root and static top-level sections.

        Args:
                additional_scenario_information: Optional header metadata from the GUI.

        """
        root = ET.Element("OpenSCENARIO")
        file_header, rev_minor = self.file_header_for_additional_information(
            additional_scenario_information,
        )
        self.current_rev_minor = rev_minor
        self.sub(
            root,
            "FileHeader",
            revMajor="1",
            revMinor=str(rev_minor),
            date=current_datetime_string(),
            description=file_header["description"],
            author=file_header["author"],
        )
        self.sub(root, "ParameterDeclarations")
        self.sub(root, "CatalogLocations")
        return root

    def add_parameter_declarations(
        self,
        parent: ET.Element,
        trajectory: dict[str, object],
    ):
        """Append one actor's validated declarations to its maneuver."""
        raw_declarations = self.parameter_declarations_for_trajectory(trajectory)
        if not raw_declarations:
            return
        wrapper = safe_xml_fromstring(
            f"<ParameterDeclarations>{raw_declarations}</ParameterDeclarations>"
        )
        declarations = self.sub(parent, "ParameterDeclarations")
        for declaration in wrapper:
            if self.local_name(declaration.tag) != "ParameterDeclaration":
                raise ValueError("Parameter declarations must contain only ParameterDeclaration elements.")
            declarations.append(declaration)

    def add_road_network(self, root: ET.Element, road_logic_file: str | None):
        """Append the RoadNetwork section.

        Args:
                root: OpenSCENARIO root element.
                road_logic_file: Optional OpenDRIVE file path referenced by LogicFile.

        """
        road_network = self.sub(root, "RoadNetwork")
        if road_logic_file:
            self.sub(road_network, "LogicFile", filepath=road_logic_file)

    def add_entities(
        self,
        root: ET.Element,
        trajectories: dict[str, dict[str, object]],
    ):
        """Append all ScenarioObject entities.

        Args:
                root: OpenSCENARIO root element.
                trajectories: Actor trajectory dictionaries keyed by entity name.

        """
        entities = self.sub(root, "Entities")
        for entity_name, trajectory in trajectories.items():
            self.add_actor(entities, entity_name, trajectory)

    def add_storyboard(
        self,
        root: ET.Element,
        trajectories: dict[str, dict[str, object]],
        additional_scenario_information: dict[str, object] | None,
    ):
        """Append Init, optional motion story, and storyboard stop trigger.

        Args:
                root: OpenSCENARIO root element.
                trajectories: Actor trajectory dictionaries keyed by entity name.
                additional_scenario_information: Optional environment metadata.

        """
        storyboard = self.sub(root, "Storyboard")
        self.add_init_section(storyboard, trajectories, additional_scenario_information)

        motion_trajectories = self.motion_trajectories(trajectories)
        spawn_trajectories = self.spawn_trajectories(trajectories)
        simulation_time_condition_factor = (
            self.simulation_time_condition_factor_for_additional_information(
                additional_scenario_information,
            )
        )
        if motion_trajectories or spawn_trajectories:
            self.add_motion_story(
                storyboard,
                motion_trajectories,
                spawn_trajectories,
                simulation_time_condition_factor,
            )
        self.add_storyboard_stop_trigger(
            storyboard,
            trajectories,
            simulation_time_condition_factor,
        )

    def add_init_section(
        self,
        storyboard: ET.Element,
        trajectories: dict[str, dict[str, object]],
        additional_scenario_information: dict[str, object] | None,
    ) -> ET.Element:
        """Append storyboard Init actions.

        Args:
                storyboard: Storyboard element that receives Init.
                trajectories: Actor trajectory dictionaries keyed by entity name.
                additional_scenario_information: Optional environment metadata.

        """
        init = self.sub(storyboard, "Init")
        actions = self.sub(init, "Actions")

        # Environment is a global init action and must precede per-actor initialization.
        if additional_scenario_information:
            raw_environment = additional_scenario_information.get("environment")
            if isinstance(raw_environment, dict):
                self.add_environment_action(actions, raw_environment)

        for entity_name, trajectory in trajectories.items():
            if self.trajectory_start_time(trajectory) <= 0:
                self.add_teleport_action(actions, entity_name, trajectory)
                self.add_init_action(actions, entity_name, trajectory)
        return actions

    @staticmethod
    def trajectory_start_time(trajectory: dict[str, object]) -> float:
        """Return the first timestamp of an actor trajectory."""
        time_values = trajectory.get("time_s", [])
        if not isinstance(time_values, (list, tuple)) or not time_values:
            raise ValueError("Actor trajectories require at least one time_s value.")
        return float(time_values[0])

    def spawn_trajectories(
        self,
        trajectories: dict[str, dict[str, object]],
    ) -> list[tuple[str, dict[str, object]]]:
        """Return actors that must be added after scenario initialization."""
        return [
            (entity_name, trajectory)
            for entity_name, trajectory in trajectories.items()
            if self.trajectory_start_time(trajectory) > 0
        ]

    def motion_trajectories(
        self,
        trajectories: dict[str, dict[str, object]],
    ) -> list[tuple[str, dict[str, object]]]:
        """Return actors that need a storyboard motion action.

        Args:
                trajectories: Actor trajectory dictionaries keyed by entity name.

        """
        return [
            (entity_name, trajectory)
            for entity_name, trajectory in trajectories.items()
            if self.xosc_export_mode_for_trajectory(trajectory) != "clear_trajectory"
            and len(trajectory["time_s"]) > 1
        ]

    def add_motion_story(
        self,
        storyboard: ET.Element,
        motion_trajectories: list[tuple[str, dict[str, object]]],
        spawn_trajectories: list[tuple[str, dict[str, object]]],
        simulation_time_condition_factor: float,
    ):
        """Append the Story/Act that spawns and moves actors.

        Args:
                storyboard: Storyboard element that receives the motion story.
                motion_trajectories: Filtered actors that need motion actions.
                spawn_trajectories: Actors created after scenario initialization.

        """
        story = self.sub(storyboard, "Story", name="trajectory_story")
        act = self.sub(story, "Act", name="trajectory_act")
        for entity_name, trajectory in spawn_trajectories:
            self.add_spawn_action(act, entity_name, trajectory)
        for entity_name, trajectory in motion_trajectories:
            spawn_event_name = (
                f"{entity_name}_spawn_event"
                if self.trajectory_start_time(trajectory) > 0
                else None
            )
            self.add_motion_action(act, entity_name, trajectory, spawn_event_name)
        self.add_act_start_trigger(act)
        all_story_trajectories = {
            entity_name: trajectory
            for entity_name, trajectory in motion_trajectories + spawn_trajectories
        }
        self.add_act_stop_trigger(
            act,
            list(all_story_trajectories.items()),
            simulation_time_condition_factor,
        )

    def add_act_start_trigger(self, act: ET.Element):
        """Append an immediate start trigger to a motion act.

        Args:
                act: Act element that receives StartTrigger.

        """
        self.add_simulation_time_trigger(
            self.sub(act, "StartTrigger"),
            name="act_start",
            value="0",
            rule="greaterThan",
        )

    def add_act_stop_trigger(
        self,
        act: ET.Element,
        motion_trajectories: list[tuple[str, dict[str, object]]],
        simulation_time_condition_factor: float,
    ):
        """Append a stop trigger at the final motion timestamp.

        Args:
                act: Act element that receives StopTrigger.
                motion_trajectories: Filtered actors that determine the motion duration.

        """
        stop_time = simulation_time_condition_factor * max(
            float(trajectory["time_s"][-1]) for _, trajectory in motion_trajectories
        )
        self.add_simulation_time_trigger(
            self.sub(act, "StopTrigger"),
            name="act_stop",
            value=format_float(stop_time),
            rule="greaterThan",
        )

    def add_storyboard_stop_trigger(
        self,
        storyboard: ET.Element,
        trajectories: dict[str, dict[str, object]],
        simulation_time_condition_factor: float,
    ):
        """Append the final storyboard stop trigger.

        Args:
                storyboard: Storyboard element that receives StopTrigger.
                trajectories: Actor trajectories used to determine the scenario duration.

        """
        max_time = simulation_time_condition_factor * max(
            float(trajectory["time_s"][-1]) for trajectory in trajectories.values()
        )
        self.add_simulation_time_trigger(
            self.sub(storyboard, "StopTrigger"),
            name="storyboard_stop",
            value=format_float(max_time),
            rule="greaterThan",
        )

    @staticmethod
    def simulation_time_condition_factor_for_additional_information(
        additional_scenario_information: dict[str, object] | None,
    ) -> float:
        """Return the configured duration multiplier for XOSC stop triggers."""
        if not additional_scenario_information:
            return load_simulation_time_condition_factor()
        raw_factor = additional_scenario_information.get(
            "simulation_time_condition_factor",
            additional_scenario_information.get(
                "simulation_time_factor",
                load_simulation_time_condition_factor(),
            ),
        )
        try:
            return max(float(raw_factor), 1.0)
        except (TypeError, ValueError):
            return load_simulation_time_condition_factor()

    def add_simulation_time_trigger(
        self,
        parent: ET.Element,
        name: str,
        value: str,
        rule: str,
    ):
        """Append the common ConditionGroup/Condition/SimulationTimeCondition chain.

        Args:
                parent: StartTrigger or StopTrigger element.
                name: Condition name attribute.
                value: Simulation time threshold.
                rule: OpenSCENARIO comparison rule.

        """
        condition_group = self.sub(parent, "ConditionGroup")
        condition = self.sub(
            condition_group,
            "Condition",
            name=name,
            delay="0",
            conditionEdge="rising",
        )
        by_value_condition = self.sub(condition, "ByValueCondition")
        self.sub(by_value_condition, "SimulationTimeCondition", value=value, rule=rule)

    def add_environment_action(
        self,
        actions: ET.Element,
        environmental_conditions: dict[str, object],
    ):
        """Append optional time-of-day, weather and road-condition metadata."""
        global_action = self.sub(actions, "GlobalAction")
        environment_action = self.sub(global_action, "EnvironmentAction")
        environment = self.sub(
            environment_action,
            "Environment",
            name=environmental_conditions.get("name", "environment"),
        )
        self.sub(
            environment,
            "TimeOfDay",
            animation="false",
            dateTime=environmental_conditions.get("time_of_day", "2026-06-16T12:00:00"),
        )
        weather = self.sub(
            environment,
            "Weather",
            cloudState=environmental_conditions.get("cloud_state", "free"),
        )
        self.sub(
            weather,
            "Sun",
            intensity=format_float(
                float(environmental_conditions.get("sun_intensity", 1.0)),
            ),
            azimuth=format_float(
                float(environmental_conditions.get("sun_azimuth", 0.0)),
            ),
            elevation=format_float(
                float(environmental_conditions.get("sun_elevation", 1.0)),
            ),
        )
        self.sub(
            weather,
            "Fog",
            visualRange=format_float(
                float(environmental_conditions.get("fog_visual_range", 100000.0)),
            ),
        )
        self.sub(
            weather,
            "Precipitation",
            precipitationType=environmental_conditions.get("precipitation_type", "dry"),
            intensity=format_float(
                float(environmental_conditions.get("precipitation_intensity", 0.0)),
            ),
        )
        self.sub(
            environment,
            "RoadCondition",
            frictionScaleFactor=format_float(
                float(environmental_conditions.get("road_friction", 1.0)),
            ),
        )

    @staticmethod
    def sub(parent: ET.Element, tag: str, **attrs: object) -> ET.Element:
        """Create a child XML element and stringify all attributes."""
        return ET.SubElement(
            parent,
            tag,
            {key: str(value) for key, value in attrs.items()},
        )

    @staticmethod
    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def actor_type_for_trajectory(
        self,
        trajectory: dict[str, object] | None = None,
    ) -> str:
        """Normalize trajectory metadata into vehicle, cyclist or pedestrian."""
        if not trajectory:
            return "vehicle"
        raw_actor_type = trajectory.get("actor_type")
        if not raw_actor_type:
            raw_dimensions = trajectory.get("dimensions")
            if isinstance(raw_dimensions, dict):
                raw_actor_type = raw_dimensions.get("actor_type")
        actor_type = str(raw_actor_type or "vehicle").lower()
        if actor_type in {"car", "vehicle"}:
            return "vehicle"
        if actor_type in {"bike", "bicycle", "cyclist"}:
            return "cyclist"
        if actor_type in {"pedestrian", "walker", "person"}:
            return "pedestrian"
        return "vehicle"

    def carla_blueprint_for_trajectory(
        self,
        trajectory: dict[str, object] | None = None,
    ) -> str | None:
        """Return the optional CARLA blueprint stored with trajectory metadata."""
        if not trajectory:
            return None
        blueprint = trajectory.get("carla_blueprint")
        if blueprint:
            return str(blueprint)
        raw_dimensions = trajectory.get("dimensions")
        if isinstance(raw_dimensions, dict):
            blueprint = raw_dimensions.get("carla_blueprint")
            if blueprint:
                return str(blueprint)
        actor_type = self.actor_type_for_trajectory(trajectory)
        dimensions = load_actor_dimensions(actor_type)
        blueprint = dimensions.get("carla_blueprint")
        return str(blueprint) if blueprint else None

    def vehicle_dimensions(
        self,
        trajectory: dict[str, object] | None = None,
    ) -> dict[str, float]:
        """Return the physical vehicle dimensions for one trajectory."""
        raw_dimensions = trajectory.get("dimensions") if trajectory else None
        if isinstance(raw_dimensions, dict):
            return {
                "length_m": float(
                    raw_dimensions.get("length_m", raw_dimensions.get("length", 4.5)),
                ),
                "width_m": float(
                    raw_dimensions.get("width_m", raw_dimensions.get("width", 1.8)),
                ),
                "height_m": float(
                    raw_dimensions.get("height_m", raw_dimensions.get("height", 1.8)),
                ),
            }
        dimensions = load_actor_dimensions(self.actor_type_for_trajectory(trajectory))
        return {
            "length_m": float(dimensions.get("length_m", 4.5)),
            "width_m": float(dimensions.get("width_m", 1.8)),
            "height_m": float(dimensions.get("height_m", 1.8)),
        }

    def xosc_export_mode_for_trajectory(
        self,
        trajectory: dict[str, object] | None = None,
    ) -> str:
        """Normalize the requested OpenSCENARIO motion mode."""
        if not trajectory:
            return "trajectory"
        raw_mode = trajectory.get("xosc_export_mode")
        if not raw_mode:
            raw_dimensions = trajectory.get("dimensions")
            if isinstance(raw_dimensions, dict):
                raw_mode = raw_dimensions.get("xosc_export_mode")
        mode = str(raw_mode or "trajectory").strip().lower().replace("-", "_")
        if mode in {"reach_position", "clear_trajectory", "route", "trajectory"}:
            return mode
        return mode.replace(" ", "_")

    def parameter_declarations_for_trajectory(
        self,
        trajectory: dict[str, object] | None = None,
    ) -> str:
        """Return configured ParameterDeclaration XML for one actor."""
        if not trajectory:
            return ""
        declarations = trajectory.get("parameter_declarations")
        if declarations:
            return str(declarations).strip()
        raw_dimensions = trajectory.get("dimensions")
        if isinstance(raw_dimensions, dict):
            declarations = raw_dimensions.get("parameter_declarations")
            if declarations:
                return str(declarations).strip()
        return ""

    def controller_name_for_trajectory(
        self,
        trajectory: dict[str, object] | None = None,
    ) -> str | None:
        """Return the controller name if one was configured."""
        if not trajectory:
            return None
        controller_name = trajectory.get("controller_name")
        if controller_name:
            return str(controller_name)
        raw_dimensions = trajectory.get("dimensions")
        if isinstance(raw_dimensions, dict):
            controller_name = raw_dimensions.get("controller_name")
            if controller_name:
                return str(controller_name)
        return None

    def controller_xml_for_trajectory(
        self,
        trajectory: dict[str, object] | None = None,
    ) -> str | None:
        """Return the raw controller XML template if one was configured."""
        if not trajectory:
            return None
        controller_xml = trajectory.get("controller_xml")
        if controller_xml:
            return str(controller_xml)
        raw_dimensions = trajectory.get("dimensions")
        if isinstance(raw_dimensions, dict):
            controller_xml = raw_dimensions.get("controller_xml")
            if controller_xml:
                return str(controller_xml)
        return None

    def controller_xml_element_for_trajectory(
        self,
        trajectory: dict[str, object] | None = None,
    ) -> ET.Element | None:
        """Parse and validate the controller template for one trajectory."""
        controller_xml = self.controller_xml_for_trajectory(trajectory)
        if not controller_xml:
            return None
        try:
            element = safe_xml_fromstring(controller_xml)
        except ET.ParseError as exc:
            raise ValueError("Controller XML template is invalid.") from exc
        local_name = self.local_name(element.tag)
        if local_name not in {"Controller", "ControllerAction"}:
            raise ValueError(
                "Controller XML template must be a Controller or ControllerAction element.",
            )
        return element

    def add_bounding_box(
        self,
        parent: ET.Element,
        actor_type: str,
        trajectory: dict[str, object],
    ):
        """Append a bounding box relative to the OpenSCENARIO reference point."""
        dimensions = self.vehicle_dimensions(trajectory)
        is_pedestrian = actor_type == "pedestrian"
        bounding_box = self.sub(parent, "BoundingBox")
        self.sub(
            bounding_box,
            "Center",
            x=format_float(0.0 if is_pedestrian else dimensions["length_m"] / 2.0),
            y="0",
            z=format_float(0.0 if is_pedestrian else dimensions["height_m"] / 2.0),
        )
        self.sub(
            bounding_box,
            "Dimensions",
            width=format_float(dimensions["width_m"]),
            length=format_float(dimensions["length_m"]),
            height=format_float(dimensions["height_m"]),
        )

    def add_vehicle_defaults(
        self,
        vehicle: ET.Element,
        actor_type: str,
        entity_name: str,
        trajectory: dict[str, object],
    ):
        """Append required XSD child elements for one exported vehicle."""
        defaults = load_actor_xosc_defaults(actor_type)
        self.add_bounding_box(
            vehicle,
            "cyclist" if actor_type == "cyclist" else "vehicle",
            trajectory,
        )
        self.sub(vehicle, "Performance", **defaults["performance"])
        axles = self.sub(vehicle, "Axles")
        self.sub(axles, "FrontAxle", **defaults["front_axle"])
        self.sub(axles, "RearAxle", **defaults["rear_axle"])
        properties = self.sub(vehicle, "Properties")
        if is_ego_vehicle_name(entity_name):
            self.sub(properties, "Property", name="type", value="ego_vehicle")

    def add_pedestrian_defaults(
        self,
        pedestrian: ET.Element,
        entity_name: str,
        trajectory: dict[str, object],
    ):
        """Append required XSD child elements for one exported pedestrian."""
        self.add_bounding_box(pedestrian, "pedestrian", trajectory)
        properties = self.sub(pedestrian, "Properties")
        if is_ego_vehicle_name(entity_name):
            self.sub(properties, "Property", name="type", value="ego_vehicle")

    def add_actor(
        self,
        parent: ET.Element,
        entity_name: str,
        trajectory: dict[str, object],
    ):
        """Append one scenario object with entity metadata."""
        scenario_object = self.sub(parent, "ScenarioObject", name=entity_name)

        actor_type = self.actor_type_for_trajectory(trajectory)
        if actor_type == "pedestrian":
            defaults = load_actor_xosc_defaults(actor_type)
            pedestrian = self.sub(
                scenario_object,
                "Pedestrian",
                name=entity_name,
                **defaults["attributes"],
            )
            self.add_pedestrian_defaults(pedestrian, entity_name, trajectory)
        else:
            defaults = load_actor_xosc_defaults(actor_type)
            vehicle = self.sub(
                scenario_object,
                "Vehicle",
                name=entity_name,
                **defaults["attributes"],
            )
            self.add_vehicle_defaults(vehicle, actor_type, entity_name, trajectory)

        controller_element = self.controller_xml_element_for_trajectory(trajectory)
        controller_name = self.controller_name_for_trajectory(trajectory)
        if controller_element is None:
            if controller_name:
                object_controller = self.sub(scenario_object, "ObjectController")
                self.sub(object_controller, "Controller", name=controller_name)
        elif self.local_name(controller_element.tag) == "Controller":
            object_controller = self.sub(scenario_object, "ObjectController")
            controller_copy = safe_xml_fromstring(
                ET.tostring(controller_element, encoding="unicode"),
            )
            if controller_name and not controller_copy.attrib.get("name"):
                controller_copy.set("name", controller_name)
            object_controller.append(controller_copy)
        else:
            # ControllerAction templates are attached during Init.
            pass

    def add_init_action(
        self,
        actions: ET.Element,
        entity_name: str,
        trajectory: dict[str, object],
    ):
        """Append initial speed and optional controller actions for an actor."""
        self.add_initial_speed_action(actions, entity_name, trajectory)
        controller_element = self.controller_xml_element_for_trajectory(trajectory)
        if controller_element is None:
            return
        if self.local_name(controller_element.tag) != "ControllerAction":
            return
        private = self.sub(actions, "Private", entityRef=entity_name)
        private_action = self.sub(private, "PrivateAction")
        private_action.append(
            safe_xml_fromstring(ET.tostring(controller_element, encoding="unicode")),
        )

    def add_initial_speed_action(
        self,
        actions: ET.Element,
        entity_name: str,
        trajectory: dict[str, object],
    ):
        """Set an actor's first trajectory speed immediately during Init."""
        speed_values = trajectory.get("speed_mps", [])
        initial_speed = (
            float(speed_values[0])
            if isinstance(speed_values, (list, tuple)) and speed_values
            else 0.0
        )
        private = self.sub(actions, "Private", entityRef=entity_name)
        private_action = self.sub(private, "PrivateAction")
        longitudinal_action = self.sub(private_action, "LongitudinalAction")
        speed_action = self.sub(longitudinal_action, "SpeedAction")
        self.sub(
            speed_action,
            "SpeedActionDynamics",
            dynamicsShape="step",
            value="0",
            dynamicsDimension="time",
        )
        speed_action_target = self.sub(speed_action, "SpeedActionTarget")
        self.sub(
            speed_action_target,
            "AbsoluteTargetSpeed",
            value=format_float(initial_speed),
        )

    def add_trajectory_position(
        self,
        parent: ET.Element,
        trajectory: dict[str, object],
        index: int,
    ):
        """Append one trajectory sample as an OpenSCENARIO world position."""
        position = self.sub(parent, "Position")
        self.sub(
            position,
            "WorldPosition",
            **self.world_position_attributes(trajectory, index),
        )

    def world_position_attributes(
        self,
        trajectory: dict[str, object],
        index: int,
        heading_rad: float | None = None,
    ) -> dict[str, str]:
        """Return an OSC entity-reference position for one GUI or external sample."""
        x_values = trajectory.get("x_m", [])
        y_values = trajectory.get("y_m", [])
        x_m = float(x_values[index])
        y_m = float(y_values[index])
        z_m = self.trajectory_z_at(trajectory, index)
        yaw_values = trajectory.get("yaw_rad", [])
        if heading_rad is None and isinstance(yaw_values, (list, tuple)) and yaw_values:
            heading_rad = self.trajectory_heading_at(trajectory, index)
        if trajectory.get("coordinate_reference") == "bounding_box_center":
            dimensions = self.vehicle_dimensions(trajectory)
            if self.actor_type_for_trajectory(trajectory) == "pedestrian":
                z_m += dimensions["height_m"] / 2.0
            else:
                heading = 0.0 if heading_rad is None else heading_rad
                center_offset = dimensions["length_m"] / 2.0
                x_m -= center_offset * math.cos(heading)
                y_m -= center_offset * math.sin(heading)
        attributes = {
            "x": format_float(x_m),
            "y": format_float(y_m),
            "z": format_float(z_m),
        }
        if heading_rad is not None:
            attributes["h"] = format_float(heading_rad)
        return attributes

    @staticmethod
    def trajectory_z_at(trajectory: dict[str, object], index: int) -> float:
        """Return one exported trajectory height, defaulting to the XY plane."""
        z_values = trajectory.get("z_m", [])
        if (
            isinstance(z_values, (list, tuple))
            and z_values
            and -len(z_values) <= index < len(z_values)
        ):
            return float(z_values[index])
        return 0.0

    @staticmethod
    def trajectory_movement_headings(
        trajectory: dict[str, object],
        rolling_window: int = 20,
        minimum_speed_mps: float = 0.5 / 3.6,
    ) -> list[float]:
        """Return headings aligned with the checker's smoothed XY movement."""
        x_values = [float(value) for value in trajectory.get("x_m", [])]
        y_values = [float(value) for value in trajectory.get("y_m", [])]
        time_values = [float(value) for value in trajectory.get("time_s", [])]
        yaw_values = trajectory.get("yaw_rad", [])
        local_headings: list[float] = []
        fallback = (
            float(yaw_values[0])
            if isinstance(yaw_values, (list, tuple)) and yaw_values
            else 0.0
        )
        for index in range(len(x_values)):
            fallback = heading_between(x_values, y_values, index, fallback)
            local_headings.append(fallback)
        if not (
            len(x_values) == len(y_values) == len(time_values)
            and len(x_values) > rolling_window
        ):
            return local_headings

        half_window = rolling_window // 2
        smoothed: list[float | None] = [None] * len(x_values)
        for index in range(len(x_values)):
            start = index - half_window
            end = start + rolling_window
            # diff() makes sample zero unavailable, matching pandas rolling().
            if start < 1 or end > len(x_values):
                continue
            delta_time = time_values[end - 1] - time_values[start - 1]
            if delta_time <= 0.0:
                continue
            delta_x = x_values[end - 1] - x_values[start - 1]
            delta_y = y_values[end - 1] - y_values[start - 1]
            speed_mps = math.hypot(delta_x, delta_y) / delta_time
            if speed_mps > minimum_speed_mps:
                smoothed[index] = math.atan2(delta_y, delta_x)

        last_heading: float | None = None
        for index, heading in enumerate(smoothed):
            if heading is not None:
                last_heading = heading
            elif last_heading is not None:
                smoothed[index] = last_heading
        first_heading = next(
            (heading for heading in smoothed if heading is not None),
            None,
        )
        if first_heading is None:
            return local_headings
        return [first_heading if heading is None else heading for heading in smoothed]

    @classmethod
    def trajectory_heading_at(cls, trajectory: dict[str, object], index: int) -> float:
        """Return one heading aligned with the exported XY movement."""
        headings = cls.trajectory_movement_headings(trajectory)
        if not headings:
            return 0.0
        return headings[index]

    def add_teleport_action(
        self,
        actions: ET.Element,
        entity_name: str,
        trajectory: dict[str, object],
    ):
        """Teleport an actor that exists when the scenario starts."""
        private = self.sub(actions, "Private", entityRef=entity_name)
        private_action = self.sub(private, "PrivateAction")
        teleport_action = self.sub(private_action, "TeleportAction")
        self.add_trajectory_position(teleport_action, trajectory, 0)

    def add_spawn_action(
        self,
        act: ET.Element,
        entity_name: str,
        trajectory: dict[str, object],
    ):
        """Add an actor at its first trajectory sample during runtime."""
        maneuver_group = self.sub(
            act,
            "ManeuverGroup",
            maximumExecutionCount="1",
            name=f"{entity_name}_spawn_maneuver_group",
        )
        actors = self.sub(maneuver_group, "Actors", selectTriggeringEntities="false")
        self.sub(actors, "EntityRef", entityRef=entity_name)
        maneuver = self.sub(
            maneuver_group,
            "Maneuver",
            name=f"{entity_name}_spawn_maneuver",
        )
        event = self.sub(
            maneuver,
            "Event",
            maximumExecutionCount="1",
            name=f"{entity_name}_spawn_event",
            priority="overwrite",
        )
        action = self.sub(event, "Action", name=f"{entity_name}_spawn_action")
        global_action = self.sub(action, "GlobalAction")
        entity_action = self.sub(global_action, "EntityAction", entityRef=entity_name)
        add_entity_action = self.sub(entity_action, "AddEntityAction")
        self.add_trajectory_position(add_entity_action, trajectory, 0)
        self.add_simulation_time_trigger(
            self.sub(event, "StartTrigger"),
            name=f"{entity_name}_spawn_start",
            value=format_float(self.trajectory_start_time(trajectory)),
            rule="greaterThan",
        )
        self.add_spawn_controller_action(maneuver, entity_name, trajectory)

    def add_spawn_controller_action(
        self,
        maneuver: ET.Element,
        entity_name: str,
        trajectory: dict[str, object],
    ):
        """Assign a private controller after a runtime actor has spawned."""
        controller_element = self.controller_xml_element_for_trajectory(trajectory)
        if (
            controller_element is None
            or self.local_name(controller_element.tag) != "ControllerAction"
        ):
            return
        event = self.sub(
            maneuver,
            "Event",
            maximumExecutionCount="1",
            name=f"{entity_name}_controller_event",
            priority="overwrite",
        )
        action = self.sub(event, "Action", name=f"{entity_name}_controller_action")
        private_action = self.sub(action, "PrivateAction")
        private_action.append(
            safe_xml_fromstring(ET.tostring(controller_element, encoding="unicode")),
        )
        self.add_storyboard_element_state_trigger(
            self.sub(event, "StartTrigger"),
            name=f"{entity_name}_controller_start",
            event_name=f"{entity_name}_spawn_event",
        )

    def add_storyboard_element_state_trigger(
        self,
        parent: ET.Element,
        name: str,
        event_name: str,
    ):
        """Trigger an event after another event has completed."""
        condition_group = self.sub(parent, "ConditionGroup")
        condition = self.sub(
            condition_group,
            "Condition",
            name=name,
            delay="0",
            conditionEdge="rising",
        )
        by_value_condition = self.sub(condition, "ByValueCondition")
        self.sub(
            by_value_condition,
            "StoryboardElementStateCondition",
            storyboardElementType="event",
            storyboardElementRef=event_name,
            state="completeState",
        )

    def add_motion_action(
        self,
        act: ET.Element,
        entity_name: str,
        trajectory: dict[str, object],
        spawn_event_name: str | None = None,
    ):
        """Append a motion maneuver for one actor unless the trajectory is static."""
        if self.xosc_export_mode_for_trajectory(trajectory) == "clear_trajectory":
            return
        motion = self.sub(
            act,
            "ManeuverGroup",
            maximumExecutionCount="1",
            name=f"{entity_name}_maneuver_group",
        )
        actors = self.sub(motion, "Actors", selectTriggeringEntities="false")
        self.sub(actors, "EntityRef", entityRef=entity_name)
        maneuver = self.sub(motion, "Maneuver", name=f"{entity_name}_maneuver")
        event = self.sub(
            maneuver,
            "Event",
            maximumExecutionCount="1",
            name=f"{entity_name}_event",
            priority="overwrite",
        )
        action = self.sub(event, "Action", name=f"{entity_name}_action")
        private_action = self.sub(action, "PrivateAction")
        routing_action = self.sub(private_action, "RoutingAction")
        mode = self.xosc_export_mode_for_trajectory(trajectory)
        if mode == "route":
            assign_route = self.sub(routing_action, "AssignRouteAction")
            route = self.sub(
                assign_route,
                "Route",
                name=f"{entity_name}_route",
                closed="false",
            )
            self.add_parameter_declarations(route, trajectory)
            route_trajectory = self.route_trajectory(trajectory)
            x_values = route_trajectory["x_m"]
            heading_values = self.trajectory_movement_headings(route_trajectory)
            for index in range(len(x_values)):
                waypoint = self.sub(route, "Waypoint", routeStrategy="fastest")
                position = self.sub(waypoint, "Position")
                self.sub(
                    position,
                    "WorldPosition",
                    **self.world_position_attributes(
                        route_trajectory,
                        index,
                        heading_values[index],
                    ),
                )
        elif (
            mode == "reach_position"
            and self.actor_type_for_trajectory(trajectory) == "pedestrian"
        ):
            # AcquirePositionAction has no nested declaration-capable element.
            self.add_parameter_declarations(maneuver, trajectory)
            acquire_position = self.sub(routing_action, "AcquirePositionAction")
            position = self.sub(acquire_position, "Position")
            self.sub(
                position,
                "WorldPosition",
                **self.world_position_attributes(trajectory, -1),
            )
        else:
            follow_trajectory = self.sub(routing_action, "FollowTrajectoryAction")
            time_reference = self.sub(follow_trajectory, "TimeReference")
            self.sub(time_reference, "None")
            self.sub(
                follow_trajectory,
                "TrajectoryFollowingMode",
                followingMode="position",
            )
            if self.current_rev_minor == 0:
                trajectory_element = self.sub(
                    follow_trajectory,
                    "Trajectory",
                    name=f"{entity_name}_trajectory",
                    closed="false",
                )
            else:
                trajectory_ref = self.sub(follow_trajectory, "TrajectoryRef")
                trajectory_element = self.sub(
                    trajectory_ref,
                    "Trajectory",
                    name=f"{entity_name}_trajectory",
                    closed="false",
                )
            self.add_parameter_declarations(trajectory_element, trajectory)
            shape = self.sub(trajectory_element, "Shape")
            polyline = self.sub(shape, "Polyline")
            time_values = trajectory.get("time_s", [])
            heading_values = self.trajectory_movement_headings(trajectory)
            for index, time_s in enumerate(time_values):
                vertex = self.sub(polyline, "Vertex", time=format_float(float(time_s)))
                position = self.sub(vertex, "Position")
                self.sub(
                    position,
                    "WorldPosition",
                    **self.world_position_attributes(
                        trajectory,
                        index,
                        heading_values[index],
                    ),
                )

        start_trigger = self.sub(event, "StartTrigger")
        if spawn_event_name is not None:
            self.add_storyboard_element_state_trigger(
                start_trigger,
                name=f"{entity_name}_event_start",
                event_name=spawn_event_name,
            )
        else:
            self.add_simulation_time_trigger(
                start_trigger,
                name=f"{entity_name}_event_start",
                value="0",
                rule="greaterThan",
            )

    @staticmethod
    def route_trajectory(trajectory: dict[str, object]) -> dict[str, object]:
        """Return raw GUI route points, with sampled trajectory data as fallback."""
        raw_waypoints = trajectory.get("route_waypoints")
        if isinstance(raw_waypoints, (list, tuple)) and len(raw_waypoints) >= 2:
            try:
                return {
                    "time_s": [float(waypoint["time_s"]) for waypoint in raw_waypoints],
                    "x_m": [float(waypoint["x_m"]) for waypoint in raw_waypoints],
                    "y_m": [float(waypoint["y_m"]) for waypoint in raw_waypoints],
                    "z_m": [
                        float(waypoint.get("z_m", 0.0)) for waypoint in raw_waypoints
                    ],
                    "actor_type": trajectory.get("actor_type"),
                    "coordinate_reference": trajectory.get("coordinate_reference"),
                    "dimensions": trajectory.get("dimensions"),
                }
            except (KeyError, TypeError, ValueError):
                pass
        return {
            "time_s": [float(value) for value in trajectory.get("time_s", [])],
            "x_m": [float(value) for value in trajectory.get("x_m", [])],
            "y_m": [float(value) for value in trajectory.get("y_m", [])],
            "z_m": [float(value) for value in trajectory.get("z_m", [])],
            "actor_type": trajectory.get("actor_type"),
            "coordinate_reference": trajectory.get("coordinate_reference"),
            "dimensions": trajectory.get("dimensions"),
        }


def build_openscenario_xml(
    trajectories: dict[str, dict[str, object]],
    road_logic_file: str | None = None,
    additional_scenario_information: dict[str, object] | None = None,
) -> ET.ElementTree:
    """Build an OpenSCENARIO XML document from trajectory data."""
    return OpenScenarioExporter().build_xml(
        trajectories,
        road_logic_file=road_logic_file,
        additional_scenario_information=additional_scenario_information,
    )


def write_openscenario(
    trajectories: dict[str, dict[str, object]],
    output_path: Path,
    road_logic_file: str | None = None,
    additional_scenario_information: dict[str, object] | None = None,
):
    """Write OpenSCENARIO XML to disk."""
    OpenScenarioExporter().export(
        trajectories,
        output_path,
        road_logic_file=road_logic_file,
        additional_scenario_information=additional_scenario_information,
    )
