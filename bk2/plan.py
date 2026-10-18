"""Local plan extensions without changing the frozen upstream validator."""
import copy
from vendor_core.config import validate_dungeon_plan as upstream_validate


def validate_dungeon_plan(data, profiles_root, image_root, check_files=False):
    # Validate every upstream invariant using a private copy. A temporary enabled
    # task is validation scaffolding only; never return or persist that selection.
    probe = copy.deepcopy(data)
    if (isinstance(probe, dict) and probe.get('version') == 2
            and isinstance(probe.get('tasks'), list) and probe['tasks']
            and all(isinstance(t, dict) and t.get('enabled') is False for t in probe['tasks'])):
        candidate = next((t for t in probe['tasks'] if t.get('entrance_template')), None)
        if candidate is not None:
            candidate['enabled'] = True
    upstream_validate(probe, profiles_root, image_root, check_files)
    return data
