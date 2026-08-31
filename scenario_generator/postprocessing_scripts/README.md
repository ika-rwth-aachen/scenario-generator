# Postprocessing scripts

Postprocessing adapts files after scenario.generator creates them. It is useful
for repeatable downstream-tool adjustments without changing the scenario
itself. The web application discovers Python files in this directory and makes
them selectable in **Configure postprocessing**; arbitrary shell commands are
not accepted.

Every script must define its applicable formats, optional parameters, and this entry point:

```python
from pathlib import Path

APPLICABLE_EXPORTS = frozenset({"json", "xosc", "mcap", "xodr"})
PARAMETERS = [{"name": "example", "label": "Example", "type": "string", "default": ""}]

def run(export_directory: Path, parameters: dict[str, object]) -> bool:
    """Process files already written into export_directory."""
    return True
```

`APPLICABLE_EXPORTS` limits execution to selected matching export formats. `PARAMETERS` supports `name`, `label`, `type` (`number` or `string`), and `default`. `run` receives the directory containing generated files and the values supplied in the GUI. Return `True` only when processing succeeded and `False` on an expected failure.
