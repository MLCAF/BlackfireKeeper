import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock,patch
from bk2.notifications import TaskSounds,load_enabled,save_enabled

ROOT=Path(__file__).resolve().parents[1]

class NotificationTests(unittest.TestCase):
    def test_event_policy_deduplicates_errors_and_ignores_preview_stop_and_substeps(self):
        player=Mock();clock=Mock(return_value=100)
        sound=TaskSounds(ROOT,clock=clock,player=player)
        for kind,data in [('finished','one battle'),('schedule',{'phase':'completed'}),
                          ('batch_result',{'succeeded':False}),
                          ('batch_result',{'succeeded':True,'preview':True})]:sound.observe(kind,data)
        player.assert_not_called()
        sound.observe('error','timeout');sound.observe('memory_error','same failure')
        player.assert_called_once_with('error')
        clock.return_value=111;sound.observe('error','later failure')
        self.assertEqual(player.call_count,2)
        sound.observe('batch_result',{'succeeded':True});sound.observe('batch_result',{'succeeded':True})
        self.assertEqual(player.call_count,3);player.assert_called_with('completed')
        sound.enabled=False;clock.return_value=200;sound.observe('error','muted')
        self.assertEqual(player.call_count,3)
        sound.play('completed');self.assertEqual(player.call_count,4)

    def test_audio_failure_only_logs(self):
        log=Mock();sound=TaskSounds(ROOT,player=Mock(side_effect=OSError('no device')),log=log)
        sound.observe('error','oops');log.assert_called_once()

    def test_settings_persist_independently_and_invalid_file_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.assertTrue(load_enabled(root))
            (root/'config/runtime').mkdir(parents=True)
            protected=root/'config/runtime/food_schedule.json';protected.write_bytes(b'untouched')
            target=root/'config/runtime/notifications.json';target.write_text('{"other": 7}')
            save_enabled(root,False);self.assertFalse(load_enabled(root))
            self.assertEqual(json.loads(target.read_text())['other'],7)
            self.assertEqual(protected.read_bytes(),b'untouched')
            target.write_text('broken')
            with self.assertRaises(ValueError):save_enabled(root,True)
            self.assertEqual(target.read_text(),'broken')

    def test_shipped_tones_are_valid_short_pcm_and_play_is_asynchronous(self):
        import winsound
        for name in ['error','completed']:
            path=ROOT/'assets/sounds'/f'{name}.wav'
            with wave.open(str(path)) as wav:
                self.assertEqual((wav.getnchannels(),wav.getsampwidth()),(1,2))
                self.assertTrue(.3<wav.getnframes()/wav.getframerate()<1)
            with patch('winsound.PlaySound') as play:
                TaskSounds(ROOT).play(name)
                self.assertEqual(play.call_args.args[0],str(path))
                self.assertTrue(play.call_args.args[1]&winsound.SND_ASYNC)
