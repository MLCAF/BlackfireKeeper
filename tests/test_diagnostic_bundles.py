"""Both diagnostic entry points keep each incident self-contained."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from bk2.backend import DiagnosticBackend
from bk2.diagnostics import save_failure


def backend(root):
    value=DiagnosticBackend.__new__(DiagnosticBackend)
    value.diagnostic_root=Path(root)
    value.last_image=np.full((8,9,3),[10,20,30],dtype=np.uint8)
    value.last_capture=time.monotonic()
    value.recognition_trace=[{'name':'current','accepted':False}]
    value._previous_image=np.full((8,9,3),[40,50,60],dtype=np.uint8)
    value._previous_capture=time.monotonic()-1
    value._previous_recognition=[{'name':'previous','accepted':True}]
    return value


class DiagnosticBundleTests(unittest.TestCase):
    def test_runner_report_and_both_frames_are_in_one_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);b=backend(root)
            target=Path(b.save_diagnostic('Progress','timeout',{'threshold':.95}))
            report=json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(target.name,'report.json')
            self.assertEqual(target.parent.parent,root)
            self.assertEqual({p.name for p in target.parent.iterdir()},{'report.json','screen.png','previous.png'})
            self.assertEqual(report['screenshot'],'screen.png')
            self.assertEqual(report['previous_frame_recognition']['screenshot'],'previous.png')
            self.assertEqual(report['config'],{'threshold':.95})
            for name,expected in [('screen.png',b.last_image),('previous.png',b._previous_image)]:
                with Image.open(target.parent/name) as image:
                    np.testing.assert_array_equal(np.asarray(image)[:,:,::-1],expected)
            self.assertEqual([p.name for p in root.iterdir() if p.is_file()],['latest.txt'])
            self.assertEqual((root/'latest.txt').read_text(),str(target))

    def test_runner_and_session_sequential_reports_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);diagnostics=root/'logs/diagnostics'
            first=Path(backend(diagnostics).save_diagnostic('Progress','first',{}))
            original=first.read_bytes()
            second=save_failure(root,'run','error','second',image=np.zeros((8,9,3),dtype=np.uint8))
            self.assertNotEqual(first.parent,second.parent)
            self.assertEqual(first.read_bytes(),original)
            self.assertEqual((diagnostics/'latest.txt').read_text(),str(second))
            self.assertEqual({p.name for p in second.parent.iterdir()},{'report.json','screen.png'})

    def test_no_image_still_produces_report_without_external_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=DiagnosticBackend.__new__(DiagnosticBackend);b.diagnostic_root=Path(tmp)
            target=Path(b.save_diagnostic('Entry','failed before capture',{}))
            report=json.loads(target.read_text(encoding='utf-8'))
            self.assertIsNone(report['screenshot'])
            self.assertIn('screenshot_unavailable',report)
            self.assertEqual({p.name for p in target.parent.iterdir()},{'report.json'})

    def test_failed_screenshots_do_not_lose_error_or_recognition(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(Image.Image,'save',side_effect=OSError('image write failed')):
            target=Path(backend(tmp).save_diagnostic('Progress','original failure',{}))
            report=json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(report['reason'],'original failure')
            self.assertEqual(report['screenshot_error'],'image write failed')
            self.assertEqual(report['previous_frame_recognition']['screenshot_error'],'image write failed')
            self.assertEqual(report['previous_frame_recognition']['recognition'][0]['name'],'previous')

    def test_concurrent_bundles_have_unique_files_and_complete_latest_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths=list(pool.map(lambda i:save_failure(root,str(i),'test',str(i)),range(12)))
            self.assertEqual(len(set(paths)),12)
            latest=Path((root/'logs/diagnostics/latest.txt').read_text())
            self.assertIn(latest,paths)
            self.assertIn('error',json.loads(latest.read_text(encoding='utf-8')))
            self.assertEqual([p.name for p in (root/'logs/diagnostics').iterdir() if p.is_file()],['latest.txt'])
            self.assertFalse(list(root.rglob('*.tmp')))
