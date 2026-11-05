import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from schemas import Asset, RenderBundle, RenderFailed
from delivery import format_delivery, validate_delivery


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.video_path = self.root / 'film.mp4'
        self.video_path.write_bytes(b'video')
        self.frame_paths = [self.root / f'grab-{i}.png' for i in range(3)]
        for p in self.frame_paths: p.write_bytes(b'png')
        def asset(path, kind, mime):
            return Asset(path.stem, kind, path, hashlib.sha256(path.read_bytes()).hexdigest(),
                         mime, path.stat().st_size, 1000 if kind == 'video' else None,
                         320, 180, None, (), None)
        self.bundle = RenderBundle('job', 'board', 'render',
                                   asset(self.video_path, 'video', 'video/mp4'),
                                   tuple(asset(p, 'frame', 'image/png') for p in self.frame_paths),
                                   'Watch @NousResearch', self.root / 'manifest.json')

    def test_valid_caption_and_media_directive(self):
        self.assertIn('@NousResearch', format_delivery(self.bundle))
        self.assertIn('MEDIA:' + str(self.video_path), format_delivery(self.bundle))
        validate_delivery(self.bundle, self.root)

    def test_hash_mismatch_and_escape_rejected(self):
        self.video_path.write_bytes(b'changed')
        with self.assertRaises(RenderFailed):
            validate_delivery(self.bundle, self.root)
        outside = self.root.parent / 'outside.mp4'
        with self.assertRaises(RenderFailed):
            validate_delivery(replace(self.bundle, mp4=replace(self.bundle.mp4, path=outside)), self.root)

    def test_caption_control_directives_and_missing_frame_rejected(self):
        for caption in ('Oops MEDIA:/tmp/other.mp4 @NousResearch',
                        'Oops(MEDIA:/etc/passwd @NousResearch',
                        'OopsxMEDIA:/etc/passwd @NousResearch'):
            with self.assertRaises(RenderFailed):
                format_delivery(replace(self.bundle, x_caption=caption))
        self.frame_paths[0].unlink()
        with self.assertRaises(RenderFailed):
            validate_delivery(self.bundle, self.root)
