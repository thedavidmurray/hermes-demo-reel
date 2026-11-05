"""Contract and decoded-pixel regressions for deterministic session-driven art."""
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image

from schemas import DemoOptions, Event, Scene, SessionSnapshot, SourceRef, Storyboard


SOURCE = "a" * 64


def fixture():
    source = SourceRef(Path("/nonexistent/redacted.db"), ("fixture-session",))
    events = (
        Event("one", "fixture-session", 1, 0, "tool", "web_search", "ok", "Search", 500),
        Event("two", "fixture-session", 2, 210000, "tool", "terminal", "error", "Run", None),
        Event("three", "fixture-session", 3, 900000, "tool", "image_generate", "unknown", "Image", 1200),
    )
    snapshot = SessionSnapshot(1, "fixture", source, "2026-01-01", SOURCE, events, "Redacted fixture", ())
    options = DemoOptions(source, "developers", 15, "9:16", "focused", False)
    scene = Scene("s1", 0, 15000, "Demo", "Activity", "", tuple(e.event_id for e in events), "art")
    board = Storyboard(1, "job", 1, "fixture", SOURCE, options, (scene,), "@NousResearch", "digest")
    return snapshot, board


class ArtContractTests(unittest.TestCase):
    def setUp(self):
        from art import plan_art, render_art_frame, render_art_sequence
        self.plan_art = plan_art
        self.render_art_frame = render_art_frame
        self.render_art_sequence = render_art_sequence
        self.snapshot, self.board = fixture()
        self.plan = self.plan_art(self.snapshot, self.board, width=720, height=1280)

    def test_plan_uses_source_fingerprint_and_preserves_event_order(self):
        self.assertEqual(self.plan.event_ids, ("one", "two", "three"))
        self.assertEqual(self.plan.duration_ms, 15000)
        self.assertEqual(self.plan.fps, 24)
        self.assertEqual(self.plan, self.plan_art(self.snapshot, self.board, width=720, height=1280))
        changed = replace(self.snapshot, source_sha256="b" * 64)
        changed_board = replace(self.board, source_sha256="b" * 64)
        self.assertNotEqual(self.plan.seed, self.plan_art(changed, changed_board, width=720, height=1280).seed)

    def test_frame_is_pure_rgba_and_sensitive_to_order_and_status(self):
        from art import render_art_frame
        original = render_art_frame(self.plan, self.snapshot.events, 200)
        self.assertEqual(original.mode, "RGBA")
        self.assertEqual(original.size, (720, 1280))
        self.assertEqual(original.tobytes(), render_art_frame(self.plan, self.snapshot.events, 200).tobytes())
        swapped = (self.snapshot.events[1], self.snapshot.events[0], self.snapshot.events[2])
        with self.assertRaises(ValueError):
            render_art_frame(self.plan, swapped, 200)
        reordered_plan = replace(self.plan, event_ids=tuple(e.event_id for e in swapped))
        self.assertNotEqual(original.tobytes(), render_art_frame(reordered_plan, swapped, 200).tobytes())
        changed = (self.snapshot.events[0], replace(self.snapshot.events[1], status="ok"), self.snapshot.events[2])
        self.assertNotEqual(original.tobytes(), render_art_frame(self.plan, changed, 200).tobytes())
        unknown = (replace(self.snapshot.events[0], status="unknown"), *self.snapshot.events[1:])
        self.assertNotEqual(render_art_frame(self.plan, self.snapshot.events, 0).tobytes(),
                            render_art_frame(self.plan, unknown, 0).tobytes())

    def test_validation_fail_closed(self):
        with self.assertRaises(ValueError):
            self.plan_art(self.snapshot, self.board, width=100, height=100)
        with self.assertRaises(ValueError):
            self.plan_art(self.snapshot, self.board, width=1280, height=720)
        with self.assertRaises(ValueError):
            self.plan_art(replace(self.snapshot, source_sha256="a" * 63 + " "),
                          replace(self.board, source_sha256="a" * 63 + " "), width=720, height=1280)
        with self.assertRaises(ValueError):
            self.plan_art(self.snapshot, replace(self.board, source_sha256="b" * 64), width=720, height=1280)
        with self.assertRaises(ValueError):
            self.render_art_frame(replace(self.plan, fps=24.0), self.snapshot.events, 0)
        with self.assertRaises(ValueError):
            self.render_art_frame(replace(self.plan, width=720.0), self.snapshot.events, 0)
        with self.assertRaises(ValueError):
            self.render_art_frame(self.plan, self.snapshot.events, -1)
        with self.assertRaises(ValueError):
            self.render_art_frame(self.plan, self.snapshot.events, 360)
        with self.assertRaises(ValueError):
            self.render_art_frame(self.plan, self.snapshot.events, 0.5)

    def test_landscape_and_empty_events(self):
        empty = replace(self.snapshot, events=())
        landscape_board = replace(self.board, options=replace(self.board.options, aspect="16:9"))
        landscape = self.plan_art(empty, landscape_board, width=1280, height=720)
        image = self.render_art_frame(landscape, (), 0)
        self.assertEqual(image.size, (1280, 720))
        self.assertEqual(image.mode, "RGBA")

    def test_sequence_count_order_dimensions_and_repeatable_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            files = self.render_art_sequence(self.plan, self.snapshot.events, first)
            self.assertEqual(len(files), 360)
            self.assertEqual(files[0].name, "frame_000000.png")
            self.assertEqual(files[-1].name, "frame_000359.png")
            samples = (0, 180, 359)
            hashes = [hashlib.sha256(files[i].read_bytes()).hexdigest() for i in samples]
            for i in samples:
                with Image.open(files[i]) as image:
                    self.assertEqual(image.size, (720, 1280))
                    self.assertEqual(image.mode, "RGBA")
            again = self.render_art_sequence(self.plan, self.snapshot.events, second)
            self.assertEqual(hashes, [hashlib.sha256(again[i].read_bytes()).hexdigest() for i in samples])
            with self.assertRaises(FileExistsError):
                self.render_art_sequence(self.plan, self.snapshot.events, first)


if __name__ == "__main__":
    unittest.main()
