"""Single source of application identity for UI, CLI and diagnostics."""

APP_NAME = '黑火守望'
VERSION = '0.5.4'
VERSION_TAG = f'v{VERSION}'
DISPLAY_NAME = f'{APP_NAME} {VERSION_TAG}'
RELEASE_CHANNEL = 'public-test'


def version_info():
    """Return a fresh, JSON-safe identity without reading settings or the game."""
    return {'name': APP_NAME, 'version': VERSION, 'tag': VERSION_TAG,
            'channel': RELEASE_CHANNEL}
