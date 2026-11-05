"""Integration contract tests; fake boundaries are not live release evidence."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class IntegrationTests(unittest.TestCase):
    def test_namespaced_plugin_load_and_registration_no_io(self):
        spec = importlib.util.spec_from_file_location('test_demo_plugin', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        calls = []
        def register_tool(name, toolset, schema, handler, **kwargs):
            self.assertEqual(toolset, 'demo-reel')
            self.assertEqual(schema['name'], name)
            self.assertTrue(callable(handler))
            calls.append(('tool', (name, toolset, schema, handler), kwargs))
        ctx = SimpleNamespace(register_command=lambda *a, **k: calls.append(('command',a,k)),
                              register_tool=register_tool,
                              register_cli_command=lambda *a, **k: calls.append(('cli',a,k)))
        module.register(ctx)
        self.assertEqual([c[0] for c in calls], ['command','tool','cli'])
        handler = calls[0][1][1]
        self.assertIn('NEEDS_INPUT', handler('', session_id='', source=None, platform='cron'))

    def test_model_reply_cannot_approve_or_read_source(self):
        from host import HostAdapter
        from schemas import NeedsInput
        adapter = HostAdapter(SimpleNamespace())
        with self.assertRaises(NeedsInput):
            adapter.demo_owner(session_id='s', source=None, platform='discord')
        self.assertFalse(adapter.demo_confirm('source',None,None,'anything'))

    def test_cli_unattended_is_immediate(self):
        import argparse
        from cli import setup_cli, run_cli
        parser = argparse.ArgumentParser()
        setup_cli(parser)
        self.assertEqual(run_cli(parser.parse_args(['interview'])), 2)

    def test_render_claim_precedes_media_and_is_once(self):
        from pipeline import render_job
        from schemas import Owner, MediaPolicy, ApprovalRequired
        owner = Owner('test','session','discord','chat','user')
        job = SimpleNamespace(owner=owner,state='RENDERING',approval=None)
        with patch('pipeline.store.load_job',return_value=job), patch('pipeline.make_title') as title:
            with self.assertRaises(ApprovalRequired):
                render_job(SimpleNamespace(),owner,'job',policy=MediaPolicy())
            title.assert_not_called()

if __name__ == '__main__': unittest.main()
