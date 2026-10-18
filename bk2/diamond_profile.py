"""Diamond evidence from current capture pixels; upstream profile stays frozen."""
import copy
import json
from .runtime import ROOT

REWARD='钻石20奖励核心.png'
CONFIRM='钻石奖励确认核心.png'
CLOSE='钻石商店关闭核心.png'

def build_diamond_profile():
    profile=json.loads((ROOT/'config/profiles/diamonds.json').read_text(encoding='utf-8-sig'))
    replacements={'钻石20奖励.png':REWARD,'钻石奖励确认.png':CONFIRM,'钻石商店关闭.png':CLOSE}
    def visit(value):
        if isinstance(value,dict):
            if value.get('template') in replacements:
                value['template']=replacements[value['template']]
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(profile)
    legacy_confirm=copy.deepcopy(profile['states']['Reward']['rules'][0])
    legacy_confirm.update(name='确认获得20钻石（旧字体兼容）',template='钻石奖励确认.png')
    profile['states']['Reward']['rules'].append(legacy_confirm)
    return profile

def entry_recovery_rules():
    profile=build_diamond_profile()
    confirms=copy.deepcopy(profile['states']['Reward']['rules'])
    for confirm in confirms:
        confirm.update(name='入口恢复：'+confirm['name'],next='Entry')
        confirm.pop('increment_counter',None)  # Acknowledgement is not a new claim.
    close=copy.deepcopy(profile['states']['CloseShop']['rules'][0])
    close.update(name='入口关闭遗留钻石商店并返回主界面',next='Entry',foreground=True)
    close.pop('event',None)  # Returning home must not count as dungeon completion.
    return confirms+[close]

def build_cleanup_profile():
    home=copy.deepcopy(build_diamond_profile()['states']['CloseShop']['rules'][1])
    return dict(version=1,initial_state='Entry',max_minutes=2,max_rounds=0,poll_seconds=.5,
                watchdog=dict(warn_seconds=20,stop_seconds=45,repeat_warning_seconds=20),
                states={'Entry':dict(timeout_seconds=45,rules=entry_recovery_rules()+[home])})
