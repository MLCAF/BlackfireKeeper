"""Native dashboard checks using synthetic memory; never sends game input."""
import hashlib
import json
from pathlib import Path
import sys
import time
import tkinter as tk
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bk2.gui import App
from bk2.runtime import ROOT
from bk2.cooldowns import DUNGEON_IDS


def main():
    config = ROOT/'config/dungeon_plan.json'
    original = config.read_bytes()
    root = tk.Tk()
    try:
        app = App(root)
        root.title('BK2 界面测试 · 模拟数据')
        root.update()
        checks = {}
        app.select_dungeons('first')
        assert [t['order'] for t in app.collect_plan()['tasks'] if t['enabled']] == [1]
        app.select_dungeons('all')
        assert app.task_flags['dungeon_7'].get()
        app.select_dungeons('none')
        assert not any(t['enabled'] for t in app.collect_plan()['tasks'])
        app.task_flags['dungeon_2'].set(True)
        app.task_buffs['dungeon_2'].set('伤害 Buff')
        selected = app.collect_plan()
        assert [t['order'] for t in selected['tasks'] if t['enabled']] == [2]
        assert selected['tasks'][1]['buff'] == 'damage'
        checks['selection_and_buffs'] = True
        from bk2.automation import save_plan, load_plan
        assert app.stamina_threshold.get() == str(json.loads(original)['run_control']['minimum_stamina'])
        app.stamina_threshold.set('200')
        with tempfile.TemporaryDirectory() as folder:
            saved=Path(folder)/'plan.json'
            with patch('bk2.gui.save_plan',side_effect=lambda plan:save_plan(plan,saved)):
                app.save_options()
            loaded=load_plan(saved)
            assert loaded['run_control'] == {'mode':'stamina','minimum_stamina':200}
            reopened_root=tk.Toplevel(root)
            with patch('bk2.gui.load_plan',return_value=loaded):
                reopened=App(reopened_root)
            assert reopened.stamina_threshold.get()=='200'
            reopened_root.destroy()
        app.stamina_threshold.set('0')
        try: app.collect_plan()
        except ValueError: pass
        else: raise AssertionError('Invalid threshold accepted')
        app.stamina_threshold.set('100')
        checks['editable_threshold_save_and_reload'] = True
        now = time.time()
        snapshot = {'status':'ok','pid':123,'captured_monotonic':time.monotonic(), 'data': {
            'heroes':{'count':17,'parties':[]}, 'cooldown_fields':{'dungeons':{
                k:{'refresh_time':0 if i==0 else (now+180*i)*1000,'has_unlocked':True}
                for i,k in enumerate(DUNGEON_IDS[:6])}, 'free_gem_time':(now+3600)*1000}}}
        snapshot['data']['food_fields']={'bread':315,'start_time':(now-5000)*1000}
        app.show_memory(snapshot)
        assert app.food_text.get() == '315 / 320'
        assert '可领取 5 点' in app.food_hint.get()
        assert app.collect_plan()['food']['enabled'] is True
        app.drain();root.update()
        assert app.task_rows['dungeon_1'][0].get() == '可进入'
        assert app.task_rows['dungeon_2'][0].get() == '冷却中'
        assert app.task_rows['dungeon_7'][0].get() == '未知'
        assert ':' in app.diamond_text.get()
        assert str(app.run_button.cget('state')) == 'normal'
        checks['live_countdown_layout'] = True
        app.report_error=Mock()
        app.select_dungeons('none');app.drain();root.update()
        assert str(app.run_button.cget('state'))=='normal'
        assert app.start_block_reason() is None
        app.task_flags['dungeon_2'].set(True)
        snapshot['data']['dungeon_progress']={'result':'in progress','dungeon_id':'forest_maze'}
        app.drain();root.update()
        assert str(app.run_button.cget('state'))=='normal'
        snapshot['data']['dungeon_progress']['dungeon_id']='unsupported'
        app.drain();root.update()
        assert str(app.run_button.cget('state'))=='disabled'
        assert '无法映射' in app.task_text.get()
        app.start(False);app.report_error.assert_not_called()
        snapshot['data']['dungeon_progress']['result']='finished'
        app.drain();root.update()
        assert str(app.run_button.cget('state'))=='normal'
        checks['empty_selection_and_in_progress_preflight']=True
        snapshot['captured_monotonic'] -= 10
        app.drain();root.update()
        assert app.task_rows['dungeon_1'][0].get() == '未连接'
        assert app.diamond_text.get() == '等待连接'
        assert app.food_text.get() == '— / 320'
        assert app.progress['value'] == 0
        assert str(app.run_button.cget('state')) == 'disabled'
        checks['stale_state_invalidated'] = True
        app.worker = SimpleNamespace(is_alive=lambda:True)
        app.drain();root.update()
        assert all(str(w.cget('state')) == 'disabled' for w in app.settings_widgets)
        app.worker = None
        app.active_total = 20
        app.emit('schedule', {'task':'二本','phase':'completed','completed_runs':3})
        app.emit('stats', {'mode':'点击','state':'Entry','rounds':0,'clicks':0})
        app.drain();root.update()
        assert app.completed_text.get() == '3 / 20'
        assert app.progress['value'] == 15
        checks['global_progress_and_setting_lock'] = True
        app.active_total=None
        app.worker=SimpleNamespace(is_alive=lambda:True)
        snapshot['captured_monotonic']=time.monotonic()
        app.emit('food_schedule',{'next_check':time.time()+1800})
        app.emit('schedule',{'kind':'stamina','task':'等待体力恢复','phase':'waiting',
                            'completed_runs':3,'stamina':95,'threshold':100})
        app.drain();root.update()
        assert app.completed_text.get()=='已完成 3 轮'
        assert '补充后自动继续' in app.task_text.get()
        assert '每30分钟检查' in app.food_hint.get()
        checks['threshold_wait_and_food_timer'] = True
        layouts = []
        for width,height in [(1180,850),(1080,790)]:
            root.geometry(f'{width}x{height}');root.update()
            clipped = []
            def visit(widget):
                if widget is app.plan_body:
                    return  # Canvas content is intentionally scrollable.
                for child in widget.winfo_children():
                    if child is app.plan_body:
                        continue
                    if child.winfo_ismapped():
                        x=child.winfo_rootx()-root.winfo_rootx()
                        y=child.winfo_rooty()-root.winfo_rooty()
                        if x < 0 or y < 0 or x+child.winfo_width()>root.winfo_width()+2 or y+child.winfo_height()>root.winfo_height()+2:
                            clipped.append([str(child),child.winfo_class(),x,y,child.winfo_width(),child.winfo_height()])
                        visit(child)
            visit(root)
            app.plan_canvas.yview_moveto(0);root.update()
            assert app.food_label.winfo_ismapped(), 'Food card must be mapped'
            def visible(widget):
                top=app.plan_canvas.winfo_rooty()
                return (widget.winfo_ismapped() and widget.winfo_rooty()>=top
                        and widget.winfo_rooty()+widget.winfo_height()<=top+app.plan_canvas.winfo_height())
            assert visible(app.food_label), 'Stamina must be visible without scrolling'
            app.plan_canvas.yview_moveto(1);root.update()
            assert visible(app.stamina_entry), 'Threshold must be reachable by scrolling'
            app.plan_canvas.yview_moveto(0);root.update()
            layouts.append({'size':[root.winfo_width(),root.winfo_height()],'clipped':clipped})
        checks['layouts'] = layouts
        assert original == config.read_bytes()
        checks.update(game_input_sent=False, config_unchanged=True)
        folder=ROOT/'evidence/capture_fix_20261018';folder.mkdir(exist_ok=True,parents=True)
        (folder/'gui_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(checks,ensure_ascii=False))
        assert not any(item['clipped'] for item in layouts), 'Window has clipped controls'
    finally:
        root.destroy()


if __name__ == '__main__': main()
