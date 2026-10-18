"""Bakery-only UI flow. Collection is confirmed by the caller's live memory delta."""
def build_food_profile():
    def evidence(template, roi=None):
        rule = dict(template=template, threshold=.95, min_contrast_ratio=.75,
                    min_brightness_ratio=.75)
        if roi:
            rule['roi'] = roi
        return rule
    title = evidence('体力工坊标题.png', [500,130,400,150])
    icon = evidence('体力面包入口.png', [850,0,500,140])
    reward = evidence('体力奖励面包.png', [450,250,500,220])
    def click(name, target, next_state, success, requires=None, **kw):
        rule = dict(target, name=name, action='click', next=next_state, verify_change=True,
                    success_match=success, confirm_frames=2, retry_center_clicks=1,
                    retry_max_clicks=2, retry_radius=0, cooldown_seconds=.5, **kw)
        if requires:
            rule['requires_match'] = requires
        return rule
    collect = dict(evidence('体力收集文字.png', [650,440,300,120]),
                   name='收集免费体力', action='click', next='FoodAwaitReward',
                   requires_match=title, confirm_frames=2, cooldown_seconds=.5,
                   increment_counter='food_collection_requests', food_collect=True)
    # Submit once, then observe in a separate bounded state. The generic click
    # verifier waits only 0.6 s before treating an unchanged button as failure.
    # Neither a sent click nor a disappeared button proves that bread arrived.
    collect['verify_change'] = False
    close = click('关闭面包工坊', evidence('体力工坊关闭.png',[850,130,170,150]),
                  'FoodDone', icon, title)
    return dict(version=1, profile=dict(id='food',display_name='免费体力领取',kind='one_shot'),
                initial_state='FoodEntry', max_minutes=3, max_rounds=0, poll_seconds=.3,
                watchdog=dict(warn_seconds=20,stop_seconds=60,repeat_warning_seconds=20),
                states={
                    'FoodEntry':dict(timeout_seconds=45,rules=[
                        dict(title,name='面包工坊已打开',action='none',next='FoodCollect',confirm_frames=2),
                        click('打开面包工坊',icon,'FoodCollect',title),
                        click('主界面经传送门到体力入口',evidence('传送门.png'),
                              'FoodEntry',icon,evidence('主界面锚点.png'))]),
                    'FoodCollect':dict(timeout_seconds=30,rules=[collect]),
                    'FoodAwaitReward':dict(timeout_seconds=30,rules=[
                        dict(reward,name='等待并确认体力奖励弹窗',action='none',
                             next='FoodReward',confirm_frames=2,
                             increment_counter='food_collections')]),
                    'FoodReward':dict(timeout_seconds=30,rules=[
                        click('确认体力奖励',evidence('体力奖励确认.png',[450,440,500,110]),
                              'FoodClose',title,reward)]),
                    'FoodClose':dict(timeout_seconds=30,rules=[close]),
                    'FoodDone':dict(timeout_seconds=15,rules=[
                        dict(icon,name='确认返回体力入口',action='none',event='task_complete',confirm_frames=2)])})
