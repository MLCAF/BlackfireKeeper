"""Additional guarded loot layout; keep the proven upstream variants intact."""
import copy


def boundary_loot_rules():
    """Acknowledge a known loot modal without recording a new run/reward."""
    return [dict(name='领取边界确认遗留战利品（小字双排弹窗）',
        template='战利品小字确认.png', threshold=.95, roi=[500,380,400,220],
        min_contrast_ratio=.85, min_brightness_ratio=.85,
        requires_match=dict(template='战利品小字标题.png', threshold=.95,
                            roi=[550,80,300,180], min_contrast_ratio=.85,
                            min_brightness_ratio=.85),
        action='click', next='Boundary', event='none', foreground=True,
        verify_change=True, retry_center_clicks=1, retry_max_clicks=2,
        retry_radius=0, max_activations=2, confirm_frames=2, cooldown_seconds=1)]


def add_loot_recovery(profile):
    progress = profile['states']['Progress']['rules']
    old = next(r for r in progress if r['name']=='领取战利品后继续（宽弹窗兼容）')
    small = copy.deepcopy(old)
    small.update(name='领取战利品后继续（小字双排弹窗）', template='战利品小字继续.png',
                 threshold=.95, roi=[500,380,400,220], min_contrast_ratio=.75,
                 min_brightness_ratio=.75, retry_center_clicks=1, retry_max_clicks=2,
                 retry_radius=0, confirm_frames=2,
                 requires_match=dict(template='战利品小字标题.png', threshold=.95,
                                     roi=[550,80,300,180], min_contrast_ratio=.75,
                                     min_brightness_ratio=.75))
    progress.insert(progress.index(old), small)
    # A previously finished run may leave its loot open at startup. Closing it
    # must not invent a battle/dungeon completion in the newly selected task.
    recovery = copy.deepcopy(small)
    recovery.update(name='入口处理遗留战利品（小字双排弹窗）', next='Entry')
    recovery.pop('increment_counter',None)
    profile['states']['Entry']['rules'].append(recovery)
    return profile
