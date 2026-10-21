"""Portable entry point. Selecting an EXE never starts or connects to the game."""
import json
import os
import sys
import traceback
from pathlib import Path
from .paths import application_root


def saved_game(root):
    target = root / 'config/runtime/installation.json'
    if not target.exists():
        return None
    data = json.loads(target.read_text(encoding='utf-8'))
    value = data.get('game_exe')
    return Path(value) if isinstance(value, str) and value else None


def configure_game(root, force=False):
    if not force:
        candidates = [os.environ.get('BLACKFIRE_EXE'), saved_game(root),
                      root.parent.parent / 'Blackfire Crusade.exe',
                      root.parent / 'Blackfire Crusade.exe']
        for value in candidates:
            if value and Path(value).is_file():
                os.environ['BLACKFIRE_EXE'] = str(Path(value).resolve())
                return True
    import tkinter as tk
    from tkinter import filedialog, messagebox
    dialog = tk.Tk()
    dialog.withdraw()
    try:
        while True:
            value = filedialog.askopenfilename(parent=dialog,
                title='请选择游戏 Blackfire Crusade.exe（不选择脚本启动器）',
                filetypes=[('Blackfire Crusade', 'Blackfire Crusade.exe')])
            if not value:
                return False
            path = Path(value).resolve()
            if path.name.casefold() != 'blackfire crusade.exe' or not path.is_file():
                messagebox.showerror('游戏路径无效', '请选择已安装游戏的 Blackfire Crusade.exe。', parent=dialog)
                continue
            target = root / 'config/runtime/installation.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps({'game_exe': str(path)}, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(target)
            os.environ['BLACKFIRE_EXE'] = str(path)
            return True
    finally:
        dialog.destroy()


def main():
    root = application_root()
    # windowed bootloaders have no stdout/stderr; preserve errors in local logs.
    if sys.stdout is None or sys.stderr is None:
        (root / 'logs').mkdir(exist_ok=True)
        output = (root / 'logs/launcher.log').open('a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stdout or output
        sys.stderr = sys.stderr or output
    try:
        if '--self-test' in sys.argv:
            from .portable_check import run
            run()
            return
        gui = len(sys.argv) == 1 or sys.argv[1] in ('gui', '--select-game')
        if gui:
            if not configure_game(root, force='--select-game' in sys.argv):
                return
            if '--select-game' in sys.argv:
                sys.argv = [sys.argv[0], 'gui']
        elif sys.argv[1] in ('check', 'observe', 'run', 'preview', 'forbidden') and not os.environ.get('BLACKFIRE_EXE'):
            value = saved_game(root)
            if value:
                os.environ['BLACKFIRE_EXE'] = str(value)
        # Import only after configuring the game path: runtime.EXE is bound once.
        from .__main__ import main as app_main
        app_main()
    except Exception:
        (root / 'logs').mkdir(exist_ok=True)
        report = root / 'logs/launcher-error.txt'
        report.write_text(traceback.format_exc(), encoding='utf-8')
        if len(sys.argv) == 1 or sys.argv[1] in ('gui', '--select-game'):
            from tkinter import messagebox
            messagebox.showerror('黑火守望启动失败', f'错误详情已保存：{report}\n\n{sys.exc_info()[1]}')
        raise


if __name__ == '__main__':
    main()
