import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch


class ReleaseLookupTests(unittest.TestCase):
    def module(self):
        path=Path(__file__).resolve().parents[1]/'packaging/publish_portable.py'
        spec=importlib.util.spec_from_file_location('release_lookup_test',path)
        module=importlib.util.module_from_spec(spec)
        with patch.dict(os.environ,{'EXPECTED_REPO':'MLCAF/BlackfireKeeper','EXPECTED_PRIVATE':'false',
                                   'RELEASE_TAG':'v0.8.1','RELEASE_SHA':'0'*40,'ARTIFACT_PREFIX':'BlackfireKeeper'}):
            spec.loader.exec_module(module)
        return module

    def test_draft_is_loaded_by_id_without_release_list(self):
        m=self.module();m.api=Mock(return_value={'id':123,'draft':True})
        with patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"databaseId":123}')):
            self.assertEqual(m.release_record()['id'],123)
        self.assertIn('/releases/123?',m.api.call_args.args[0])
        self.assertNotIn('per_page',m.api.call_args.args[0])

    def test_only_real_not_found_is_treated_as_missing(self):
        m=self.module()
        for error,missing in [('release not found',True),('authentication failed',False)]:
            with patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr=error)):
                if missing:self.assertIsNone(m.release_record())
                else:
                    with self.assertRaises(RuntimeError):m.release_record()

    def test_metadata_wait_is_bounded(self):
        m=self.module();m.release_record=Mock(side_effect=[{'assets':[]},{'assets':[1,2]}])
        with patch.object(m.time,'sleep'):
            self.assertEqual(m.wait_release(lambda x:len(x['assets'])==2)['assets'],[1,2])
        m.release_record=Mock(return_value={'assets':[]})
        with patch.object(m.time,'sleep'),self.assertRaises(RuntimeError):m.wait_release(lambda x:len(x['assets'])==2)
        self.assertEqual(m.release_record.call_count,6)
