import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from schemas import (Approval, ApprovalRequired, CapabilityUnavailable, DemoOptions, MediaPolicy,
                     Owner, Scene, SourceRef, Storyboard, RenderFailed)
from media_tools import (call_tool, resolve_media_tools, make_title, make_speech,
                         capture_screenshots, make_music, bind_media_approval)


class Host:
    def __init__(self):
        self.calls = []
        self.response = {}

    def dispatch_tool(self, name, args):
        self.calls.append((name, args))
        return json.dumps(self.response.get(name, {'success': True, 'output': 'ok'}))


def board(root, *, voice=True, screenshots=False, music=False, duration=4):
    scene = Scene('s1', 0, duration * 1000, 'Safe title', 'Safe caption',
                  'A brief true narration.', ('event1',), 'art')
    created = Storyboard(1, 'job1', 1, 'snap', 'a'*64,
                         DemoOptions(SourceRef(root / 'source.db', ('one',)), 'makers', duration,
                                     '16:9', 'clear', voice, screenshots, music),
                         (scene,), 'A demo @NousResearch', 'b'*64)
    from compositor import _digest
    return replace(created, digest=_digest(replace(created, digest='')))


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = Host()
        self.policy = MediaPolicy(max_calls=24, network_allowed=True)
        registry = patch('media_tools._host_definitions', return_value={
            'terminal': {'command', 'timeout'}, 'image_generate': {'prompt', 'aspect_ratio'},
            'text_to_speech': {'text', 'output_path'}})
        registry.start()
        self.addCleanup(registry.stop)
        self.image = self.root / 'input.png'
        Image.new('RGB', (24, 16), 'blue').save(self.image)

    def approve(self, b, tools=('terminal', 'image_generate', 'text_to_speech')):
        owner = Owner('test', 'session', 'discord', 'channel', 'user')
        approval = Approval(owner, b.digest, b.board_revision, 'd'*64, '2026-11-04', tools, ())
        bind_media_approval(self.policy, b, approval)

    def test_paid_tool_requires_bound_exact_approval(self):
        b = board(self.root)
        self.host.response['image_generate'] = {'success': True, 'image': str(self.image)}
        with self.assertRaises(ApprovalRequired):
            make_title(self.host, b, self.root, policy=self.policy)
        self.assertEqual(self.host.calls, [])
        owner = Owner('test', 'session', 'discord', 'channel', 'user')
        denied = Approval(owner, b.digest, b.board_revision, 'd'*64, '2026-11-04',
                          ('terminal',), ())
        bind_media_approval(self.policy, b, denied)
        with self.assertRaises(ApprovalRequired):
            make_title(self.host, b, self.root, policy=self.policy)
        self.assertEqual(self.host.calls, [])
        with self.assertRaises(ApprovalRequired):
            bind_media_approval(MediaPolicy(network_allowed=True),
                                replace(b, scenes=(replace(b.scenes[0], narration='changed'),)),
                                denied)

    def test_host_registry_required_and_schema_checked(self):
        with patch('media_tools._host_definitions', return_value={}):
            with self.assertRaises(CapabilityUnavailable):
                resolve_media_tools(self.host)
        with patch('media_tools._host_definitions', return_value={'terminal': {'command', 'timeout'}, 'image_generate': {'prompt', 'aspect_ratio'}, 'text_to_speech': {'text', 'output_path'}}):
            self.assertEqual(resolve_media_tools(self.host)['image_generate'], 'image_generate')

    def test_shared_budget_denial_and_invalid_envelope(self):
        policy = MediaPolicy(max_calls=1)
        call_tool(self.host, 'terminal', {'command': 'true'}, policy=policy)
        with self.assertRaises(CapabilityUnavailable):
            call_tool(self.host, 'image_generate', {'prompt': 'x'}, policy=policy)
        self.assertEqual(len(self.host.calls), 1)
        self.host.response['terminal'] = {'success': False, 'error': 'permission denied'}
        with self.assertRaises(ApprovalRequired):
            call_tool(self.host, 'terminal', {'command': 'true'}, policy=MediaPolicy())
        self.host.response['terminal'] = {'success': True, 'exit_code': 1}
        with self.assertRaises(RenderFailed):
            call_tool(self.host, 'terminal', {'command': 'false'}, policy=MediaPolicy())
        self.host.dispatch_tool = lambda *_: 'garbage'
        with self.assertRaises(RenderFailed):
            call_tool(self.host, 'terminal', {}, policy=MediaPolicy())

    def test_title_requires_local_valid_raster_and_approval(self):
        with self.assertRaises(ApprovalRequired):
            make_title(self.host, board(self.root), self.root, policy=MediaPolicy(network_allowed=False))
        self.assertEqual(self.host.calls, [])
        self.host.response['image_generate'] = {'success': True, 'image': str(self.image)}
        b = board(self.root)
        self.approve(b)
        asset = make_title(self.host, b, self.root, policy=self.policy)
        self.assertEqual(asset.kind, 'title')
        self.assertTrue(asset.sha256)
        self.assertEqual(asset.width, 24)
        self.host.response['image_generate'] = {'success': True, 'image': 'https://example.com/image.png'}
        asset.path.unlink()
        with self.assertRaises(CapabilityUnavailable):
            make_title(self.host, b, self.root, policy=self.policy)

    def test_host_generated_https_artifact_uses_pinned_host_terminal_fetch(self):
        b = board(self.root)
        self.approve(b)
        self.host.terminal_backend = 'local'
        self.policy.__dict__['_demo_preflight_roots'] = {self.root.resolve()}
        self.host.response['image_generate'] = {'success': True, 'image': 'https://cdn.example.org/title.png'}
        raw_dispatch = self.host.dispatch_tool
        def dispatch(name, args):
            if name == 'terminal':
                self.host.calls.append((name, args))
                Image.new('RGB', (24, 16), 'blue').save(self.root / 'title-download.png')
                return json.dumps({'exit_code': 0, 'error': None, 'output': ''})
            return raw_dispatch(name, args)
        self.host.dispatch_tool = dispatch
        with patch('media_tools.socket.getaddrinfo', return_value=[
            (2, 1, 6, '', ('93.184.215.14', 443))]):
            title = make_title(self.host, b, self.root, policy=self.policy)
        self.assertEqual(title.kind, 'title')
        command = self.host.calls[-1][1]['command']
        self.assertIn('--resolve', command)
        self.assertIn('--max-redirs 0', command)

    def test_tts_voice_off_and_overlong_rejected(self):
        self.assertEqual(make_speech(self.host, board(self.root, voice=False), self.root, policy=MediaPolicy()), ())
        self.assertEqual(self.host.calls, [])
        audio = self.root / 'scene.mp3'
        audio.write_bytes(b'not audio')
        self.host.response['text_to_speech'] = {'success': True, 'file_path': str(audio)}
        b = board(self.root)
        self.approve(b)
        with patch('media_tools._audio_duration_ms', return_value=5000):
            with self.assertRaises(RenderFailed):
                make_speech(self.host, b, self.root, policy=self.policy)

    def test_scene_id_and_title_symlink_cannot_escape_job(self):
        b = board(self.root)
        bad = replace(b, scenes=(replace(b.scenes[0], scene_id='../outside'),))
        with self.assertRaises(RenderFailed):
            make_speech(self.host, bad, self.root, policy=self.policy)
        self.assertEqual(self.host.calls, [])
        external = self.root.parent / 'other-owned-file.png'
        external.write_bytes(b'keep')
        (self.root / 'title.png').symlink_to(external)
        self.host.response['image_generate'] = {'success': True, 'image': str(self.image)}
        self.approve(b)
        with self.assertRaises(RenderFailed):
            make_title(self.host, b, self.root, policy=self.policy)
        self.assertEqual(external.read_bytes(), b'keep')

    def test_screenshots_reject_private_and_unproven_urls_before_tool(self):
        b = board(self.root, screenshots=True)
        with self.assertRaises(ApprovalRequired):
            capture_screenshots(self.host, b, self.root, policy=MediaPolicy(network_allowed=False))
        for url in ('http://127.0.0.1/', 'https://169.254.169.254/',
                    'https://user@example.com/', 'https://example.com/redirect'):
            with self.subTest(url=url), self.assertRaises((ApprovalRequired, CapabilityUnavailable)):
                capture_screenshots(self.host, b, self.root,
                                    policy=MediaPolicy(network_allowed=True, approved_urls=(url,)))
        self.assertEqual(self.host.calls, [])
        self.assertEqual(make_music(self.host, b, self.root, policy=MediaPolicy()), None)
