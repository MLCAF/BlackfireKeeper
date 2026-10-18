"""Identify the selected dungeon tab independently of scrolling nameplates."""
import copy

from vendor_core.list_cooldown import ListCooldownScanner as BaseScanner
from vendor_core.list_cooldown import list_navigation_profile as base_navigation

LIST_RULE = dict(name='无遮挡且已选中的地下城导航', template='地下城导航选中.png',
                 roi=[400, 560, 350, 160], threshold=.97,
                 min_brightness_ratio=.85, min_contrast_ratio=.85)


def add_list_guard(profile):
    """Replace every old list predicate, including delayed success and exits."""
    def visit(value):
        if isinstance(value, dict):
            if value.get('template') == '地下城列表通用标识.png':
                for key, item in LIST_RULE.items():
                    if key != 'name':
                        value[key] = copy.deepcopy(item)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(profile)
    return profile


def list_navigation_profile(base, task, tasks):
    return add_list_guard(base_navigation(base, task, tasks))


class ListCooldownScanner(BaseScanner):
    def capture(self):
        if not self.active():
            return None
        frame = self.backend.capture(self.stop)
        captured = self.clock()
        self.emit('image', frame)
        if not self.backend.recognize(frame, LIST_RULE, self.stop):
            return None
        return frame, captured
