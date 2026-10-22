"""Explicit scene variants from portable diagnostics; original templates stay frozen."""
import copy

# Each fallback is confined to the relevant part of the normalized 720px frame.
# These are captured UI variants, not a general relaxation of matching thresholds.
SCENE_VARIANTS = {
    '未完成地下城提示.png': ('未完成地下城提示兼容.png', [500, 290, 400, 100]),
    '重连登录标识.png': ('重连登录标识兼容.png', [500, 200, 400, 180]),
    '主界面锚点.png': ('主界面锚点兼容.png', [200, 120, 350, 300]),
    '城镇.png': ('城镇兼容.png', [0, 560, 150, 160]),
    '主城面包工坊.png': ('主城面包工坊兼容.png', [100, 330, 250, 220]),
    '传送门.png': ('传送门兼容.png', [0, 560, 150, 160]),
    '放弃冒险确认文字.png': ('放弃冒险确认文字兼容.png', [650, 390, 250, 80]),
    '放弃冒险提示正文.png': ('放弃冒险提示正文兼容.png', [600, 290, 300, 100]),
}

# Real 1280x661 client captures, normalized by Maa to a 720-pixel short side.
# Selected navigation variants retain the selection ring, not just the icon.
SMALL_WINDOW_VARIANTS = {
    '副本入口进度.png': ('副本入口进度小窗.png', [550, 65, 100, 80]),
    '钻石免费按钮.png': ('钻石免费按钮小窗.png', [210, 480, 340, 90]),
    '去探险.png': ('去探险小窗.png', [430, 480, 550, 120]),
    '钻石免费包标识.png': ('钻石免费包标识小窗.png', [210, 200, 340, 110]),
    '钻石商店关闭核心.png': ('钻石商店关闭核心小窗.png', [1110, 50, 180, 160]),
    '体力工坊标题.png': ('体力工坊标题小窗.png', [500, 130, 400, 150]),
    '体力收集文字.png': ('体力收集文字小窗.png', [650, 440, 300, 120]),
    '四本名称.png': ('四本名称小窗.png', [0, 440, 1395, 110]),
    '五本名称.png': ('五本名称小窗.png', [0, 440, 1395, 110]),
    '六本名称.png': ('六本名称小窗.png', [0, 440, 1395, 110]),
    '城镇.png': ('城镇小窗.png', [0, 560, 150, 160]),
    '副本详情标识.png': ('副本详情标识小窗.png', [450, 320, 500, 100]),
    '地下城导航选中.png': ('地下城导航选中小窗.png', [400, 560, 350, 160]),
    '关卡导航选中.png': ('关卡导航选中小窗.png', [400, 560, 150, 160]),
    '一本名称.png': ('一本名称小窗.png', [0, 440, 1395, 110]),
    '二本名称.png': ('二本名称小窗.png', [0, 440, 1395, 110]),
    '三本名称.png': ('三本名称小窗.png', [0, 440, 1395, 110]),
    '传送门.png': ('传送门小窗.png', [0, 560, 150, 160]),
}

MEDIUM_WINDOW_VARIANTS = {
    '钻石免费按钮.png': ('钻石免费按钮中窗.png', [210, 480, 340, 90]),
    '钻石免费包标识.png': ('钻石免费包标识中窗.png', [210, 200, 340, 110]),
    '体力工坊标题.png': ('体力工坊标题中窗.png', [500, 130, 400, 150]),
    '五本名称.png': ('五本名称中窗.png', [0, 440, 1395, 110]),
}

LARGE_WINDOW_VARIANTS = {
    '体力收集文字.png': ('体力收集文字1600.png', [650, 440, 300, 120]),
    '二本名称.png': ('二本名称1600.png', [0, 440, 1395, 110]),
}

VARIANT_GROUPS = (SCENE_VARIANTS, SMALL_WINDOW_VARIANTS, MEDIUM_WINDOW_VARIANTS, LARGE_WINDOW_VARIANTS)


class SceneVariantMixin:
    def recognize(self, image, rule, stop):
        hit = super().recognize(image, rule, stop)
        if hit is None and rule['template']=='确认buff.png' and not stop.is_set():
            from .confirmation import BUFF_CONFIRM_TITLE
            hit = self._recognize_variant(image,rule,stop,'经验祝福确认文字.png',
                                          [650,380,110,85],BUFF_CONFIRM_TITLE)
        variants = [group[rule['template']] for group in VARIANT_GROUPS
                    if rule['template'] in group]
        if hit is not None or not variants or stop.is_set():
            return hit
        if rule['template'] == '主界面锚点.png':
            variants.append(('主界面高亮建筑核心.png', [250, 150, 300, 230]))
        for template, region in variants:
            hit = self._recognize_variant(image, rule, stop, template, region)
            if hit is not None:
                return hit
        return None

    def _recognize_variant(self, image, rule, stop, template, region, context=None):
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
        if context is not None:
            condition=copy.deepcopy(context)
            if 'requires_match' in alternate:
                condition['requires_match']=alternate['requires_match']
            alternate['requires_match']=condition
        return super().recognize(image, alternate, stop)
