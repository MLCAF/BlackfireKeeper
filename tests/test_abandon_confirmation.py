"""Abandon confirmation is guarded, single-submit and never counts a victory."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from bk2.battle import ABANDON_PROMPT, BattleRunner, DEFEAT
from vendor_core.backend import MaaRecognizer, Match
from tests.test_defeat_recovery import Clock, DefeatBackend, profile
from tests.test_unified_replay import Offline

ROOT = Path(__file__).resolve().parents[1]
CONFIRM = '放弃冒险确认文字.png'


class ConfirmationBackend(DefeatBackend):
    def __init__(self, destination='list', wrong=False, ignored=False, pause=False,
                 stop=False, flash=False, **kwargs):
        super().__init__(exit_page='popup', **kwargs)
        self.destination, self.wrong, self.ignored = destination, wrong, ignored
        self.pause, self.stop, self.flash = pause, stop, flash
        self.popup_frames = 0

    def capture(self, stop):
        if self.page == 'popup':
            self.popup_frames += 1
            if self.frames:
                return self.frames['popup']
            return np.full((720,1395,3), 6, np.uint8)
        if self.page == 'home':
            return np.full((720,1395,3), 7, np.uint8)
        return super().capture(stop)

    def recognize(self, frame, rule, stop):
        if self.reco:
            return self.reco.recognize(frame, rule, stop)
        if int(frame[0,0,0]) == 6:
            template = rule['template']
            # Defeat remains visible under the modal, reproducing the bug.
            if template == DEFEAT['template']:
                return Match((565,280,200,50),1.)
            if template == ABANDON_PROMPT['template']:
                if self.wrong or (self.flash and self.popup_frames > 1):
                    return None
                return Match((620,310,252,49),1.)
            if template == CONFIRM:
                if rule.get('requires_match') and not self.recognize(frame,rule['requires_match'],stop):
                    return None
                return Match((750,415,40,24),1.)
        if int(frame[0,0,0]) == 7 and rule['template'] == '主界面锚点.png':
            return Match((500,600,50,50),1.)
        return super().recognize(frame,rule,stop)

    def click(self, match, offset, stop, pause):
        if self.page == 'popup':
            self.clicks.append('confirm')
            if not self.ignored:
                self.page = self.destination
            if self.pause:
                pause.set()
            if self.stop:
                stop.set()
        else:
            super().click(match,offset,stop,pause)


def run_backend(backend, dry=False, config=None):
    runner = BattleRunner(backend,config or profile(),Mock(),dry,Clock())
    backend.runner = runner
    with patch.object(runner.stop_event,'wait',side_effect=lambda _:runner.stop_event.is_set()), \
            patch('bk2.battle.MaaBackend.is_nearly_uniform',side_effect=lambda im:not im.any()):
        runner.run()
    return runner


class AbandonConfirmationTests(unittest.TestCase):
    def test_three_restarts_abandon_confirm_list_is_abandoned_not_completed(self):
        backend = ConfirmationBackend()
        runner = run_backend(backend)
        self.assertEqual(backend.clicks,['restart']*3+['abandon','confirm'],runner.reason)
        self.assertTrue(runner.abandoned,runner.reason)
        self.assertFalse(runner.completed)
        self.assertEqual(runner.stats.rounds,0)
        self.assertEqual(runner.counters['abandon_confirm_requests'],1)
        self.assertTrue(runner.abandon_confirmation_sent)
        backend.save_diagnostic.assert_not_called()

    def test_home_return_is_also_verified(self):
        backend = ConfirmationBackend(destination='home')
        self.assertTrue(run_backend(backend).abandoned)
        self.assertEqual(backend.clicks.count('confirm'),1)

    def test_existing_direct_return_remains_supported(self):
        backend = DefeatBackend()
        runner = run_backend(backend)
        self.assertTrue(runner.abandoned,runner.reason)
        self.assertEqual(backend.clicks,['restart']*3+['abandon'])

    def test_wrong_confirmation_does_not_receive_input(self):
        backend = ConfirmationBackend(wrong=True)
        runner = run_backend(backend)
        self.assertEqual(backend.clicks,['restart']*3+['abandon'])
        self.assertFalse(runner.abandoned)
        backend.save_diagnostic.assert_called_once()

    def test_one_frame_prompt_does_not_trigger_confirmation(self):
        backend = ConfirmationBackend(flash=True)
        runner = run_backend(backend)
        self.assertFalse(runner.abandoned)
        self.assertNotIn('confirm',backend.clicks)

    def test_ignored_confirmation_is_not_resubmitted(self):
        backend = ConfirmationBackend(ignored=True)
        runner = run_backend(backend)
        self.assertEqual(backend.clicks,['restart']*3+['abandon','confirm'])
        self.assertFalse(runner.abandoned)
        self.assertIn('未连续两帧确认返回',runner.reason)
        backend.save_diagnostic.assert_called_once()

    def test_unknown_or_black_destination_is_not_an_exit(self):
        for destination in ('unknown','black'):
            backend = ConfirmationBackend(destination=destination)
            runner = run_backend(backend)
            self.assertFalse(runner.abandoned, destination)
            self.assertFalse(runner.completed)
            self.assertEqual(backend.clicks.count('confirm'),1)
            backend.save_diagnostic.assert_called_once()

    def test_pause_after_confirmation_never_resubmits(self):
        backend = ConfirmationBackend(pause=True)
        runner = run_backend(backend)
        self.assertEqual(backend.clicks.count('confirm'),1)
        self.assertFalse(runner.abandoned)
        self.assertIn('暂停',runner.reason)

    def test_stop_after_confirmation_never_resubmits(self):
        backend = ConfirmationBackend(stop=True)
        runner = run_backend(backend)
        self.assertEqual(backend.clicks.count('confirm'),1)
        self.assertFalse(runner.abandoned)
        self.assertEqual(runner.reason,'操作已中止')

    def test_preview_has_no_clicks(self):
        backend = ConfirmationBackend()
        runner = run_backend(backend,dry=True)
        self.assertEqual(backend.clicks,[])
        self.assertFalse(runner.abandon_confirmation_sent)

    def test_confirmation_requires_three_restarts_and_own_abandon_request(self):
        runner = BattleRunner(ConfirmationBackend(),profile(),Mock(),False,Clock())
        runner.stats.state = 'DefeatReturn'
        rule = next(r for r in profile()['states']['DefeatReturn']['rules'] if r.get('defeat_action'))
        for restarts,requested,sent,allowed in [(0,False,False,False),(3,False,False,False),
                (2,True,False,False),(3,True,True,False),(3,True,False,True)]:
            runner.battle_restarts,runner.abandon_requested = restarts,requested
            runner.abandon_confirmation_sent = sent
            self.assertEqual(any(r.get('defeat_action') == 'confirm_abandon' for r in runner.ordered_rules()),allowed)
            if not allowed:
                with self.assertRaisesRegex(RuntimeError,'不一致'):
                    runner.click_with_change_confirmation(rule,Match((750,415,40,24),1.))
        self.assertEqual(runner.backend.clicks,[])

    def test_no_popup_confirmation_in_progress_even_after_manual_modal(self):
        backend = ConfirmationBackend()
        backend.page = 'popup'
        runner = run_backend(backend)
        self.assertFalse(runner.abandoned)
        self.assertEqual(backend.clicks,[])

    def test_stale_confirmation_target_is_revalidated_before_click(self):
        backend = ConfirmationBackend()
        runner = BattleRunner(backend,profile(),Mock(),False,Clock())
        runner.battle_restarts,runner.abandon_requested = 3,True
        rule = next(r for r in profile()['states']['DefeatReturn']['rules'] if r.get('defeat_action'))
        with patch.object(runner,'refresh_match_before_click',return_value=None):
            self.assertFalse(runner.click_with_change_confirmation(rule,Match((750,415,40,24),1.)))
        self.assertEqual(backend.clicks,[])
        self.assertFalse(runner.abandon_confirmation_sent)

    def test_single_destination_frame_does_not_confirm_exit(self):
        backend = ConfirmationBackend()
        original = backend.capture
        seen = 0
        def capture(stop):
            nonlocal seen
            if backend.page == 'list':
                seen += 1
                if seen > 1: backend.page = 'unknown'
            return original(stop)
        backend.capture = capture
        runner = run_backend(backend)
        self.assertFalse(runner.abandoned)
        self.assertEqual(backend.clicks.count('confirm'),1)
        backend.save_diagnostic.assert_called_once()


class AbandonTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline(); controller.post_connection().wait()
        cls.reco = MaaRecognizer(ROOT/'assets',controller)
        cls.rule = next(r for r in profile()['states']['DefeatReturn']['rules'] if r.get('defeat_action'))

    @classmethod
    def tearDownClass(cls):
        cls.reco.cancel().wait()

    def frame(self, prompt=True, button=True, dim=False):
        frame = np.full((720,1395,3),45,np.uint8)
        for name,origin,enabled in [(ABANDON_PROMPT['template'],(620,310),prompt),(CONFIRM,(750,415),button)]:
            if enabled:
                with Image.open(ROOT/'assets/image'/name) as im:
                    pixels = np.asarray(im.convert('RGB'))[:,:,::-1].copy()
                if dim: pixels = (pixels*.45).astype(np.uint8)
                x,y=origin; h,w=pixels.shape[:2]
                frame[y:y+h,x:x+w]=pixels
        return frame

    def test_guarded_template_matches_both_message_and_button(self):
        match = self.reco.recognize(self.frame(),self.rule,threading.Event())
        self.assertIsNotNone(match)
        self.assertEqual(match.box[:2],(750,415))
        self.assertGreaterEqual(match.score,.95)

    def test_generic_confirm_alone_prompt_alone_and_dimmed_modal_rejected(self):
        for frame in (self.frame(prompt=False),self.frame(button=False),self.frame(dim=True)):
            self.assertIsNone(self.reco.recognize(frame,self.rule,threading.Event()))

    @unittest.skipUnless((ROOT/'tests/fixtures/abandon_confirm_20261018/popup_720_scale.png').exists(),
                         'Private user popup is not distributed')
    def test_uploaded_popup_after_documented_scale_calibration(self):
        with Image.open(ROOT/'tests/fixtures/abandon_confirm_20261018/popup_720_scale.png') as im:
            pixels = np.asarray(im.convert('RGB'))[:,:,::-1].copy()
        frame = np.full((720,1395,3),45,np.uint8)
        frame[250:461,490:900] = pixels
        match = self.reco.recognize(frame,self.rule,threading.Event())
        self.assertIsNotNone(match)
        self.assertTrue(750 <= match.box[0] <= 790)
        frame[312:361,622:874]=45  # Remove the specific warning, retain the button.
        self.assertIsNone(self.reco.recognize(frame,self.rule,threading.Event()))

    @unittest.skipUnless((ROOT/'tests/fixtures/abandon_confirm_20261018/popup_720_scale.png').exists()
                         and (ROOT/'tests/fixtures/defeat_20261018/defeat.png').exists(),
                         'Private game frames are not distributed')
    def test_pixel_replay_three_restarts_prompt_confirmation_then_list(self):
        def read(name):
            with Image.open(ROOT/'tests/fixtures'/name) as im:
                return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
        frames = dict(defeat=read('defeat_20261018/defeat.png'),
                      battle=read('capture_20261018/room.png'),
                      list=read('failures_20261003/dungeon_list.png'))
        frames['popup'] = (frames['defeat']*.55).astype(np.uint8)
        frames['popup'][250:461,490:900] = read('abandon_confirm_20261018/popup_720_scale.png')
        for frame in list(frames.values())[:3]+[read('diamond_fix_20261018/reward.png'),
                                               read('food_20261018/reward-maa.png')]:
            self.assertIsNone(self.reco.recognize(frame,self.rule,threading.Event()))
        backend = ConfirmationBackend(reco=self.reco,frames=frames)
        runner = run_backend(backend)
        self.assertTrue(runner.abandoned,runner.reason)
        self.assertFalse(runner.completed)
        self.assertEqual(backend.clicks,['restart']*3+['abandon','confirm'])
        self.assertEqual(runner.stats.rounds,0)


if __name__ == '__main__':
    unittest.main()
