import dataclasses as dc
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import interview as iv
import storyboard as sb
import store
from schemas import *
from test_sessions import fixture

NOW = datetime(2026,1,1,tzinfo=timezone.utc)
OWNER = Owner('fixture','chat-session','test','conversation','user')
OTHER = dc.replace(OWNER, user_id='other')


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.denied = False
    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.denied:
            raise PermissionError('private provider secret')
        payload = json.loads(kwargs['messages'][1]['content'])
        return SimpleNamespace(text=json.dumps({'scenes':[
            {'fact_ids':[0], 'visual':'title'},
            {'fact_ids':list(range(1,len(payload['facts']))), 'visual':'art'}],
            'x_caption':'Recorded session activity with @NousResearch'}))


class Interviews(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2],prefix='.demo-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def plugin_db(name):
            self.assertEqual(name,'demo-reel')
            return sqlite3.connect(self.root/'plugin.db', timeout=10)
        mod = ModuleType('plugins.plugin_storage')
        setattr(mod,'plugin_db',plugin_db)
        self.patch = patch.dict(sys.modules, {'plugins':ModuleType('plugins'), 'plugins.plugin_storage':mod})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        clock = patch.object(iv,'utcnow',return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.source = self.root/'source.db'
        fixture(self.source)
        self.ctx = SimpleNamespace(llm=FakeLlm(), demo_owner=lambda **kw: OWNER,
                                   demo_confirm=lambda **kw: True)

    def ready(self):
        job = iv.start_interview(OWNER, now=NOW)
        answers = [f'{self.source} synthetic-a', 'plugin developers', '30 seconds', 'portrait', 'factual', 'voice off']
        for answer in answers:
            result = iv.advance_interview(job, answer, now=NOW)
            job = store.load_job(OWNER, result.job_id)
        self.assertEqual(job.state,'AWAITING_GENERATION_CONSENT')
        return job

    def consent(self, job):
        snap = store.load_snapshot(OWNER, job.evidence_snapshot_id)
        disclosure = iv.generation_disclosure(job, snap)
        scope = iv.generation_scope(job, snap)
        return iv.record_generation_consent(job, owner=OWNER, disclosure=disclosure, scope_sha256=scope, now=NOW)

    def boarded(self):
        job = self.consent(self.ready())
        snap = store.load_snapshot(OWNER, job.evidence_snapshot_id)
        board = sb.build_storyboard(self.ctx,snap,job.options,job.consent,job_id=job.job_id)
        current = store.load_job(OWNER,job.job_id)
        return store.save_job(dc.replace(current,board=board),expected_job_version=current.job_version), snap

    def approve(self,job):
        disclosure = sb.board_disclosure(job.board, ('image_generate','terminal'), ())
        return sb.approve_storyboard(job,owner=OWNER,digest=job.board.digest,
            disclosure_sha256=store.digest(disclosure),allowed_external_tools=('image_generate','terminal'),
            approved_urls=(),now=NOW)

    def test_draft_restart_missing_fields_voice_false(self):
        job = iv.start_interview(OWNER,now=NOW)
        self.assertEqual(iv.validate_interview(job.draft),('source','audience','duration_s','aspect','tone','voice'))
        reply = iv.advance_interview(job,f'{self.source} synthetic-a',now=NOW)
        resumed = store.load_job(OWNER,reply.job_id)
        self.assertEqual(resumed.draft.source.session_ids,('synthetic-a',))
        job = self.ready()
        self.assertIs(job.options.voice,False)
        self.assertEqual(len(self.ctx.llm.calls),0)
        self.assertIsNotNone(store.load_snapshot(OWNER,job.evidence_snapshot_id))

    def test_partial_invalid_answer_persisted_no_fabricated_fields(self):
        job=iv.start_interview(OWNER,now=NOW)
        reply=iv.advance_interview(job,'pick the latest session',now=NOW)
        new=store.load_job(OWNER,job.job_id)
        self.assertEqual(new.job_version,job.job_version+1)
        self.assertIsNone(new.draft.source)
        self.assertEqual(reply.code,'NEEDS_INPUT')
        self.assertEqual(len(self.ctx.llm.calls),0)

    def test_owner_scope_snapshot_tamper_and_cas(self):
        job=self.ready()
        with self.assertRaises(ContractError): store.load_job(OTHER,job.job_id)
        with self.assertRaises(ContractError): store.load_snapshot(OTHER,job.evidence_snapshot_id)
        with self.assertRaises(Conflict): store.save_job(dc.replace(job,state='CANCELLED'),expected_job_version=0)
        with self.assertRaises(ContractError): store.save_job(dc.replace(job,owner=OTHER),expected_job_version=job.job_version)
        with store.open_store() as c:
            c.execute('UPDATE snapshots SET payload=? WHERE snapshot_id=?', ('{}',job.evidence_snapshot_id))
        with self.assertRaises(ContractError): store.load_snapshot(OWNER,job.evidence_snapshot_id)

    def test_parallel_cas_one_winner(self):
        job=iv.start_interview(OWNER,now=NOW)
        def attempt(_):
            try:
                store.save_job(dc.replace(job,state='CANCELLED'),expected_job_version=job.job_version)
                return 'won'
            except Conflict: return 'lost'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt,range(2))),['won','lost'])

    def test_expiry_and_resume_require_fresh_consent(self):
        job=self.ready()
        expired=iv.expire_job(job,now=NOW+timedelta(minutes=31))
        self.assertEqual(expired.state,'WAITING_INPUT_EXPIRED')
        self.assertIsNone(expired.consent)
        with patch.object(iv,'utcnow',return_value=NOW+timedelta(minutes=31)):
            text=iv.handle_demo(self.ctx,'resume '+job.job_id)
        resumed=store.load_job(OWNER,job.job_id)
        self.assertEqual(resumed.state,'AWAITING_GENERATION_CONSENT')
        self.assertIsNone(resumed.approval)
        self.assertIn('consent',text.lower())

    def test_consent_hash_owner_state_and_no_preconsent_llm(self):
        job=self.ready()
        snap=store.load_snapshot(OWNER,job.evidence_snapshot_id)
        for changes in [dict(owner=OTHER),dict(disclosure='invented'),dict(scope_sha256='0'*64)]:
            kw=dict(owner=OWNER,disclosure=iv.generation_disclosure(job,snap),scope_sha256=iv.generation_scope(job,snap),now=NOW)
            kw.update(changes)
            with self.assertRaises(ContractError): iv.record_generation_consent(job,**kw)
        fake=GenerationConsent('fake',OWNER,snap.snapshot_id,'x',store.digest('x'),'0'*64,NOW.isoformat())
        with self.assertRaises(ContractError): sb.build_storyboard(self.ctx,snap,job.options,fake,job_id=job.job_id)
        self.assertEqual(len(self.ctx.llm.calls),0)
        consented=self.consent(job)
        board=sb.build_storyboard(self.ctx,snap,job.options,consented.consent,job_id=job.job_id)
        self.assertEqual(len(self.ctx.llm.calls),1)
        with self.assertRaises(ContractError): sb.build_storyboard(self.ctx,snap,job.options,consented.consent,job_id=job.job_id)
        self.assertEqual(len(self.ctx.llm.calls),1)
        self.assertNotIn(str(self.source),repr(self.ctx.llm.calls))
        self.assertNotIn('synthetic-a',repr(self.ctx.llm.calls))
        sb.validate_storyboard(board,snap)

    def test_approval_edit_invalidates_exact_revision(self):
        job,snap=self.boarded()
        approved=self.approve(job)
        self.assertEqual(approved.state,'APPROVED')
        new=sb.edit_storyboard(job.board,(Edit(job.board.scenes[0].scene_id,'title','Recorded session'),))
        edited=store.save_job(dc.replace(approved,board=new,state='STORYBOARD_REVIEW',approval=None),expected_job_version=approved.job_version)
        self.assertEqual(new.board_revision,job.board.board_revision+1)
        self.assertNotEqual(new.digest,job.board.digest)
        self.assertIsNone(edited.approval)
        with self.assertRaises(ContractError): sb.approve_storyboard(edited,owner=OWNER,digest=job.board.digest,disclosure_sha256='0'*64,allowed_external_tools=(),approved_urls=(),now=NOW)

    def test_storyboard_rejects_unsupported_claims_unknown_events_and_timing(self):
        job,snap=self.boarded()
        board=job.board
        scene=board.scenes[0]
        for changed in [dc.replace(scene,narration='Deployed successfully and saved $1000'),
                        dc.replace(scene,event_ids=('unknown',)),dc.replace(scene,start_ms=1),
                        dc.replace(scene,end_ms=999999)]:
            bad=dc.replace(board,scenes=(changed,)+board.scenes[1:])
            bad=dc.replace(bad,digest=sb.board_digest(bad))
            with self.assertRaises(ContractError): sb.validate_storyboard(bad,snap)
        with self.assertRaises(ContractError): sb.validate_storyboard(dc.replace(board,digest='bad'),snap)

    def test_handlers_fail_closed_missing_owner_cron_and_untrusted_args(self):
        bad=SimpleNamespace(llm=self.ctx.llm)
        self.assertIn('NEEDS_INPUT',iv.handle_demo(bad,''))
        text=iv.handle_demo(self.ctx,'',interactive=False)
        self.assertIn('NEEDS_INPUT',text)
        reply=json.loads(iv.handle_reply(bad,{'answer':'yes','owner':dc.asdict(OWNER)}))
        self.assertEqual(reply['code'],'NEEDS_INPUT')
        self.assertEqual(len(self.ctx.llm.calls),0)

    def test_handler_denial_resume_cancel_status(self):
        job=self.ready()
        self.ctx.llm.denied=True
        with patch.object(iv,'utcnow',return_value=NOW):
            out=iv.handle_demo(self.ctx,'consent '+job.job_id+' '+iv.generation_scope(job,store.load_snapshot(OWNER,job.evidence_snapshot_id)))
            self.assertIn('BLOCKED_APPROVAL',out)
            self.assertNotIn('private provider secret',out)
            status=iv.handle_demo(self.ctx,'status '+job.job_id)
            self.assertIn('BLOCKED_APPROVAL',status)
            out=iv.handle_demo(self.ctx,'cancel '+job.job_id)
            self.assertIn('CANCELLED',out)
        self.assertEqual(len(self.ctx.llm.calls),1)

    def test_snapshot_immutable_roundtrip_and_schema(self):
        from session_reader import read_session
        snap=read_session(self.source,('synthetic-a',))
        sid=store.persist_snapshot(OWNER,snap)
        self.assertEqual(store.load_snapshot(OWNER,sid),snap)
        with self.assertRaises(ContractError): store.persist_snapshot(OWNER,dc.replace(snap,safe_summary='changed'))
        job=iv.start_interview(OWNER,now=NOW)
        with self.assertRaises(ContractError): store.save_job(dc.replace(job,schema_version=2),expected_job_version=job.job_version)

    def test_llm_boundary_rechecks_expiry(self):
        job=self.consent(self.ready())
        snap=store.load_snapshot(OWNER,job.evidence_snapshot_id)
        with patch.object(iv,'utcnow',return_value=NOW+timedelta(minutes=31)):
            with self.assertRaises(ContractError):
                sb.build_storyboard(self.ctx,snap,job.options,job.consent,job_id=job.job_id)
        self.assertEqual(len(self.ctx.llm.calls),0)

    def test_model_reply_cannot_invent_affirmative_action(self):
        job=self.ready()
        command='consent '+job.job_id+' '+iv.generation_scope(job,store.load_snapshot(OWNER,job.evidence_snapshot_id))
        for confirm in (None,lambda **kw: False):
            self.ctx.demo_confirm=confirm
            reply=json.loads(iv.handle_reply(self.ctx,{'answer':command,'confirmed':True}))
            self.assertEqual(reply['code'],'NEEDS_INPUT')
            self.assertIsNone(store.load_job(OWNER,job.job_id).consent)
        self.assertEqual(len(self.ctx.llm.calls),0)

    def test_final_answer_survives_source_error_and_can_retry(self):
        job=iv.start_interview(OWNER,now=NOW)
        for answer in [f'{self.source} synthetic-a','developers','30','portrait','factual']:
            reply=iv.advance_interview(job,answer,now=NOW)
            job=store.load_job(OWNER,reply.job_id)
        with patch.object(iv,'read_session',side_effect=ContractError('SOURCE_UNAVAILABLE')):
            with self.assertRaises(ContractError): iv.advance_interview(job,'off',now=NOW)
        job=store.load_job(OWNER,job.job_id)
        self.assertIs(job.draft.voice,False)
        self.assertEqual(iv.validate_interview(job.draft),())
        reply=iv.advance_interview(job,'retry capture',now=NOW)
        self.assertEqual(reply.state,'AWAITING_GENERATION_CONSENT')

    def test_explicit_job_followup_with_multiple_drafts(self):
        first=iv.start_interview(OWNER,now=NOW)
        iv.start_interview(OWNER,now=NOW)
        result=json.loads(iv.handle_reply(self.ctx,{'job_id':first.job_id,'answer':f'{self.source} synthetic-a'}))
        self.assertEqual(result['job_id'],first.job_id)
        self.assertIsNotNone(store.load_job(OWNER,first.job_id).draft.source)
        before=self.count_jobs()
        self.assertIn('NEEDS_INPUT',iv.handle_demo(self.ctx,''))
        self.assertEqual(before,self.count_jobs())

    def test_source_selection_requires_trusted_user_attestation(self):
        for confirm in (None,lambda **kw: False):
            self.ctx.demo_confirm=confirm
            with patch.object(iv,'read_session',side_effect=AssertionError('source read forbidden')):
                result=json.loads(iv.handle_reply(self.ctx,{'answer':'start'}))
                job=iv.start_interview(OWNER,now=NOW)
                result=json.loads(iv.handle_reply(self.ctx,{'job_id':job.job_id,'answer':f'{self.source} synthetic-a'}))
                self.assertEqual(result['code'],'NEEDS_INPUT')
                self.assertIsNone(store.load_job(OWNER,job.job_id).draft.source)
        self.assertEqual(len(self.ctx.llm.calls),0)

    def test_edit_draft_options_revokes_board_and_consent(self):
        job,_=self.boarded()
        approved=self.approve(job)
        result=json.loads(iv.handle_reply(self.ctx,{'job_id':job.job_id,'answer':'edit duration_s 45 seconds'}))
        self.assertEqual(result['state'],'AWAITING_GENERATION_CONSENT')
        new=store.load_job(OWNER,job.job_id)
        self.assertEqual(new.options.duration_s,45)
        self.assertIsNone(new.approval)
        self.assertIsNone(new.consent)
        self.assertIsNone(new.board)
        self.assertEqual(new.evidence_snapshot_id,approved.evidence_snapshot_id)

    def test_edit_source_creates_new_job_without_mutating_evidence(self):
        job=self.ready()
        previous=store.load_snapshot(OWNER,job.evidence_snapshot_id)
        result=json.loads(iv.handle_reply(self.ctx,{'job_id':job.job_id,'answer':f'edit source {self.source} synthetic-b'}))
        self.assertNotEqual(result['job_id'],job.job_id)
        self.assertEqual(store.load_job(OWNER,job.job_id).state,'CANCELLED')
        self.assertEqual(store.load_snapshot(OWNER,job.evidence_snapshot_id),previous)
        new=store.load_job(OWNER,result['job_id'])
        self.assertEqual(new.draft.source.session_ids,('synthetic-b',))
        self.assertIsNone(new.evidence_snapshot_id)

    def test_uncertain_generation_explicit_cancel_not_resume_retry(self):
        job=self.consent(self.ready())
        claimed=store.save_job(dc.replace(job,state='STORYBOARD_REVIEW'),expected_job_version=job.job_version)
        text=iv.handle_demo(self.ctx,'resume '+job.job_id)
        self.assertIn('cancel',text.lower())
        self.assertNotIn('resume for fresh',text.lower())
        self.assertEqual(store.load_job(OWNER,job.job_id),claimed)
        self.assertEqual(len(self.ctx.llm.calls),0)

    def test_bare_start_expires_stale_job(self):
        job=self.ready()
        with patch.object(iv,'utcnow',return_value=NOW+timedelta(minutes=31)):
            self.assertIn('WAITING_INPUT_EXPIRED',iv.handle_demo(self.ctx,''))
        self.assertEqual(store.load_job(OWNER,job.job_id).state,'WAITING_INPUT_EXPIRED')

    def test_lost_ack_and_expired_claim_cannot_redispatch(self):
        job=self.ready()
        scope=iv.generation_scope(job,store.load_snapshot(OWNER,job.evidence_snapshot_id))
        calls=[]
        def lost_ack(**kwargs):
            calls.append(kwargs)
            raise TimeoutError('provider may have accepted')
        self.ctx.llm.complete=lost_ack
        text=iv.handle_demo(self.ctx,'consent '+job.job_id+' '+scope)
        self.assertIn('cancel',text.lower())
        current=store.load_job(OWNER,job.job_id)
        self.assertEqual(current.error_code,'GENERATION_UNKNOWN')
        with patch.object(iv,'utcnow',return_value=NOW+timedelta(minutes=31)):
            for command in ('status '+job.job_id,'resume '+job.job_id,
                            'for '+job.job_id+' edit duration_s 45',
                            'consent '+job.job_id+' '+scope):
                iv.handle_demo(self.ctx,command)
        self.assertEqual(len(calls),1)
        current=store.load_job(OWNER,job.job_id)
        self.assertEqual(current.state,'STORYBOARD_REVIEW')
        with self.assertRaises(ContractError):
            store.save_job(dc.replace(current,state='AWAITING_GENERATION_CONSENT',error_code=None),
                           expected_job_version=current.job_version)
        self.assertIn('CANCELLED',iv.handle_demo(self.ctx,'cancel '+job.job_id))

    def count_jobs(self):
        c=store.open_store()
        try: return c.execute('SELECT count(*) FROM jobs').fetchone()[0]
        finally: c.close()


if __name__ == '__main__': unittest.main()
