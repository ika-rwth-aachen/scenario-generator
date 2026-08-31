from scenario_generator.config.settings import load_config, load_lane_type_colors


def test_yaml_parser_preserves_hash_in_quoted_values(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        'map_display:\n  lane_type_colors:\n    driving: "#123abc" # custom\n',
        encoding="utf-8",
    )

    assert load_config(config_path)["map_display"]["lane_type_colors"]["driving"] == (
        "#123abc"
    )
    assert load_lane_type_colors(config_path)["driving"] == "#123abc"
