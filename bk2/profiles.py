"""BK2 corrections layered over the unchanged upstream dungeon profile."""
import copy

from vendor_core.unified import build_dungeon_profile as upstream_profile


def build_dungeon_profile(base, task, tasks):
    profile = upstream_profile(base, task, tasks)
    rules = profile['states']['Progress']['rules']
    # The old full altar includes the first dungeon's green cave background.
    # Its interior is identical on the saved blue-cave and hover frames.
    old = next(r for r in rules if r['template'] == '开局buff.png')
    core = copy.deepcopy(old)
    core.update(
        name='进入开局 Buff 房间（祭坛核心）', template='开局祭坛核心.png',
        roi=[880, 200, 350, 330], threshold=.95, min_contrast_ratio=.65,
        requires_match=dict(template='副本入口进度.png', roi=[550, 65, 100, 80],
                            threshold=.94, min_contrast_ratio=.65),
        offset=[0, 16], confirm_frames=2, retry_center_clicks=2,
        retry_max_clicks=2, retry_radius=0,
    )
    # Preserve the per-dungeon selected Buff as the positive successor evidence.
    core['success_match']['min_contrast_ratio'] = .65
    fixed = []
    for rule in rules:
        if rule is old:
            fixed.append(core)
        elif rule['template'] != '开局buff高亮.png':
            fixed.append(rule)
    profile['states']['Progress']['rules'] = fixed
    from .diamond_profile import entry_recovery_rules
    profile['states']['Entry']['rules'][0:0] = entry_recovery_rules()
    from .loot_profile import add_loot_recovery
    add_loot_recovery(profile)
    from .battle import add_defeat_recovery
    add_defeat_recovery(profile)
    from .list_guard import add_list_guard
    add_list_guard(profile)
    from .room_profile import add_room_evidence
    add_room_evidence(profile)
    from .cooldowns import DUNGEON_IDS
    profile['expected_dungeon_id'] = DUNGEON_IDS[task['order']-1]
    profile['navigation_names'] = [t['entrance_template'] for t in tasks if t.get('entrance_template')]
    return profile
