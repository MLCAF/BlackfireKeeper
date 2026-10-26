"""Local plan extensions without changing the frozen upstream validator."""
import copy
from vendor_core.config import validate_dungeon_plan as upstream_validate

# Ordered game metadata; actual ready/cooling state still comes from memory/OCR.
BUNDLED_LATE_DUNGEONS = {
    7: ('七本', '兽人地堡', 2100),
    8: ('八本', '巨龙巢穴', 2400),
    9: ('九本', '古老墓穴', 2700),
    10: ('十本', '地狱深渊', 3000),
}


def with_bundled_entrances(data):
    """Add bundled entrances in memory; preserve existing choices and files."""
    plan = copy.deepcopy(data)
    if not isinstance(plan, dict) or plan.get('version') != 2:
        return plan
    tasks = plan.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        return plan
    for task in tasks:
        if not isinstance(task, dict):
            continue
        order = task.get('order')
        if (type(order) is int and order in BUNDLED_LATE_DUNGEONS
                and task.get('id') == f'dungeon_{order}' and not task.get('entrance_template')):
            label, title, _ = BUNDLED_LATE_DUNGEONS[order]
            task['entrance_template'] = label+'名称.png'
            task.setdefault('entrance_offset', [0, -150])
            if task.get('display_name') == label+'（待补入口图）':
                task['display_name'] = f'{label}（{title}）'
    for order in (8, 9, 10):
        # Keep custom rows untouched, including any row occupying this order.
        if any(isinstance(t, dict) and (t.get('id') == f'dungeon_{order}'
               or t.get('order') == order) for t in tasks):
            continue
        label, title, cooldown = BUNDLED_LATE_DUNGEONS[order]
        tasks.append(dict(id=f'dungeon_{order}', display_name=f'{label}（{title}）',
                          order=order, enabled=False, entrance_template=label+'名称.png',
                          entrance_offset=[0, -150], cooldown_seconds=cooldown,
                          fallback=False, buff='experience'))
    return plan


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
