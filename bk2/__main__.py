import argparse
import json
import time
from pathlib import Path
from .runtime import Session, ROOT
from .automation import load_plan, run_plan


def main():
    parser = argparse.ArgumentParser(description='BK2 内存状态监视和地下城自动化')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('gui', help='打开监视和控制界面')
    observe = sub.add_parser('observe', help='只读内存，不点击')
    observe.add_argument('--seconds', type=int, default=10)
    for name in ('run', 'preview'):
        p = sub.add_parser(name, help='实际刷本' if name == 'run' else '截图识别预览，不点击')
        p.add_argument('--rounds', type=int, help='临时使用按轮数模式；不传则使用计划中的体力阈值模式')
        p.add_argument('--plan', type=Path, help='合并地下城计划路径')
        p.add_argument('--capture', choices=['FramePool', 'ScreenDC'], default='FramePool')
        p.add_argument('--input', choices=['PostMessage', 'SendMessage', 'Seize'], default='PostMessage')
    sub.add_parser('check', help='检查版本、依赖及模板')
    args = parser.parse_args()
    if args.command in (None, 'gui'):
        from .gui import launch
        launch()
        return
    if args.command == 'check':
        from .runtime import verify_build
        from vendor_core.config import load
        import maa, numpy, PIL
        plan = load_plan()
        print(json.dumps({'build_sha256': verify_build(), 'templates': 'ok', 'dependencies': 'ok',
                          'scheduler': plan['scheduler'], 'enabled_dungeons': sum(t['enabled'] for t in plan['tasks'])}))
        return
    def emit(kind, data):
        if kind == 'image':
            from PIL import Image
            temp = ROOT / 'logs/latest_screen.tmp.png'
            Image.fromarray(data[:, :, :3][:, :, ::-1]).save(temp)
            temp.replace(ROOT / 'logs/latest_screen.png')
        elif kind == 'memory_change':
            state = data['data']
            d = state.get('dungeon_progress') or {}
            brief = {k: d.get(k) for k in ('dungeon_id', 'result', 'current_room_index')}
            brief['hero_count'] = state['heroes']['count']
            from .cooldowns import summarize
            brief['cooldowns'] = summarize(state.get('cooldown_fields'))
            from .food import summarize as summarize_food
            brief['food'] = summarize_food(state.get('food_fields'))
            print(json.dumps({'kind': kind, 'data': brief}, ensure_ascii=False), flush=True)
        elif kind not in ('stats', 'memory'):
            print(json.dumps({'kind': kind, 'data': data}, ensure_ascii=False), flush=True)
            if kind == 'manual_diamonds':
                def acknowledge():
                    try:
                        if input('手动领取并返回后，输入 CLAIMED 并回车；其他输入停止：').strip() == 'CLAIMED':
                            session.acknowledge_diamonds()
                        else:
                            session.stop_automation('未确认领取')
                    except EOFError:
                        session.stop_automation('没有人工确认输入；请用 GUI 继续')
                import threading
                threading.Thread(target=acknowledge, daemon=True).start()
    session = Session(emit)
    try:
        session.start()
        session.wait_ready()
        if args.command == 'observe':
            if not 1 <= args.seconds <= 3600:
                raise ValueError('观察时间须为 1–3600 秒')
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline and not session.error:
                time.sleep(0.2)
            if session.error:
                raise RuntimeError(session.error)
            print(json.dumps({'status': 'observed', 'seconds': args.seconds, 'log': str(session.log_path)}))
        else:
            plan = load_plan(args.plan)
            if args.rounds is not None:
                plan['max_total_runs'] = args.rounds
                plan['run_control'] = {'mode':'rounds'}
            result = run_plan(session, plan, args.command == 'preview', args.capture, args.input)
            if args.command == 'run' and not result['succeeded']:
                raise SystemExit(2)
    except Exception as exc:
        session.publish('error', str(exc))
        raise
    finally:
        session.stop()
        if session.thread:
            session.thread.join(timeout=3)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Covers failures before Session exists: imports, GUI/Tcl, config and check.
        from .diagnostics import save_failure
        try:
            path = save_failure(ROOT, 'startup', 'entrypoint', exc)
            print(f'错误诊断已保存：{path}', flush=True)
        except Exception as report_exc:
            print(f'诊断保存失败：{report_exc}', flush=True)
        raise
