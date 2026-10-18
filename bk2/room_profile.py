"""Room navigation evidence cropped inside controls, independent of cave art."""
import copy


def add_room_evidence(profile):
    for state in ('Progress', 'OpeningBuffLeaveConfirm', 'SkillCardLeaveConfirm'):
        rules = profile['states'][state]['rules']
        old = next(r for r in rules if r['template'] == '去下一层.png')
        core = copy.deepcopy(old)
        core.update(name=old['name']+'（阶梯与文字核验）', template='下一层阶梯核心.png',
                    roi=[620,570,160,145], threshold=.95,
                    min_contrast_ratio=.85, min_brightness_ratio=.85,
                    requires_match=dict(template='下一层文字.png', roi=[620,670,160,45],
                                        threshold=.95, min_contrast_ratio=.85,
                                        min_brightness_ratio=.85),
                    retry_center_clicks=1, retry_max_clicks=2, retry_radius=0)
        rules.insert(rules.index(old)+1, core)
    rules = profile['states']['Progress']['rules']
    old = next(r for r in rules if r['template'] == '技能卡.png')
    card = copy.deepcopy(old)
    card.update(name='进入技能卡房间（中牌与门徽核验）', template='技能卡中牌核心.png',
                roi=[940,240,220,230], threshold=.95,
                min_contrast_ratio=.85, min_brightness_ratio=.85,
                requires_match=dict(template='技能卡门徽核心.png',roi=[940,340,220,150],
                                    threshold=.95,min_contrast_ratio=.85,min_brightness_ratio=.85),
                offset=[0,64],retry_center_clicks=1,retry_max_clicks=2,retry_radius=0)
    rules.insert(rules.index(old)+1, card)
    return profile
