"""Deterministic, session-evidence-driven Pillow motion art (no transcript text).

Time compression: retain input event order; adjacent positive timestamp gaps are
mapped to ``1 + sqrt(gap_ms / 1000)`` units (nonpositive gaps get one unit).
Normalize cumulative units to 8%..92% of the requested film. Thus idle gaps
are compressed nonlinearly while simultaneous calls keep separate births. The
frame clock is ``frame_index * 1000 / fps``; no wall clock or global RNG enters
rendering. Particle size/density uses actual invocation counts, error rings only
explicit error statuses, and unknown durations use a fixed neutral trail.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw

from schemas import ArtPlan, Event, SessionSnapshot, Storyboard

# ArtPlan has no timeline field: derive compressed event births from the frozen
# ordered evidence at frame-render time; never persist inferred durations.
_PALETTE = {
    "search": (79, 223, 203),
    "browser": (107, 185, 248),
    "terminal": (242, 168, 92),
    "image": (210, 134, 247),
    "speech": (245, 143, 186),
    "other": (168, 181, 208),
}


def _category(tool: str) -> str:
    name = tool.casefold()
    if any(word in name for word in ("search", "web_", "query")):
        return "search"
    if any(word in name for word in ("browser", "screenshot", "playwright")):
        return "browser"
    if any(word in name for word in ("terminal", "shell", "exec", "code")):
        return "terminal"
    if any(word in name for word in ("image", "vision", "draw")):
        return "image"
    if any(word in name for word in ("speech", "audio", "tts", "voice")):
        return "speech"
    return "other"


def _births(events: tuple[Event, ...], duration_ms: int) -> tuple[float, ...]:
    if not events:
        return ()
    units = [0.0]
    for previous, event in zip(events, events[1:]):
        gap_ms = max(0, event.t_ms - previous.t_ms)
        units.append(units[-1] + 1.0 + math.sqrt(gap_ms / 1000.0))
    # Even a single event has a visible entrance at 8% of the film.
    total = units[-1] if len(units) > 1 else 1.0
    return tuple(duration_ms * (0.08 + 0.84 * u / total) for u in units)


def _token(seed: int, event: Event, index: int) -> bytes:
    # Include ordinal and status, not labels/payloads, so reordering is visible
    # even when two tools share a category. No Python randomized hash().
    payload = f"{seed}:{index}:{event.event_id}:{event.tool}:{event.status}".encode("utf-8")
    return hashlib.sha256(payload).digest()


def plan_art(snapshot: SessionSnapshot, board: Storyboard, *, width: int, height: int, fps: int = 24) -> ArtPlan:
    """Bind art to the approved evidence fingerprint and exact film geometry."""
    expected = {"9:16": (720, 1280), "16:9": (1280, 720)}.get(board.options.aspect)
    if expected is None or (width, height) != expected or type(width) is not int or type(height) is not int or type(fps) is not int or fps != 24:
        raise ValueError("art geometry must match board aspect at 24 fps")
    if snapshot.schema_version != 1 or board.schema_version != 1:
        raise ValueError("unsupported evidence/board version")
    if board.evidence_snapshot_id != snapshot.snapshot_id or board.source_sha256 != snapshot.source_sha256:
        raise ValueError("storyboard evidence mismatch")
    if not isinstance(snapshot.source_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", snapshot.source_sha256) is None:
        raise ValueError("invalid source fingerprint")
    source = bytes.fromhex(snapshot.source_sha256)
    duration_s = board.options.duration_s
    if type(duration_s) is not int or not 15 <= duration_s <= 90:
        raise ValueError("art duration must be 15..90 seconds")
    event_ids = tuple(event.event_id for event in snapshot.events)
    if len(event_ids) > 2000 or len(event_ids) != len(set(event_ids)):
        raise ValueError("too many events or duplicate event ids")
    seed = int.from_bytes(hashlib.sha256(source + b":demo-reel-art-v1").digest()[:8], "big")
    return ArtPlan(seed, snapshot.source_sha256, event_ids, width, height, fps, duration_s * 1000)


def _validate_frame(plan: ArtPlan, events: tuple[Event, ...], frame_index: int) -> int:
    if type(plan.width) is not int or type(plan.height) is not int or (plan.width, plan.height) not in ((720, 1280), (1280, 720)) or type(plan.fps) is not int or plan.fps != 24:
        raise ValueError("invalid art geometry")
    if type(plan.duration_ms) is not int or plan.duration_ms < 15000 or plan.duration_ms > 90000:
        raise ValueError("invalid art duration")
    frame_count = plan.duration_ms * plan.fps // 1000
    if type(frame_index) is not int or not 0 <= frame_index < frame_count:
        raise ValueError("frame index out of bounds")
    if tuple(e.event_id for e in events) != plan.event_ids:
        raise ValueError("event order/identity mismatch")
    return frame_count


def render_art_frame(plan: ArtPlan, events: tuple[Event, ...], frame_index: int) -> Image.Image:
    """Render a new RGBA frame from immutable inputs, without I/O or shared state."""
    frame_count = _validate_frame(plan, events, frame_index)
    w, h = plan.width, plan.height
    small = min(w, h)
    now_ms = frame_index * 1000.0 / plan.fps
    births = _births(events, plan.duration_ms)
    image = Image.new("RGBA", (w, h), (9, 15, 30, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    horizontal = w > h
    # Fixed scene geometry: timeline is bottom quarter, activity is centered.
    center_x, center_y = w // 2, int(h * (0.42 if not horizontal else 0.43))
    for radius in (int(small * .11), int(small * .21), int(small * .30)):
        draw.ellipse((center_x-radius, center_y-radius, center_x+radius, center_y+radius),
                     outline=(39, 60, 84, 180), width=2)
    left, right = int(w * .09), int(w * .91)
    rail_y = int(h * .80)
    draw.line((left, rail_y, right, rail_y), fill=(62, 88, 112, 255), width=3)
    progress = left + int((right-left) * frame_index / max(1, frame_count-1))
    draw.line((left, rail_y, progress, rail_y), fill=(105, 221, 207, 255), width=4)

    # Exact ordered-event strip: thin bars preserve order/status throughout the
    # clip, including frames preceding the first birth. Never print raw labels.
    strip_left, strip_width = int(w * .12), int(w * .76)
    for i, event in enumerate(events):
        x = strip_left + int(strip_width * (i + .5) / len(events))
        color = ((246, 93, 104) if event.status == "error" else
                 (112, 120, 139) if event.status == "unknown" else _PALETTE[_category(event.tool)])
        draw.line((x, int(h*.89), x, int(h*(.91 if event.status == "error" else .905))),
                  fill=(*color, 255), width=max(1, min(5, strip_width // max(1, len(events)))))

    # Bounded evidence count (<=2000) and 8-second particle lifetimes limit
    # drawing on long films; compressed births and timeline markers persist.
    density: dict[str, int] = {}
    for i, (event, born) in enumerate(zip(events, births)):
        if born > now_ms:
            continue
        category = _category(event.tool)
        density[category] = density.get(category, 0) + 1
        color = _PALETTE[category]
        mark_x = left + int((right-left) * born / plan.duration_ms)
        draw.line((mark_x, rail_y-9, mark_x, rail_y+9), fill=(*color, 255), width=2)
        age = now_ms - born
        if age > 8000:
            continue
        token = _token(plan.seed, event, i)
        orbit = small * (.11 + token[0] / 255 * .18)
        angle = (token[1] / 255 * math.tau + age / 1300 * (1 if i % 2 else -1))
        x = center_x + orbit * math.cos(angle)
        y = center_y + orbit * math.sin(angle)
        size = 3 + min(11, density[category] // 2) + token[2] % 4
        color = _PALETTE[category]
        # Unknown durations are neutral: a fixed 10px tail, not an estimate.
        trail = 10 if event.duration_ms is None else 10 + min(65, max(0, event.duration_ms) // 45)
        end_x = x - math.cos(angle + .55) * trail
        end_y = y - math.sin(angle + .55) * trail
        draw.line((int(end_x), int(end_y), int(x), int(y)), fill=(*color, 145), width=max(2, size//2))
        draw.ellipse((int(x-size), int(y-size), int(x+size), int(y+size)), fill=(*color, 255))
        # Only an explicit error pulses; unknown does not imply success/failure.
        if event.status == "error":
            pulse = int((age % 800) / 800 * 24)
            radius = size + 5 + pulse
            draw.ellipse((int(x-radius), int(y-radius), int(x+radius), int(y+radius)),
                         outline=(249, 83, 105, 255), width=3)
    cursor = max(left, min(right, progress))
    draw.ellipse((cursor-7, rail_y-7, cursor+7, rail_y+7), fill=(224, 253, 238, 255))
    return image


def render_art_sequence(plan: ArtPlan, events: tuple[Event, ...], output_dir: Path) -> tuple[Path, ...]:
    """Write at most 2160 PNGs, refusing overwrite of any existing PNG."""
    _validate_frame(plan, events, 0)
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.glob("frame_*.png")):
        raise FileExistsError("art frame sequence already exists")
    output_dir.mkdir(parents=True, exist_ok=True)
    count = plan.duration_ms * plan.fps // 1000
    files = []
    for index in range(count):
        path = output_dir / f"frame_{index:06d}.png"
        if path.exists():
            raise FileExistsError(str(path))
        image = render_art_frame(plan, events, index)
        try:
            with path.open("xb") as stream:
                try:
                    image.save(stream, format="PNG", optimize=False)
                except Exception:
                    path.unlink()
                    raise
        finally:
            image.close()
        files.append(path)
    return tuple(files)
