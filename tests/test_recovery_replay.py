"""Replay actual frames through MaaFramework; controller cannot click."""
import json
import threading
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from maa.controller import CustomController
from vendor_core.backend import MaaRecognizer

ROOT = Path(__file__).resolve().parents[1]


class Offline(CustomController):
    def connect(self): return True
    def request_uuid(self): return 'bk2-recovery-replay'
    def get_features(self): return 0
    def screencap(self): raise AssertionError('Replay must not capture live game')
    def click(self, *args): raise AssertionError('Replay must not click')


@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class RecoveryReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = Offline()
        cls.controller.post_connection().wait()
        cls.reader = MaaRecognizer(ROOT / 'assets', cls.controller)
        config = json.loads((ROOT / 'config/dungeon.json').read_text(encoding='utf-8'))
        cls.rule = config['states']['OpeningBuffClose']['rules'][0]

    @classmethod
    def tearDownClass(cls):
        cls.reader.cancel().wait()

    def frame(self, name):
        with Image.open(ROOT / 'tests/fixtures' / name) as im:
            return np.asarray(im.convert('RGB'))[:, :, ::-1].copy()

    def test_manual_close_recognized_without_another_click(self):
        self.assertEqual(self.rule['action'], 'none')
        match = self.reader.recognize(self.frame('entrance_after_manual_close.png'), self.rule, threading.Event())
        self.assertIsNotNone(match)
        self.assertGreaterEqual(match.score, 0.95)

    def test_portal_cannot_be_misread_as_room(self):
        match = self.reader.recognize(self.frame('portal.png'), self.rule, threading.Event())
        self.assertIsNone(match)


if __name__ == '__main__': unittest.main()
