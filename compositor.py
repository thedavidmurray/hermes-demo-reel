"""Immutable input checks and host-terminal-only ffmpeg composition."""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import shlex
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from .media_tools import _asset, _confined, call_tool, resolve_media_tools
except ImportError:
    from media_tools import _asset, _confined, call_tool, resolve_media_tools
try:
    from .schemas import (Approval, ArtPlan, Asset, CapabilityUnavailable, MediaPolicy,
                         RenderBundle, RenderFailed, RenderSnapshot, SessionSnapshot, Storyboard)
except ImportError:
    from schemas import (Approval, ArtPlan, Asset, CapabilityUnavailable, MediaPolicy,
                         RenderBundle, RenderFailed, RenderSnapshot, SessionSnapshot, Storyboard)


def _canonical(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.resolve())
    if hasattr(value, '__dataclass_fields__'):
        return {key: _canonical(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(_canonical(value), sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _terminal(ctx: Any, argv: list[str], *, policy: MediaPolicy, timeout: int | None = None) -> dict:
    result = call_tool(ctx, 'terminal', {'command': shlex.join(argv),
                                        'timeout': min(timeout or policy.timeout_s, 600)}, policy=policy)
    return result.data


def preflight_terminal(ctx: Any, job_dir: Path, *, policy: MediaPolicy) -> None:
    root = Path(job_dir).resolve(strict=True)
    if not root.is_dir():
        raise CapabilityUnavailable('job directory unavailable')
    # This assertion must be supplied by the trusted host adapter, NOT inferred from
    # an env var or successful nonce check: remote FS mounts can pass the nonce too.
    if getattr(ctx, 'terminal_backend', None) != 'local':
        raise CapabilityUnavailable('confirmed local terminal backend unavailable')
    if root in policy.__dict__.get('_demo_preflight_roots', set()):
        return
    nonce_file = root / f'.terminal-nonce-{secrets.token_hex(12)}'
    nonce = secrets.token_hex(32)
    try:
        with nonce_file.open('x') as stream:
            stream.write(nonce)
        # Fixed shell operators only. Paths shlex-quoted; no user text in command.
        command = (f'test -f {shlex.quote(str(nonce_file))} && '
                   f'cat {shlex.quote(str(nonce_file))} && '
                   'command -v ffmpeg && command -v ffprobe')
        result = call_tool(ctx, 'terminal', {'command': command, 'timeout': policy.timeout_s}, policy=policy)
        output = result.data.get('output', '')
        if not isinstance(output, str) or not output.startswith(nonce) or len(output.splitlines()) < 2:
            raise CapabilityUnavailable('terminal shared-filesystem nonce mismatch')
        policy.__dict__.setdefault('_demo_preflight_roots', set()).add(root)
    finally:
        nonce_file.unlink(missing_ok=True)


def _validated_assets(root: Path | None, assets: tuple[Asset, ...]) -> None:
    for asset in assets:
        path = _confined(asset.path, root) if root is not None else asset.path.resolve(strict=True)
        if path.stat().st_size != asset.size_bytes or hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
            raise RenderFailed('frozen asset checksum mismatch')
        if asset.kind in ('art', 'title', 'screenshot', 'frame'):
            if asset.mime_type != 'image/png' or not asset.width or not asset.height:
                raise RenderFailed('frozen image metadata incomplete')
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if image.format != 'PNG' or image.size != (asset.width, asset.height):
                    raise RenderFailed('frozen image dimensions differ')
        elif asset.kind in ('speech', 'music'):
            if asset.duration_ms is None or asset.duration_ms <= 0:
                raise RenderFailed('frozen audio duration missing')


def freeze_render(snapshot: SessionSnapshot, board: Storyboard, approval: Approval,
                  assets: tuple[Asset, ...], art_plan: ArtPlan) -> RenderSnapshot:
    if (board.schema_version != 1 or snapshot.schema_version != 1 or
        board.digest != approval.storyboard_digest or _digest(replace(board, digest='')) != board.digest or
        board.board_revision != approval.board_revision or
        board.evidence_snapshot_id != snapshot.snapshot_id or
        board.source_sha256 != snapshot.source_sha256 or
        art_plan.source_sha256 != snapshot.source_sha256 or
        art_plan.event_ids != tuple(event.event_id for event in snapshot.events) or
        snapshot.source != board.options.source or
        not approval.owner.profile or not approval.owner.user_id or
        not approval.disclosure_sha256):
        raise RenderFailed('evidence, approval, board or art plan mismatch')
    if not 1 <= len(board.scenes) <= 6 or not 15 <= board.options.duration_s <= 90:
        raise RenderFailed('invalid board duration/scenes')
    if (board.options.duration_s * 1000 != art_plan.duration_ms or
        (board.options.aspect == '16:9' and art_plan.width <= art_plan.height) or
        (board.options.aspect == '9:16' and art_plan.width >= art_plan.height) or
        (art_plan.width, art_plan.height) not in ((320, 180), (180, 320), (1280, 720), (720, 1280)) or
        art_plan.fps != 24):
        raise RenderFailed('art dimensions/duration unsupported')
    cursor = 0
    for scene in board.scenes:
        if scene.start_ms != cursor or scene.end_ms <= cursor:
            raise RenderFailed('noncontiguous approved storyboard')
        cursor = scene.end_ms
    if cursor != art_plan.duration_ms:
        raise RenderFailed('storyboard does not fill approved duration')
    if any(scene.event_ids and not set(scene.event_ids) <= set(art_plan.event_ids) for scene in board.scenes):
        raise RenderFailed('scene references non-frozen event')
    expected = art_plan.fps * art_plan.duration_ms // 1000
    art = sorted((asset for asset in assets if asset.kind == 'art'), key=lambda a: a.path.name)
    if (len(art) != expected or
        [a.path.name for a in art] != [f'frame_{i:06d}.png' for i in range(expected)] or
        len({a.path.parent.resolve() for a in art}) != 1 or
        any((a.width, a.height) != (art_plan.width, art_plan.height) for a in art)):
        raise RenderFailed('art PNG sequence not fully frozen')
    titles = [asset for asset in assets if asset.kind == 'title']
    if (len(titles) != 1 or 'image_generate' not in approval.allowed_external_tools or
        not titles[0].dispatch_id):
        raise RenderFailed('generated approved title asset required')
    if board.options.voice and 'text_to_speech' not in approval.allowed_external_tools:
        raise RenderFailed('speech was not approved')
    if board.options.screenshots and 'browser_navigate' not in approval.allowed_external_tools:
        raise RenderFailed('screenshots were not approved')
    if board.options.music and 'music' not in approval.allowed_external_tools:
        raise RenderFailed('music was not approved')
    speech = [asset for asset in assets if asset.kind == 'speech']
    if board.options.voice:
        if {asset.scene_id for asset in speech} != {scene.scene_id for scene in board.scenes} or len(speech) != len(board.scenes):
            raise RenderFailed('one TTS asset per approved scene required')
        for scene in board.scenes:
            item = next(a for a in speech if a.scene_id == scene.scene_id)
            if item.duration_ms is None or item.duration_ms > scene.end_ms - scene.start_ms or not item.dispatch_id:
                raise RenderFailed('TTS longer than scene or missing host dispatch witness')
    elif speech:
        raise RenderFailed('unexpected voice assets on voice-off board')
    # No job_dir parameter here: verify immutable bytes now; compose rechecks every
    # asset against its explicit job_dir, never infers a parent from art layout.
    _validated_assets(None, assets)
    frozen = RenderSnapshot(1, board.job_id + '-' + board.digest[:12], snapshot, board,
                            approval, assets, art_plan, art_plan.fps, art_plan.width, art_plan.height, '')
    return replace(frozen, digest=_digest(frozen))


def _filter_path(path: Path) -> str:
    # ffmpeg filtergraph escaping, not shell escaping. Paths are already confined.
    return str(path).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'").replace(',', '\\,').replace('[', '\\[').replace(']', '\\]')


def _timestamp(ms: int) -> str:
    h, remainder = divmod(ms, 3600_000)
    m, remainder = divmod(remainder, 60_000)
    s, milli = divmod(remainder, 1000)
    return f'{h:02}:{m:02}:{s:02},{milli:03}'


def _write_captions(board: Storyboard, path: Path) -> None:
    content = []
    for i, scene in enumerate(board.scenes, 1):
        text = ' '.join((scene.title + ' — ' + scene.caption).split())
        text = re.sub(r'<[^>]*>', '', text)[:240]
        if not text:
            raise RenderFailed('empty approved scene caption')
        content.append(f'{i}\n{_timestamp(scene.start_ms)} --> {_timestamp(scene.end_ms)}\n{text}\n')
    path.write_text('\n'.join(content), encoding='utf-8')


def build_ffmpeg_argv(board: Storyboard, assets: tuple[Asset, ...],
                      art_frames: tuple[Path, ...], output: Path) -> list[str]:
    if not art_frames or any(frame.parent != art_frames[0].parent or
                             frame.name != f'frame_{i:06d}.png' for i, frame in enumerate(art_frames)):
        raise RenderFailed('missing/noncontiguous frozen art frames')
    title = next((asset for asset in assets if asset.kind == 'title'), None)
    if title is None:
        raise RenderFailed('title not generated')
    duration = board.options.duration_s
    # Pillow rasterizes approved captions; the installed ffmpeg need not have
    # libass/subtitles or drawtext. No untrusted copy enters a shell/filter arg.
    first_frame = next((asset for asset in assets if asset.kind == 'art'), None)
    if not first_frame or not first_frame.width or not first_frame.height:
        raise RenderFailed('art dimensions missing')
    width, height = first_frame.width, first_frame.height
    captions = output.parent / 'captions.srt'
    _write_captions(board, captions)
    argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-framerate', '24', '-start_number', '0', '-i', str(art_frames[0].parent / 'frame_%06d.png'),
            '-loop', '1', '-i', str(title.path)]
    caption_files = []
    for i, scene in enumerate(board.scenes):
        image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=max(12, min(32, width // 30)))
        title_text = ' '.join(re.sub(r'<[^>]*>', '', scene.title).split())[:90]
        body_text = ' '.join(re.sub(r'<[^>]*>', '', scene.caption).split())[:180]
        text = f'{title_text}\n{body_text}'
        lines = []
        for paragraph in text.split('\n'):
            current = ''
            for word in paragraph.split():
                trial = (current + ' ' + word).strip()
                if current and draw.textlength(trial, font=font) > width * .85:
                    lines.append(current)
                    current = word
                else:
                    current = trial
            lines.append(current)
        line_height = max(16, int(height * .05))
        start_y = max(0, height - len(lines) * line_height - 12)
        draw.rectangle((0, start_y - 7, width, height), fill=(5, 10, 22, 220))
        for line in lines:
            draw.text((width * .07, start_y), line, font=font, fill=(255, 255, 255, 255))
            start_y += line_height
        caption_path = output.parent / f'caption-{i}.png'
        image.save(caption_path, format='PNG')
        image.close()
        caption_files.append(caption_path)
        argv += ['-loop', '1', '-i', str(caption_path)]
    first = board.scenes[0]
    first_duration = min(2.0, (first.end_ms - first.start_ms) / 5000)
    segments = [f'[1:v]scale={width}:{height}[title]',
                f"[0:v][title]overlay=0:0:enable='between(t,0,{first_duration:.3f})'[base]"]
    previous = 'base'
    for i, scene in enumerate(board.scenes):
        end = scene.end_ms / 1000 - .001
        start = scene.start_ms / 1000
        following = 'v' if i == len(board.scenes) - 1 else f'v{i}'
        segments.append(f"[{previous}][{i+2}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[{following}]")
        previous = following
    speech = [next((a for a in assets if a.kind == 'speech' and a.scene_id == scene.scene_id), None)
              for scene in board.scenes]
    if board.options.voice and any(a is None for a in speech):
        raise RenderFailed('missing narration audio')
    present = [a for a in speech if a is not None]
    for item in present:
        argv += ['-i', str(item.path)]
    if present:
        first_audio = len(caption_files) + 2
        segments += [f'[{i+first_audio}:a]adelay={scene.start_ms}:all=1[a{i}]'
                     for i, scene in enumerate(board.scenes)]
        segments.append(''.join(f'[a{i}]' for i in range(len(present))) +
                        f'amix=inputs={len(present)}:duration=longest:normalize=0[a]')
    filter_file = output.parent / 'compose.filter'
    filter_file.write_text(';'.join(segments), encoding='utf-8')
    argv += ['-filter_complex', ';'.join(segments), '-map', '[v]']
    if present:
        argv += ['-map', '[a]', '-c:a', 'aac', '-b:a', '128k']
    else:
        argv += ['-an']
    argv += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-r', '24', '-t', str(duration),
             '-movflags', '+faststart', str(output)]
    return argv


def compose(ctx: Any, snapshot: RenderSnapshot, job_dir: Path, *, policy: MediaPolicy) -> RenderBundle:
    root = Path(job_dir).resolve(strict=True)
    if _digest(replace(snapshot, digest='')) != snapshot.digest:
        raise RenderFailed('render snapshot digest mismatch')
    _validated_assets(root, snapshot.assets)
    resolve_media_tools(ctx)
    preflight_terminal(ctx, root, policy=policy)
    art_assets = sorted((asset for asset in snapshot.assets if asset.kind == 'art'), key=lambda a: a.path.name)
    if len(art_assets) != snapshot.art_plan.duration_ms * snapshot.fps // 1000:
        raise RenderFailed('frozen frame count changed')
    output = root / 'demo.mp4'
    if output.exists():
        raise RenderFailed('render output exists; explicit retry in new job required')
    argv = build_ffmpeg_argv(snapshot.board, snapshot.assets,
                             tuple(a.path for a in art_assets), output)
    _terminal(ctx, argv, policy=policy, timeout=min(600, policy.timeout_s * 5))
    meta = _terminal(ctx, ['ffprobe', '-v', 'error', '-show_entries',
                           'format=duration:stream=codec_name,codec_type,width,height',
                           '-of', 'json', str(output)], policy=policy)
    try:
        info = json.loads(meta['output'])
        seconds = float(info['format']['duration'])
        picture = next(s for s in info['streams'] if s.get('codec_name') == 'h264')
        dimensions = (picture['width'], picture['height'])
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise RenderFailed('encoded video not probeable') from exc
    if (not 0 < seconds <= 90 or
        abs(seconds - snapshot.board.options.duration_s) > .2 or
        dimensions != (snapshot.width, snapshot.height)):
        raise RenderFailed('encoded video duration/dimensions invalid')
    video = replace(_asset(output, root, 'video', 'video/mp4', duration_ms=round(seconds * 1000)),
                    width=dimensions[0], height=dimensions[1])
    grabs = []
    for idx, point in enumerate((.18, .50, .82)):
        path = root / f'grab-{idx}.png'
        _terminal(ctx, ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss',
                        f'{snapshot.board.options.duration_s * point:.3f}', '-i', str(output),
                        '-frames:v', '1', '-y', str(path)], policy=policy)
        grabs.append(_asset(path, root, 'frame', 'image/png', duration_ms=None))
    manifest = root / 'render-manifest.json'
    manifest.write_text(json.dumps({'render_digest': snapshot.digest,
                                    'voice': snapshot.board.options.voice,
                                    'board_digest': snapshot.board.digest,
                                    'video_sha256': video.sha256}, sort_keys=True), encoding='utf-8')
    bundle = RenderBundle(snapshot.board.job_id, snapshot.board.digest, snapshot.render_snapshot_id,
                          video, tuple(grabs), snapshot.board.x_caption, manifest)
    try:
        from .delivery import validate_delivery
    except ImportError:
        from delivery import validate_delivery
    validate_delivery(bundle, root)
    return bundle


def probe_video(ctx: Any, bundle: RenderBundle, *, policy: MediaPolicy) -> dict:
    preflight_terminal(ctx, bundle.mp4.path.parent, policy=policy)
    argv = ['ffprobe', '-v', 'error', '-show_entries',
            'format=duration,size:stream=codec_name,codec_type,width,height', '-of', 'json',
            str(bundle.mp4.path)]
    payload = _terminal(ctx, argv, policy=policy)
    try:
        data = json.loads(payload['output'])
        duration = float(data['format']['duration'])
        streams = data['streams']
    except (TypeError, KeyError, ValueError) as exc:
        raise RenderFailed('invalid ffprobe output') from exc
    if not 0 < duration <= 90.05 or not any(s.get('codec_name') == 'h264' for s in streams):
        raise RenderFailed('MP4 duration/video codec invalid')
    manifest = json.loads(bundle.manifest_path.read_text(encoding='utf-8'))
    if manifest['video_sha256'] != bundle.mp4.sha256:
        raise RenderFailed('manifest video mismatch')
    if manifest['voice'] and not any(s.get('codec_name') == 'aac' for s in streams):
        raise RenderFailed('voice-on release has no AAC')
    _terminal(ctx, ['ffmpeg', '-v', 'error', '-i', str(bundle.mp4.path),
                    '-f', 'null', '-'], policy=policy, timeout=min(600, policy.timeout_s*5))
    if manifest['voice']:
        loudness = _terminal(ctx, ['ffmpeg', '-hide_banner', '-i', str(bundle.mp4.path),
                                   '-vn', '-af', 'volumedetect', '-f', 'null', '-'], policy=policy)
        diagnostic = str(loudness.get('output', '')) + str(loudness.get('stderr', ''))
        found = re.search(r'mean_volume:\s*(-?[\d.]+) dB', diagnostic)
        if not found or float(found.group(1)) <= -50:
            raise RenderFailed('voice-on output silent or unmeasurable')
    return data
