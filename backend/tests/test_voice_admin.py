import asyncio
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import voice_admin as va
from test_shortdub_upgrade import source_functions


class VoiceRetentionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def test_default_two_days_and_saved_setting_survives_new_reader(self):
        self.assertEqual(va.retention_days(self.root), 2)
        va.save_settings(self.root, {'retention_days': 5})
        self.assertEqual(va.read_settings(self.root), {'retention_days': 5})

    def test_invalid_values_never_replace_saved_setting(self):
        va.save_settings(self.root, {'retention_days': 8})
        for value in [0, 31, 1.5, True, '2', None, float('nan')]:
            with self.assertRaises(ValueError): va.save_settings(self.root, {'retention_days': value})
        self.assertEqual(va.retention_days(self.root), 8)

    def test_corrupt_settings_do_not_silently_shorten_retention(self):
        (self.root / 'voice_management.json').write_text('damaged')
        with self.assertRaises(ValueError): va.retention_days(self.root)

    def test_last_correction_use_and_missing_nonfinite_timestamps(self):
        job = {'result': {'finished': 100}, 'edit_voice_last_used': 1000}
        self.assertEqual(va.expires_at(job, 2), 1000 + 2 * 86400)
        self.assertEqual(va.expires_at({'result': {'finished': 100}}, 2), 100 + 2 * 86400)
        for value in [None, float('nan'), float('inf'), 'invalid']:
            self.assertIsNone(va.expires_at({'edit_voice_last_used': value}, 2))

    def test_shorter_period_applies_to_existing_projects(self):
        voice = [{'id': 'v', 'name': 'lisan-tmp-a-s', 'source': 'IVC'}]
        job = {'id': 'a', 'status': 'done', 'edit_assets': True, 'dub': {'voices': {'s': 'v'}}, 'result': {'finished': 100}}
        self.assertEqual(va.classify(voice, [job], [], 100 + 3 * 86400, 7)['counts']['retained'], 1)
        self.assertEqual(va.classify(voice, [job], [], 100 + 3 * 86400, 2)['counts']['expired'], 1)

    def test_zero_is_a_valid_epoch_timestamp_and_can_expire(self):
        self.assertEqual(va.expires_at({'edit_voice_last_used': 0}, 2), 2 * 86400)

    def test_actual_housekeeping_uses_configured_period(self):
        import longdub_service as ld
        job = {'id': 'a', 'status': 'done', 'uid': 'u', 'edit_assets': True, 'voices_pending_delete': ['v'],
               'dub': {'voices': {'s': 'v'}}, 'result': {'finished': 100}}
        directory = Mock(); directory.name = 'a'; directory.is_dir.return_value = True
        longdir = Mock(); longdir.exists.return_value = True; longdir.iterdir.return_value = [directory]
        with patch.object(ld, 'LONG_DIR', longdir), patch.object(ld, 'load_job', return_value=job), \
             patch.object(ld, '_lock_for', return_value=va.nullcontext()), patch.object(ld, '_RUNNING', set()), \
             patch.object(ld, '_now', return_value=100 + 3 * 86400), patch.object(ld, '_save'), \
             patch('longdub_edits.active', return_value=False), patch.object(ld, '_delete_pending_voices') as delete:
            with patch.object(va, 'retention_days', return_value=7):
                ld.sweep_voices(); delete.assert_not_called()
            with patch.object(va, 'retention_days', return_value=2):
                ld.sweep_voices(); delete.assert_called_once_with(job)
        self.assertEqual(job['dub']['voices'], {})


class ProviderInventoryTests(unittest.TestCase):
    def test_all_pages_and_stock_exclusion(self):
        provider = Mock()
        provider._request.side_effect = [
            {'voices':[{'voiceId':'s','source':'SYSTEM'}, {'voiceId':'v1','displayName':'lisan-tmp-a','source':'IVC'}], 'nextPageToken':'token'},
            {'voices':[{'voiceId':'v2','displayName':'manual','source':'TVD'}, {'voiceId':'v1','source':'IVC'}]}]
        self.assertEqual({r['id'] for r in va.list_custom(provider, 'synthetic')}, {'v1','v2'})
        self.assertIn('pageToken=token', provider._request.call_args_list[1].args[1])

    def test_partial_or_looping_list_is_rejected(self):
        provider = Mock()
        provider._request.return_value = {'voices':[], 'nextPageToken':'again'}
        with self.assertRaises(ValueError): va.list_custom(provider, 'synthetic')
        provider._request.return_value = {'voices':None}
        with self.assertRaises(ValueError): va.list_custom(provider, 'synthetic')

    def test_provider_404_is_already_deleted_but_timeout_is_not_success(self):
        provider = Mock()
        provider._request.side_effect = HTTPError('https://example.test',404,'gone',{},io.BytesIO())
        self.assertTrue(va.delete_custom(provider,'key','voice'))
        provider._request.side_effect = TimeoutError()
        self.assertFalse(va.delete_custom(provider,'key','voice'))


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name); self.now = 100 + 3 * 86400
        self.voices = [{'id':'v1','name':'lisan-tmp-a-sp1','source':'IVC'},
                       {'id':'v2','name':'Custom_test','source':'IVC'},
                       {'id':'v3','name':'manual','source':'IVC'}]
        self.jobs = [{'id':'a','uid':'u','status':'done','edit_assets':True,'voices_pending_delete':['v1'],
                      'dub':{'voices':{'sp1':'v1','sp2':'another'}},'result':{'finished':100},'name':'Test'}]
        self.saved = [{'id':'row','uid':'u','elevenlabs_voice_id':'v2'}]
        self.deleted = []; self.fail = set(); self.is_busy = False
        def delete_saved(vid): self.saved[:] = [r for r in self.saved if r['elevenlabs_voice_id'] != vid]
        def delete_provider(vid):
            self.assertFalse(any(vid in (j.get('dub') or {}).get('voices',{}).values() for j in self.jobs))
            self.assertFalse(any(r['elevenlabs_voice_id'] == vid for r in self.saved))
            if vid in self.fail: return False
            self.deleted.append(vid); self.voices[:] = [r for r in self.voices if r['id'] != vid]; return True
        self.manager = va.Manager(self.root, lambda:copy.deepcopy(self.voices), lambda:self.jobs, lambda:self.saved,
                                  lambda job:None, delete_saved, delete_provider, busy=lambda:self.is_busy, clock=lambda:self.now)

    def test_inventory_separates_expired_saved_and_other(self):
        result = self.manager.inventory()
        self.assertEqual(result['total'],3)
        self.assertEqual(result['counts']['expired'],1); self.assertEqual(result['counts']['saved'],1)
        self.assertFalse(next(r for r in result['voices'] if r['id']=='v3')['selectable'])
        json.dumps(result, allow_nan=False)

    def test_preview_never_deletes_and_cancel_is_free(self):
        plan = self.manager.preview(['v1','v2'])
        self.assertEqual(plan['count'],2); self.assertEqual(self.deleted,[])
        self.assertEqual(self.jobs[0]['dub']['voices']['sp1'],'v1')
        self.assertEqual(len(self.saved),1)

    def test_completed_cleanup_clears_references_and_is_idempotent(self):
        token = self.manager.preview(['v1','v2'])['token']
        result = self.manager.execute(token)
        self.assertEqual(result['state'],'complete'); self.assertEqual(result['deleted'],2)
        self.assertEqual(self.jobs[0]['dub']['voices'],{'sp2':'another'})
        self.assertEqual(self.jobs[0]['voices_pending_delete'],[])
        self.assertEqual(self.manager.execute(token),result); self.assertEqual(self.deleted,['v1','v2'])

    def test_provider_failure_leaves_durable_retry_and_pending_id(self):
        token = self.manager.preview(['v1'])['token']; self.fail.add('v1')
        result = self.manager.execute(token)
        self.assertEqual(result['deleted'],0); self.assertEqual(result['failed'],1)
        self.assertIn('v1',self.jobs[0]['voices_pending_delete'])
        self.assertNotIn('sp1',self.jobs[0]['dub']['voices'])
        self.assertIn(token,self.manager.inventory()['pending_cleanup'])
        self.fail.clear(); self.now += 10000
        self.assertEqual(self.manager.execute(token)['state'],'complete')

    def test_active_project_and_other_workspace_voice_cannot_be_selected(self):
        with self.assertRaises(ValueError): self.manager.preview(['v3'])
        self.jobs[0]['status']='dubbing'
        with self.assertRaises(ValueError): self.manager.preview(['v1'])

    def test_busy_or_expired_preview_deletes_nothing(self):
        token = self.manager.preview(['v1'])['token']
        self.is_busy = True
        with self.assertRaises(ValueError): self.manager.execute(token)
        self.is_busy = False; self.now += 301
        with self.assertRaises(ValueError): self.manager.execute(token)
        self.assertEqual(self.deleted,[])

    def test_reference_or_saved_owner_change_requires_new_preview(self):
        token = self.manager.preview(['v2'])['token']
        self.saved[0]['id']='different-row'
        with self.assertRaises(ValueError): self.manager.execute(token)
        self.assertEqual(self.deleted,[])

    def test_unverified_inventory_blocks_preview(self):
        self.manager.saved = Mock(side_effect=ValueError('unavailable'))
        with self.assertRaises(ValueError): self.manager.preview(['v1'])
        self.assertEqual(self.deleted,[])

    def test_invalid_token_cannot_read_arbitrary_file(self):
        for token in ['../voice_management', '', 'z'*32]:
            with self.assertRaises(ValueError): self.manager.status(token)


class RouteSafetyTests(unittest.TestCase):
    def test_cleanup_pauses_mutations_but_keeps_status_reads_available(self):
        import threading
        from types import SimpleNamespace
        class Response:
            def __init__(self, body, status_code=200): self.status_code=status_code
        state={'running':True,'requests':0}
        env=source_functions('main.py',['voice_cleanup_gate'],{'_voice_admin_state_lock':threading.Lock(),
            '_voice_admin_state':state,'JSONResponse':Response})
        async def next_call(request): return Response({},200)
        for method, expected in [('POST',503),('GET',200)]:
            request=SimpleNamespace(method=method,url=SimpleNamespace(path='/api/generate'))
            coroutine=env['voice_cleanup_gate'](request,next_call)
            with self.assertRaises(StopIteration) as stopped: coroutine.send(None)
            self.assertEqual(stopped.exception.value.status_code,expected)
        self.assertEqual(state['requests'],0)

    def test_worker_launch_failure_does_not_leave_site_paused(self):
        import threading
        from types import SimpleNamespace
        class Response:
            def __init__(self,body,status_code=200): self.status_code=status_code
        class Request:
            async def json(self): return {'token':'a'*32}
        state={'running':False,'requests':0}
        thread=Mock(); thread.start.side_effect=RuntimeError('synthetic launch failure')
        env=source_functions('main.py',['admin_voice_cleanup'],{'_admin_check':lambda req:True,
            '_voice_admin_state_lock':threading.Lock(),'_voice_admin_state':state,
            '_voice_admin_busy':lambda:False,'_voice_admin_manager':lambda:Mock(),
            'threading':SimpleNamespace(Thread=lambda **kwargs:thread),'JSONResponse':Response})
        coroutine=env['admin_voice_cleanup'](Request())
        with self.assertRaises(StopIteration) as stopped: coroutine.send(None)
        self.assertEqual(stopped.exception.value.status_code,400)
        self.assertFalse(state['running'])

    def test_all_admin_endpoints_require_admin_before_any_provider_work(self):
        class Response:
            def __init__(self,body,status_code=200,**kwargs): self.status_code=status_code
        names = ['admin_voice_management','admin_voice_settings','admin_voice_inventory','admin_voice_preview','admin_voice_cleanup','admin_voice_cleanup_status']
        env = source_functions('main.py',names,{'_admin_check':lambda request:False,'JSONResponse':Response})
        for name in names:
            args = ('a'*32,object()) if name.endswith('_status') else (object(),)
            result = env[name](*args)
            if asyncio.iscoroutine(result):
                # The unauthorized branch has no awaits; no network-backed event loop is needed.
                with self.assertRaises(StopIteration) as stopped: result.send(None)
                result = stopped.exception.value
            self.assertEqual(result.status_code,401,name)


if __name__ == '__main__': unittest.main()
