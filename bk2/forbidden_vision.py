"""Golden Kingdom visual rules. Free chest actions never use price coordinates."""

def rule(name, roi, **extra):
    return dict(name='禁域 / '+name, template='forbidden/'+name+'.png', roi=roi,
                threshold=.95, min_brightness_ratio=.85, min_contrast_ratio=.85, **extra)


RULES = {
    'treasure_title': rule('treasure_title', [400,180,590,140]),
    'free_open': rule('free_open', [70,400,1250,130]),
    'claimed': rule('claimed', [70,400,1250,130]),
    'close': rule('close', [700,120,680,200]),
    'xp_title': rule('xp_title', [500,155,400,95]),
    # One/two/three reward rows move both the title and the Continue button.
    'loot_title': rule('loot_title', [500,70,400,190]),
    'continue': rule('continue', [450,430,500,240]),
    'auto_off': rule('auto_off', [1000,0,180,110]),
    'auto_on': rule('auto_on', [1000,0,180,110]),
    'gold_header': rule('gold_header', [550,15,300,85]),
    'explore': rule('explore', [490,595,190,125]),
    'entrance': rule('entrance', [300,560,800,160]),
    'map': rule('map', [30,100,1320,430]),
    'detail': rule('detail', [490,65,420,130]),
    'enter': rule('enter', [400,440,600,160]),
}


class ForbiddenVision:
    def __init__(self, backend, stop):
        self.backend, self.stop = backend, stop

    def observe(self, image):
        def hit(name):
            result = self.backend.recognize(image, RULES[name], self.stop)
            if result:
                return result
            # Captured UI rasterization differs by physical window size. Each
            # variant keeps the same ROI, context, brightness and .95 threshold.
            for size in (1600,1440,1280,1920):
                template=f'forbidden/{name}_{size}.png'
                if (self.backend.image_root/template).is_file():
                    result=self.backend.recognize(image,dict(RULES[name],template=template),self.stop)
                    if result:
                        return result
            return None

        # Foreground modals take priority over dimmed exploration controls.
        from .confirmation import notification_rules
        welcome = self.backend.recognize(image, notification_rules()[0], self.stop)
        if welcome:
            return 'welcome', welcome
        if hit('treasure_title'):
            claimed = hit('claimed')
            if claimed:
                return 'chest_claimed', hit('close')
            return 'chest_free', hit('free_open')
        for title, kind in [('xp_title','xp'), ('loot_title','loot')]:
            if hit(title):
                return kind, hit('continue')
        from .battle import DEFEAT
        if self.backend.recognize(image, dict(DEFEAT,name='禁域 / 战败'), self.stop):
            return 'defeat', None
        for name in ('auto_on', 'auto_off'):
            match = hit(name)
            if match:
                return name, match
        if hit('gold_header'):
            match = hit('explore')
            if match:
                return 'explore', match
        if hit('detail'):
            return 'detail', hit('enter')
        match = hit('map')
        if match:
            return 'map', match
        match = hit('entrance')
        if match:
            return 'menu', match
        from .recovery import HOME
        if self.backend.recognize(image, dict(HOME,name='禁域 / 主城'), self.stop):
            portal = dict(name='禁域 / 主城传送门', template='传送门.png', threshold=.95,
                          min_brightness_ratio=.85, min_contrast_ratio=.85)
            return 'home', self.backend.recognize(image, portal, self.stop)
        return 'unknown', None
