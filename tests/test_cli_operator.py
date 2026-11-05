"""Offline CLI boundaries; provider mocks are never release evidence."""
import argparse
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from cli import setup_cli, run_cli


def args(*values):
    p = argparse.ArgumentParser()
    setup_cli(p)
    return p.parse_args(values)


class OperatorCliTests(unittest.TestCase):
    def test_script_without_explicit_source_consent_never_reads_db(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'answers.json'
            f.write_text(json.dumps({'aspect':'16:9','duration_s':30,'voice':True,'tone':'builder log','audience':'builders'}))
            with patch('interview.read_session') as capture:
                self.assertEqual(run_cli(args('interview','--operator','hive','--answers',str(f),'--db',str(Path(d)/'state.db'),'--session-id','s')),2)
                capture.assert_not_called()

    def test_unattended_render_has_no_host_dispatch(self):
        ctx = SimpleNamespace(dispatch_tool=lambda *a,**k: self.fail('no dispatch'))
        self.assertEqual(run_cli(args('render','--operator','hive','--job','j'),ctx=ctx),2)

    def test_canary_scan_rejects_private_material(self):
        from operator_cli import canary_scan
        for text in ['alice@example.org','/Users/alice/private','sk-proj-123456789012345678901234','Bearer abcdefghijklmnopqrstuvwxyz']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                canary_scan(text)
        canary_scan('Recorded terminal invocation; reported status ok. @NousResearch')

    def test_config_answers_no_private_ids(self):
        from operator_cli import read_answers
        options = read_answers(Path(__file__).resolve().parents[1]/'examples/v0-answers.json')
        self.assertEqual(options.aspect,'16:9')
        self.assertTrue(options.voice)
        self.assertEqual(options.tone,'builder log')
        self.assertIsInstance(options.duration_s,int)
        self.assertLessEqual(options.duration_s or 0,90)

    def test_probe_requires_codecs_audio_and_strict_duration(self):
        from scripts.probe_outcome import validate_probe,validate_volume
        streams = [{'codec_type':'video','codec_name':'h264'},{'codec_type':'audio','codec_name':'aac'}]
        for duration in ('90.001','NaN','0','inf'):
            with self.assertRaises(ValueError):
                validate_probe({'format':{'duration':duration},'streams':streams})
        with self.assertRaises(ValueError):
            validate_probe({'format':{'duration':'20'},'streams':streams[:1]})
        with self.assertRaises(ValueError):
            validate_probe({'format':{'duration':'20'},'streams':[dict(codec_type='video',codec_name='vp9'),streams[1]]})
        self.assertEqual(validate_probe({'format':{'duration':'30'},'streams':streams})['duration_s'],30.0)
        for level in ('-inf','-80'):
            with self.assertRaises(ValueError): validate_volume('mean_volume: '+level+' dB')
        self.assertEqual(validate_volume('mean_volume: -20.5 dB'),-20.5)

    def test_standalone_paid_stage_requires_host_context(self):
        self.assertEqual(run_cli(args('draft','--operator','hive','--job','j','--consent-generation','x')),3)


class OperatorFlowTests(unittest.TestCase):
    def setUp(self):
        import test_interview as fixtures
        self.f = fixtures
        self.helper = fixtures.Interviews()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.root = self.helper.root
        self.owner_patch = patch('operator_cli.HostAdapter.demo_owner',return_value=fixtures.OWNER)
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        env = patch.dict(os.environ,{'HERMES_HOME':str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.answers = self.root/'answers.json'
        self.settings = dict(aspect='16:9',duration_s=30,voice=True,tone='builder log',audience='builders')
        self.answers.write_text(json.dumps(self.settings))
        self.ctx = self.helper.ctx
        self.ctx.dispatch_tool = lambda *a,**k: self.fail('no tool dispatch expected')

    def run_command(self,*values):
        out = io.StringIO()
        with redirect_stdout(out):
            code = run_cli(args(*values,'--operator','hive'),ctx=self.ctx)
        return code,json.loads(out.getvalue())

    def test_offline_interview_and_command_looking_settings_are_data(self):
        import interview,store
        self.settings['tone'] = 'consent existing-job '+('a'*32)
        self.settings['audience'] = 'render existing-job'
        self.answers.write_text(json.dumps(self.settings))
        with patch.object(interview,'_handle',side_effect=AssertionError('must not parse settings as commands')):
            code,result = self.run_command('interview','--answers',str(self.answers),
                '--db',str(self.helper.source),'--session-id','synthetic-a','--consent-source')
        self.assertEqual(code,0)
        job = store.load_job(self.f.OWNER,result['job_id'])
        self.assertEqual(job.state,'AWAITING_GENERATION_CONSENT')
        self.assertEqual(job.draft.tone,self.settings['tone'])
        self.assertEqual(self.ctx.llm.calls,[])
        self.assertEqual(result['provider_calls'],0)
        self.assertTrue((Path(result['review_dir'])/'redaction-preview.json').is_file())

    def test_wrong_generation_hash_never_calls_llm(self):
        job = self.helper.ready()
        code,_ = self.run_command('draft','--job',job.job_id,'--consent-generation','wrong')
        self.assertEqual(code,3)
        self.assertEqual(self.ctx.llm.calls,[])

    def test_wrong_board_hash_and_expired_board_never_render(self):
        import pipeline,store,storyboard
        job,_ = self.helper.boarded()
        disclosure = store.digest(storyboard.board_disclosure(job.board,('image_generate','terminal'),()))
        with patch.object(pipeline,'render_job') as render:
            code,_ = self.run_command('render','--job',job.job_id,'--approve-board','wrong','--approve-disclosure',disclosure)
            self.assertEqual(code,3)
            render.assert_not_called()
            expired = store.save_job(replace(job,expires_at=(self.f.NOW-timedelta(seconds=1)).isoformat()),expected_job_version=job.job_version)
            code,_ = self.run_command('render','--job',expired.job_id,'--approve-board',job.board.digest,'--approve-disclosure',disclosure)
            self.assertEqual(code,3)
            render.assert_not_called()

    def test_cross_owner_job_never_dispatches(self):
        job = self.helper.ready()
        with patch('operator_cli.HostAdapter.demo_owner',return_value=self.f.OTHER):
            code,_ = self.run_command('draft','--job',job.job_id,'--consent-generation','wrong')
        self.assertEqual(code,3)
        self.assertEqual(self.ctx.llm.calls,[])


if __name__ == '__main__': unittest.main()
