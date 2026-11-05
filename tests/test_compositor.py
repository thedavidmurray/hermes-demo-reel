import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from schemas import (Approval, ApprovalRequired, ArtPlan, Asset, CapabilityUnavailable,
                     DemoOptions, Event, MediaPolicy, Owner, RenderFailed, Scene,
                     SessionSnapshot, SourceRef, Storyboard)
from compositor import freeze_render, build_ffmpeg_argv, preflight_terminal, compose, probe_video


def fixture(root, *, duration=15, voice=False):
    owner = Owner('test', 'session', 'discord', 'channel', 'user')
    source = SourceRef(root / 'source.db', ('session',))
    event = Event('event1', 'session', 1, 0, 'tool', 'terminal', 'ok', 'terminal', None)
    evidence = SessionSnapshot(1, 'snap', source, '2026-11-04', 'a'*64, (event,), 'Safe summary', ())
    from compositor import _digest
    board = Storyboard(1, 'job1', 1, 'snap', 'a'*64,
                       DemoOptions(source, 'makers', duration, '16:9', 'clear', voice),
                       (Scene('s1', 0, duration*1000, 'One', 'Caption', 'Narration', ('event1',), 'art'),),
                       'Demo @NousResearch', 'b'*64)
    from dataclasses import replace
    board = replace(board, digest=_digest(replace(board, digest='')))
    approval = Approval(owner, board.digest, 1, 'c'*64, '2026-11-04', ('terminal', 'image_generate', 'text_to_speech'), ())
    plan = ArtPlan(42, evidence.source_sha256, ('event1',), 320, 180, 24, duration*1000)
    return evidence, board, approval, plan


def asset(path, kind, *, scene_id=None, duration=None, width=None, height=None):
    return Asset(path.stem, kind, path, hashlib.sha256(path.read_bytes()).hexdigest(),
                 'image/png' if kind in ('art', 'title', 'frame') else 'audio/mpeg',
                 path.stat().st_size, duration, width, height, scene_id, (), 'dispatch' if kind != 'art' else None)


class Host:
    terminal_backend = 'local'
    def __init__(self): self.calls = []
    def dispatch_tool(self, name, args):
        self.calls.append((name, args))
        return json.dumps({'success': True, 'exit_code': 0, 'output': 'ok'})


class CompositorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.host = Host()
        self.policy = MediaPolicy(max_calls=24)

    def test_unknown_backend_and_nonce_mismatch_fail_closed(self):
        self.host.terminal_backend = 'ssh'
        with self.assertRaises(CapabilityUnavailable):
            preflight_terminal(self.host, self.root, policy=self.policy)
        self.assertEqual(self.host.calls, [])
        self.host.terminal_backend = 'local'
        with self.assertRaises(CapabilityUnavailable):
            preflight_terminal(self.host, self.root, policy=self.policy)
        self.assertEqual(len(self.host.calls), 1)

    def test_freeze_rejects_edited_board_even_if_digest_field_stale(self):
        evidence, board, approval, plan = fixture(self.root)
        from dataclasses import replace
        edited = replace(board, scenes=(replace(board.scenes[0], narration='unapproved edit'),))
        with self.assertRaisesRegex(RenderFailed, 'approval|evidence'):
            freeze_render(evidence, edited, approval, (), plan)

    def test_freeze_rejects_missing_art_and_changed_hash(self):
        evidence, board, approval, plan = fixture(self.root)
        with self.assertRaises(RenderFailed):
            freeze_render(evidence, board, approval, (), plan)
        art_path = self.root / 'frame_000000.png'
        Image.new('RGB', (320, 180), 'red').save(art_path)
        frames = (asset(art_path, 'art', width=320, height=180),)
        with self.assertRaises(RenderFailed):
            freeze_render(evidence, board, approval, frames, plan)

    def test_argv_contains_h264_aac_and_captions_not_shell_text(self):
        evidence, board, approval, plan = fixture(self.root)
        art_path = self.root / 'frame_000000.png'
        Image.new('RGB', (320, 180), 'red').save(art_path)
        frame = asset(art_path, 'art', width=320, height=180)
        title = asset(art_path, 'title', width=320, height=180)
        argv = build_ffmpeg_argv(board, (frame, title), (art_path,), self.root / 'video.mp4')
        self.assertEqual(argv[0], 'ffmpeg')
        self.assertIn('libx264', argv)
        self.assertNotIn('Caption', ' '.join(argv))

    def test_voice_on_silent_audio_rejected_after_decode(self):
        evidence, board, approval, plan = fixture(self.root, voice=True)
        path = self.root / 'demo.mp4'
        path.write_bytes(b'fixture')
        frame = self.root / 'grab.png'
        Image.new('RGB', (320, 180)).save(frame)
        from schemas import RenderBundle
        bundle = RenderBundle('job1', board.digest, 'id',
                              asset(path, 'video', duration=15000, width=320, height=180),
                              (), board.x_caption, self.root / 'render-manifest.json')
        bundle.manifest_path.write_text(json.dumps({'voice': True,
                                                     'video_sha256': bundle.mp4.sha256}))
        host = Host()
        def dispatch(name, args):
            host.calls.append((name, args))
            command = args['command']
            if 'terminal-nonce-' in command:
                # This fake never proves a shared FS. Set trusted preflight cache
                # explicitly; the voice probe itself still exercises dispatch.
                raise AssertionError('unexpected preflight')
            if 'ffprobe' in command:
                return json.dumps({'exit_code': 0, 'error': None, 'output': json.dumps({
                    'format': {'duration': '15.0'}, 'streams': [
                        {'codec_name': 'h264'}, {'codec_name': 'aac'}]})})
            return json.dumps({'exit_code': 0, 'error': None, 'output': 'mean_volume: -91.0 dB'})
        host.dispatch_tool = dispatch
        self.policy.__dict__['_demo_preflight_roots'] = {self.root.resolve()}
        with self.assertRaises(RenderFailed):
            probe_video(host, bundle, policy=self.policy)
        self.assertEqual(len(host.calls), 3)

    def test_preflight_then_real_terminal_render_and_probe(self):
        """Host-dispatched ffmpeg fixture with audible synthetic tone, NOT release narration."""
        import subprocess
        evidence, board, approval, plan = fixture(self.root, voice=True)
        art_dir = self.root / 'art'
        art_dir.mkdir()
        frames = []
        for idx in range(plan.fps * plan.duration_ms // 1000):
            path = art_dir / f'frame_{idx:06d}.png'
            Image.new('RGB', (320, 180), ('red' if idx % 2 else 'blue')).save(path)
            frames.append(asset(path, 'art', width=320, height=180))
        title_path = self.root / 'title.png'
        Image.new('RGB', (320, 180), 'green').save(title_path)
        speech_path = self.root / 'speech.mp3'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:duration=3', '-q:a', '3', str(speech_path)],
                       capture_output=True, check=True)
        speech = asset(speech_path, 'speech', scene_id='s1', duration=3000)
        title = asset(title_path, 'title', width=320, height=180)
        from dataclasses import replace
        with self.assertRaises(RenderFailed):
            freeze_render(evidence, board, approval,
                          tuple([title, replace(speech, dispatch_id=None), *frames]), plan)
        frozen = freeze_render(evidence, board, approval, tuple([title, speech, *frames]), plan)
        class LocalHost(Host):
            def dispatch_tool(inner, name, args):
                inner.calls.append((name, args))
                result = subprocess.run(args['command'], shell=True, text=True, capture_output=True, timeout=60)
                return json.dumps({'success': result.returncode == 0, 'exit_code': result.returncode,
                                   'output': (result.stdout + result.stderr)[-5000:],
                                   'error': result.stderr[-2000:] if result.returncode else None})
        host = LocalHost()
        with patch('media_tools._host_definitions', return_value={
            'terminal': {'command', 'timeout'}, 'image_generate': {'prompt', 'aspect_ratio'},
            'text_to_speech': {'text', 'output_path'}}):
            bundle = compose(host, frozen, self.root, policy=self.policy)
        from delivery import format_delivery
        self.assertIn('MEDIA:', format_delivery(bundle))
        self.assertTrue(bundle.mp4.path.exists())
        metadata = probe_video(host, bundle, policy=self.policy)
        self.assertGreater(float(metadata['format']['duration']), 0)
        self.assertAlmostEqual(float(metadata['format']['duration']), board.options.duration_s, delta=.2)
        self.assertLessEqual(float(metadata['format']['duration']), 90)
        self.assertEqual(len(bundle.frames), 3)
        self.assertTrue(all(a.path.exists() for a in bundle.frames))
        self.assertTrue(any('ffmpeg' in args['command'] for _, args in host.calls))
