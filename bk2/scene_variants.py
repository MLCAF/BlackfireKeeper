"""Explicit scene variants from portable diagnostics; original templates stay frozen."""
import copy

# Each fallback is confined to the relevant part of the normalized 720px frame.
# These are captured UI variants, not a general relaxation of matching thresholds.
SCENE_VARIANTS = {
    '主界面锚点.png': ('主界面锚点兼容.png', [200, 120, 350, 300]),
    '城镇.png': ('城镇兼容.png', [0, 560, 150, 160]),
    '主城面包工坊.png': ('主城面包工坊兼容.png', [100, 330, 250, 220]),
    '传送门.png': ('传送门兼容.png', [0, 560, 150, 160]),
}


class SceneVariantMixin:
    def recognize(self, image, rule, stop):
        hit = super().recognize(image, rule, stop)
        variant = SCENE_VARIANTS.get(rule['template'])
        if hit is not None or variant is None or stop.is_set():
            return hit
        template, region = variant
        h, w = image.shape[:2]
        x, y, rw, rh = rule.get('roi', [0, 0, w, h])
        vx, vy, vw, vh = region
        left, top = max(x, vx), max(y, vy)
        right, bottom = min(x+rw, vx+vw, w), min(y+rh, vy+vh, h)
        (tw, th), _, _ = self.template_metadata(self.image_root / template)
        if right-left < tw or bottom-top < th:
            return None
        alternate = copy.deepcopy(rule)
        alternate.update(template=template, name=rule['name']+' / 兼容画面',
                         roi=[left, top, right-left, bottom-top],
                         threshold=max(.95, rule.get('threshold', .95)),
                         min_contrast_ratio=max(.85, rule.get('min_contrast_ratio', 0)),
                         min_brightness_ratio=max(.85, rule.get('min_brightness_ratio', 0)))
        # Keep requires_match: a bakery or portal alone cannot authorize a click.
        return super().recognize(image, alternate, stop)
