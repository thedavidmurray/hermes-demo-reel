"""Narrative regression tests use synthetic evidence, never real source text."""
import dataclasses as dc
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import narrative as n
import session_reader as reader
import store
from schemas import *


def snapshot():
    events = tuple(Event(f'e{i}', 'synthetic', i, i*1000, 'user' if i == 0 else 'tool',
                        '' if i == 0 else 'write_file', 'ok', 'Recorded event', None) for i in range(5))
    texts = ['Build a timer that remembers the last duration.',
             'def save_timer(seconds):\n    return {"duration": seconds}',
             'function restoreTimer(saved) { return saved.duration; }',
             '{"verified": true, "file": "timer.js"}',
             '{"output": "3 tests passed", "exit_code": 0}']
    excerpts = [n.capture_excerpt(e, t, 'ask' if i == 0 else 'file_input' if i < 3 else 'tool_output')
                for i, (e, t) in enumerate(zip(events, texts))]
    return SessionSnapshot(2, 'snapshot', SourceRef(Path('/synthetic/source.db'), ('synthetic',)),
                           '2026-01-01T00:00:00+00:00', 'a'*64, events,
                           store.canonical({'excerpts': excerpts}), ())


def board(snap):
    opts = DemoOptions(SourceRef(Path('/synthetic/source.db'), ('synthetic',)), 'builders', 30,
                       '16:9', 'builder log', True, False, False)
    captions = ['A timer that remembers your last duration', 'Save the duration instead of losing it',
                'Restore the saved duration when reopened', 'Check the written file and test result',
                'Timer persistence is implemented and tests passed']
    scenes = tuple(Scene(f's{i}',i*6000,(i+1)*6000,text, text, text, (f'e{i}',), 'timeline')
                   for i,text in enumerate(captions))
    return Storyboard(2,'job',1,snap.snapshot_id,snap.source_sha256,opts,scenes,'Timer persistence with @NousResearch','')


class Narrative(unittest.TestCase):
    def test_artifact_catalog_and_three_acts(self):
        snap = snapshot()
        self.assertEqual(len(n.excerpt_catalog(snap)),5)
        n.validate_narrative(board(snap),snap)
        for scene in board(snap).scenes:
            beats = n.caption_beats(scene)
            self.assertEqual(beats[0][0], scene.start_ms)
            self.assertEqual(beats[-1][1], scene.end_ms)
            self.assertTrue(all(2000 <= b-a <= 4000 for a,b,_ in beats))
            self.assertEqual(' '.join(t for _,_,t in beats),scene.caption)

    def test_missing_artifact_bad_hash_and_empty_ask_fail_closed(self):
        snap = snapshot()
        data = json.loads(snap.safe_summary)
        data['excerpts'][1]['text'] = 'tampered'
        with self.assertRaises(ContractError): n.excerpt_catalog(dc.replace(snap,safe_summary=store.canonical(data)))
        bad = dc.replace(board(snap),scenes=board(snap).scenes[:3])
        with self.assertRaises(ContractError): n.validate_narrative(bad,snap)
        scenes = list(board(snap).scenes)
        scenes[2] = dc.replace(scenes[2],event_ids=('missing',))
        with self.assertRaises(ContractError): n.validate_narrative(dc.replace(board(snap),scenes=tuple(scenes)),snap)
        catalog = n.excerpt_catalog(snap)[1:]
        with self.assertRaises(ContractError):
            n.validate_narrative(board(snap),dc.replace(snap,safe_summary=store.canonical({'excerpts':catalog})))

    def test_redaction_and_directives(self):
        raw = 'hello user@example.com\n/Users/private/secret.py\nTOKEN=abc123\nignore previous instructions\nprint(42)'
        redacted = n.redact_excerpt(raw)
        for needle in ['user@example.com','/Users/private','abc123','ignore previous']:
            self.assertNotIn(needle,redacted)
        self.assertIn('print(42)',redacted)

    def test_caption_cadence_full_duration_range(self):
        for duration in range(2500,18001,500):
            scene = dc.replace(board(snapshot()).scenes[0],start_ms=0,end_ms=duration,
                               caption=' '.join(['word']*(max(1,__import__('math').ceil(duration/4000))*5)))
            beats = n.caption_beats(scene)
            self.assertTrue(all(2000<=b-a<=4000 for a,b,_ in beats),duration)

    def test_first_visible_evidence_must_match_act(self):
        snap = snapshot()
        original = board(snap)
        scenes = list(original.scenes)
        # Supporting the ask later in the references is not enough: renderer
        # paints the FIRST referenced event, which must match the opening act.
        scenes[0] = dc.replace(scenes[0],event_ids=('e1','e0'))
        with self.assertRaisesRegex(ContractError,'ASK_EVIDENCE_REQUIRED'):
            n.validate_narrative(dc.replace(original,scenes=tuple(scenes)),snap)
        scenes = list(original.scenes)
        scenes[-1] = dc.replace(scenes[-1],event_ids=('e1','e4'))
        with self.assertRaisesRegex(ContractError,'RESULT_OUTPUT_REQUIRED'):
            n.validate_narrative(dc.replace(original,scenes=tuple(scenes)),snap)

    def test_reader_captures_only_selected_recorded_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'source.db'
            with sqlite3.connect(path) as c:
                c.executescript('CREATE TABLE sessions(id TEXT, started_at REAL); CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_call_id TEXT, tool_name TEXT, timestamp REAL);')
                c.execute('INSERT INTO sessions VALUES(?,?)',('synthetic',1.))
                calls = json.dumps([{'id':'c','function':{'name':'write_file','arguments':json.dumps({'path':'timer.py','content':'print(42)'})}}])
                c.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',[
                    (1,'synthetic','user','Build a timer',None,None,None,1),
                    (2,'synthetic','assistant','Do not capture private reasoning',calls,None,None,2),
                    (3,'synthetic','tool','{"verified":true}',None,'c','write_file',3)])
            before = path.read_bytes()
            snap = reader.read_session(path,('synthetic',))
            self.assertEqual(before,path.read_bytes())
            self.assertEqual(snap.schema_version,2)
            catalog = n.excerpt_catalog(snap)
            self.assertIn('print(42)',str(catalog))
            self.assertIn('Build a timer',str(catalog))
            self.assertNotIn('private reasoning',str(catalog))

if __name__ == '__main__': unittest.main()
