"""The real API router with an in-memory stand-in; no payments or network."""
import unittest
from test_api_dub import Base, idem
from api_fake_ld import FakeLD
from api_testkit import Harness, OTHER


class LanguageLD(FakeLD):
    async def create(self, request, params):
        status, data = await super().create(request, params)
        if status == 200:
            self.jobs[data['id']]['source_language'] = params.get('source_language', 'en')
        return status, data

    async def switch_source(self, request, job_id, code):
        job = self.jobs[job_id]
        if job['status'] != 'language_mismatch':
            return 409, {'error':'This project is not paused for a language choice.'}
        job.update(source_language=code, status='accepted')
        job.pop('language_mismatch',None)
        self.calls.append('switch')
        return 200, {'id':job_id}


class ApiSourceLanguageTests(Base):
    def setUp(self):
        self.ld = LanguageLD(credits=1000)
        self.h = Harness(ld=self.ld, requests_per_minute=6000, burst=1000)
        self.key = self.h.make_key(scopes=('read','dub','account'),cap=500)
        self.hd = self.h.bearer(self.key)

    def test_optional_source_defaults_to_english(self):
        result = self.create()
        self.assertEqual(result.status_code,201,result.text)
        self.assertEqual(result.json()['source_language'],'en')

    def test_each_source_is_visible_in_poll_and_list_and_does_not_change_the_fee(self):
        for code in ('en','es','fr','pt','it','de','nl','pl'):
            result = self.create(source_language=code)
            self.assertEqual(result.status_code,201,result.text)
            jid = result.json()['id']
            self.assertEqual(result.json()['source_language'],code)
            self.assertEqual(self.get('/jobs/'+jid).json()['source_language'],code)
            self.upload(jid)
            self.assertEqual(self.quote(jid,'estimate')['quoted_credits'],3)
        self.assertEqual({j['source_language'] for j in self.get('/jobs').json()['jobs']}, {'en','es','fr','pt','it','de','nl','pl'})
        self.assertEqual(self.ld.credits,1000)

    def test_unknown_source_cannot_start_a_job(self):
        for code in ('xx','auto','FR',None):
            self.assertEqual(self.create(source_language=code).status_code,400)
        self.assertEqual(self.ld.jobs,{})

    def paused(self):
        jid = self.create().json()['id']
        self.ld.jobs[jid].update(status='language_mismatch',language_mismatch={'detected':'fr'},
            paid={'fee':3,'analysis':20,'dub':0})
        return jid

    def test_switch_is_owned_scoped_and_free(self):
        jid = self.paused(); path='/jobs/'+jid+'/source-language'
        readonly=self.h.bearer(self.h.make_key(scopes=('read',)))
        self.assertEqual(self.post(path,{'source_language':'fr'},headers=readonly).status_code,403)
        other=self.h.bearer(self.h.make_key(uid=OTHER,scopes=('read','dub')))
        self.assertEqual(self.post(path,{'source_language':'fr'},headers=other).status_code,404)
        result=self.post(path,{'source_language':'fr'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['source_language'],'fr')
        self.assertEqual(result.json()['status'],'accepted')
        self.assertEqual(self.ld.credits,1000)
        self.assertEqual(self.ld.jobs[jid]['paid'],{'fee':3,'analysis':20,'dub':0})
        self.assertEqual(self.ld.ledger,{})

    def test_switch_retry_returns_same_response_and_starts_only_once(self):
        jid=self.paused(); path='/jobs/'+jid+'/source-language'; key=idem()
        first=self.post(path,{'source_language':'fr'},k=key)
        again=self.post(path,{'source_language':'fr'},k=key)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(again.json(),first.json())
        self.assertEqual(self.ld.calls.count('switch'),1)
        self.assertEqual(self.post(path,{'source_language':'es'},k=key).status_code,409)

    def test_switch_needs_supported_code_and_paused_state(self):
        jid=self.create().json()['id']; path='/jobs/'+jid+'/source-language'
        self.assertEqual(self.post(path,{'source_language':'fr'}).status_code,409)
        self.assertEqual(self.post(path,{'source_language':'xx'}).status_code,400)
        self.assertNotIn('switch',self.ld.calls)


if __name__ == '__main__':
    unittest.main()
