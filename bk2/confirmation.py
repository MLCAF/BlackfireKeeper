"""Known notification acknowledgements, never a bare 'confirm' auto-click."""
import copy


def evidence(template, roi):
    return dict(template=template, roi=roi, threshold=.95,
                min_brightness_ratio=.85, min_contrast_ratio=.85)


WELCOME_TITLE = evidence('欢迎归队标题.png', [350,60,700,250])
WELCOME_TITLE['requires_match'] = evidence('欢迎归队称呼.png', [200,180,800,220])
NOTICE_TEMPLATES = frozenset({'欢迎归队标题.png','欢迎归队称呼.png','欢迎归队确认.png'})


def notification_rules():
    """Global known notice; recovery verifies destination and fresh game state."""
    return [dict(name='通知确认：欢迎归队',
        **evidence('欢迎归队确认.png', [350,380,700,270]),
        requires_match=copy.deepcopy(WELCOME_TITLE),
        action='click', event='none', foreground=True, recovery_action='welcome',
        verify_change=True, confirm_frames=2, cooldown_seconds=1,
        max_activations=3, retry_max_clicks=1, retry_center_clicks=1, retry_radius=0)]


BUFF_CONFIRM_TITLE = dict(template='经验祝福确认标题.png',roi=[600,220,200,95],
                          threshold=.96,min_brightness_ratio=.85,min_contrast_ratio=.85)
