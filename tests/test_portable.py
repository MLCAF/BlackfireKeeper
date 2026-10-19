import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bk2.paths import application_root
from bk2.portable import configure_game, saved_game


class PortablePathsTests(unittest.TestCase):
    def test_source_root_does_not_depend_on_working_directory(self):
        self.assertEqual(application_root(), Path(__file__).resolve().parents[1])

    def test_frozen_root_uses_executable_not_internal_folder(self):
        with patch('sys.frozen', True, create=True), patch('sys.executable', 'C:/portable/BlackfireKeeper.exe'):
            self.assertEqual(application_root(), Path('C:/portable').resolve())

    def test_saved_path_used_without_writing_settings_or_timers(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            exe = root / 'Blackfire Crusade.exe'
            exe.touch()
            target = root / 'config/runtime/installation.json'
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({'game_exe': str(exe)}), encoding='utf-8')
            before = target.read_bytes()
            self.assertEqual(saved_game(root), exe)
            with patch('tkinter.Tk') as tk:
                self.assertTrue(configure_game(root))
                tk.assert_not_called()
            self.assertEqual(os.environ['BLACKFIRE_EXE'], str(exe.resolve()))
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list((root / 'config/runtime').iterdir()), [target])

    def test_cancel_picker_creates_no_runtime_state(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
                patch('tkinter.Tk'), patch('tkinter.filedialog.askopenfilename', return_value=''):
            root = Path(directory)
            self.assertFalse(configure_game(root, force=True))
            self.assertFalse((root / 'config/runtime').exists())

    def test_select_game_preserves_existing_cooldowns(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
                patch('tkinter.Tk'):
            root = Path(directory)
            exe = root / 'Blackfire Crusade.exe'
            exe.touch()
            folder = root / 'config/runtime'
            folder.mkdir(parents=True)
            timer = folder / 'food_schedule.json'
            timer.write_text('{"next_check": 123}', encoding='utf-8')
            with patch('tkinter.filedialog.askopenfilename', return_value=str(exe)):
                self.assertTrue(configure_game(root, force=True))
            self.assertEqual(saved_game(root), exe.resolve())
            self.assertEqual(timer.read_text(encoding='utf-8'), '{"next_check": 123}')

    def test_distribution_filters_local_data_even_if_tracked(self):
        import runpy
        build = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'packaging/build_portable.py'))
        allow = build['distributable']
        for name in ('config/runtime/food_schedule.json', 'logs/a.json', 'tests/fixtures/private.png',
                     '.venv/a.py', '.env', 'dump.dmp'):
            self.assertFalse(allow(Path(name)), name)
        self.assertTrue(allow(Path('assets/image/一本名称.png')))

    def test_distribution_plan_is_public_head_not_working_copy(self):
        import runpy
        build = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'packaging/build_portable.py'))
        with patch('subprocess.check_output', return_value=b'public default') as read:
            self.assertEqual(build['source_bytes'](Path('config/dungeon_plan.json')), b'public default')
        self.assertEqual(read.call_args.args[0], ['git', 'show', 'HEAD:config/dungeon_plan.json'])
