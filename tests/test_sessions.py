"""Synthetic fixtures only: never commit historical transcripts or session IDs."""
import dataclasses
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import session_reader as reader
from schemas import ContractError


def fixture(path):
    with sqlite3.connect(path) as c:
        c.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL, profile_name TEXT);
        CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
        tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL);''')
        c.executemany('INSERT INTO sessions VALUES(?,?,?)', [('synthetic-a', 100., 'fixture'), ('synthetic-b', 101., 'fixture')])
        calls = [{'id':'call-1', 'function':{'name':'terminal','arguments':json.dumps({'command':'print SECRET_TOKEN /Users/private'})}},
                 {'id':'call-2', 'function':{'name':'web_extract','arguments':json.dumps({'urls':['https://example.org/docs', 'http://127.0.0.1/private','https://example.org/?token=secret']})}}]
        c.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', [
            (1,'synthetic-a','user','SECRET user prompt',None,None,None,100.),
            (2,'synthetic-a','assistant','SECRET reasoning',None,json.dumps(calls),None,101.),
            (3,'synthetic-b','tool','{"exit_code":0}', 'call-1',None,'terminal',102.),
            (4,'synthetic-a','tool','{"exit_code":1,"output":"SECRET"}', 'call-1',None,'terminal',103.),
            (5,'synthetic-a','tool','unstructured SECRET', 'call-2',None,'web_extract',104.)])


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2],prefix='.demo-test-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'source.db'
        fixture(self.path)

    def test_read_redacts_and_joins_by_session_and_call(self):
        before = self.path.read_bytes()
        snap = reader.read_session(self.path, ('synthetic-a',))
        self.assertEqual(before, self.path.read_bytes())
        self.assertNotIn('SECRET', repr(snap))
        self.assertNotIn('/Users/private', repr(snap))
        tools = [e for e in snap.events if e.kind == 'tool']
        self.assertEqual([e.status for e in tools], ['error','unknown'])
        self.assertEqual([e.duration_ms for e in tools], [None,None])
        self.assertEqual(snap.public_urls, ('https://example.org/docs',))
        self.assertTrue(all(e.session_id == 'synthetic-a' for e in snap.events))
        self.assertEqual(snap.source_sha256, reader.read_session(self.path, ('synthetic-a',)).source_sha256)

    def test_selected_rows_fingerprint_changes(self):
        one = reader.read_session(self.path, ('synthetic-a',))
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE messages SET content=? WHERE id=3', ('unrelated',))
        self.assertEqual(one.source_sha256, reader.read_session(self.path, ('synthetic-a',)).source_sha256)
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE messages SET content=? WHERE id=4', ('{"exit_code":0}',))
        self.assertNotEqual(one.source_sha256, reader.read_session(self.path, ('synthetic-a',)).source_sha256)

    def test_explicit_selection_and_bounds(self):
        for ids in [(), ('missing',), ('synthetic-a',)*2, ('a','b','c','d')]:
            with self.subTest(ids=ids), self.assertRaises(ContractError):
                reader.read_session(self.path, ids)
        with self.assertRaises(ContractError):
            reader.read_session(self.path, ('synthetic-a',), max_events=1)
        with self.assertRaises(ContractError):
            reader.read_session(self.path.parent/'missing.db', ('a',))
        self.assertFalse((self.path.parent/'missing.db').exists())

    def test_metadata_only(self):
        rows = reader.list_sessions(self.path, limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {'id','started_at','profile_name'})
        self.assertNotIn('SECRET', repr(rows))
        with self.assertRaises(ContractError):
            reader.list_sessions(self.path, limit=0)

    def test_unknown_schema_and_malformed_calls_fail_closed(self):
        other = self.path.parent/'unknown.db'
        with sqlite3.connect(other) as c:
            c.execute('CREATE TABLE messages(secret TEXT)')
        with self.assertRaises(ContractError):
            reader.read_session(other, ('a',))
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE messages SET tool_calls='invalid' WHERE id=2")
        with self.assertRaises(ContractError):
            reader.read_session(self.path, ('synthetic-a',))

    def test_redact_event_does_not_trust_labels_names_or_duration(self):
        event = reader.redact_event(dict(session_id='synthetic-a', row_id=1, t_ms=0,
            kind='tool', tool='SECRET_UNKNOWN_TOOL', label='SECRET', duration_ms=123, status='invented'))
        self.assertNotIn('SECRET', repr(event))
        self.assertEqual(event.duration_ms, None)
        self.assertEqual(event.status, 'unknown')

    def test_safe_urls(self):
        for value in ['file:///etc/passwd','https://user:pass@example.org/a','http://localhost/a',
                      'http://10.0.0.1/a','https://foo.local/a','https://example.org/?secret=x',
                      'http://[::1]/a','https://example.org/#secret','https://metadata.google.internal/a']:
            self.assertIsNone(reader.safe_public_url(value), value)
        self.assertEqual(reader.safe_public_url('https://example.org/docs'), 'https://example.org/docs')


if __name__ == '__main__':
    unittest.main()
