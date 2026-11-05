"""Host-dispatched media generation. No provider credentials or raw provider SDK here."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shlex
import shutil
import socket
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from schemas import (ApprovalRequired, Asset, CapabilityUnavailable, MediaPolicy,
                     RenderFailed, Storyboard, ToolResult)

_CALL_LOCK = threading.Lock()
_MEDIA_LIMIT = 24 * 1024 * 1024
_REQUIRED = {"image_generate": {"prompt", "aspect_ratio"},
             "text_to_speech": {"text", "output_path"}, "terminal": {"command"}}


def _host_definitions(ctx: Any) -> dict[str, set[str]]:
    """Read installed host registry schemas; never guess a tool's argument contract."""
    try:
        from tools.registry import registry
        definitions = registry.get_definitions(set(_REQUIRED) | {"browser_navigate", "browser_screenshot"}, quiet=True)
        return {item['function']['name']: set(item['function']['parameters']['properties'])
                for item in definitions}
    except (ImportError, AttributeError, KeyError, TypeError) as exc:
        raise CapabilityUnavailable('installed host tool registry unavailable') from exc


def resolve_media_tools(ctx: Any) -> dict[str, str]:
    available = _host_definitions(ctx)
    for name, arguments in _REQUIRED.items():
        if not arguments <= available.get(name, set()):
            raise CapabilityUnavailable(f'required host tool/schema unavailable: {name}')
    return {name: name for name in available}


def call_tool(ctx: Any, name: str, arguments: dict, *, policy: MediaPolicy) -> ToolResult:
    """One shared policy instance is the job-wide call ledger; failures spend a call too."""
    if not callable(getattr(ctx, 'dispatch_tool', None)):
        raise CapabilityUnavailable('host tool dispatcher unavailable')
    with _CALL_LOCK:
        count = policy.__dict__.get('_demo_reel_calls', 0)
        if count >= min(24, policy.max_calls):
            raise CapabilityUnavailable('job tool dispatch budget exhausted')
        policy.__dict__['_demo_reel_calls'] = count + 1
    try:
        raw = ctx.dispatch_tool(name, arguments)
    except Exception as exc:
        # An exception may mean timeout, denied, or in-flight paid generation. Never retry.
        raise ApprovalRequired(f'host dispatch failed/unknown for {name}; explicit retry required') from exc
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RenderFailed(f'invalid {name} host result') from exc
    if not isinstance(data, dict):
        raise RenderFailed(f'invalid {name} host result')
    success = data.get('success', data.get('ok'))
    # The installed terminal tool reports exit_code/output/error, not success.
    if name == 'terminal' and success is None:
        success = data.get('exit_code') == 0 and not data.get('error')
    if success is False or 'error' in data and data['error']:
        error = str(data.get('error') or '')[:100].lower()
        if any(token in error for token in ('denied', 'permission', 'approval', 'not allowed', 'cancel')):
            raise ApprovalRequired(f'{name} denied by host')
        raise RenderFailed(f'{name} failed or returned an error')
    if data.get('exit_code', 0) not in (0, None):
        raise RenderFailed(f'{name} failed (nonzero exit)')
    if success is not True:
        raise RenderFailed(f'{name} result did not confirm success')
    return ToolResult(True, str(data.get('dispatch_id') or ''), data, None)


def _confined(path: Path, root: Path, *, must_exist: bool = True) -> Path:
    base = Path(root).resolve(strict=True)
    resolved = Path(path).resolve(strict=must_exist)
    if not resolved.is_relative_to(base) or (must_exist and not resolved.is_file()):
        raise RenderFailed('asset path outside job or missing')
    return resolved


def _asset(path: Path, root: Path, kind: str, mime: str, *, scene_id: str | None = None,
           duration_ms: int | None = None, dispatch_id: str | None = None,
           provenance: tuple[str, ...] = ()) -> Asset:
    path = _confined(path, root)
    size = path.stat().st_size
    if size <= 0 or size > _MEDIA_LIMIT:
        raise RenderFailed('asset size invalid')
    width = height = None
    if mime == 'image/png':
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                if width < 1 or height < 1 or image.format != 'PNG':
                    raise RenderFailed('invalid PNG asset')
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise RenderFailed('invalid image asset') from exc
    return Asset(f'{kind}-{path.stem}', kind, path, hashlib.sha256(path.read_bytes()).hexdigest(),
                 mime, size, duration_ms, width, height, scene_id, provenance, dispatch_id)


def make_title(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> Asset:
    if not policy.network_allowed:
        raise ApprovalRequired('title generation not approved')
    resolve_media_tools(ctx)
    root = Path(job_dir).resolve(strict=True)
    title = ' '.join(board.scenes[0].title.split())[:100]
    target = root / 'title.png'
    if target.exists() or target.is_symlink():
        raise RenderFailed('title destination already exists')
    if not title:
        raise RenderFailed('missing approved title')
    result = call_tool(ctx, 'image_generate', {
        'prompt': f'Abstract cinematic title background, no text, no logos; {title}',
        'aspect_ratio': 'portrait' if board.options.aspect == '9:16' else 'landscape',
    }, policy=policy)
    generated = result.data.get('image')
    if not isinstance(generated, str) or not Path(generated).is_absolute() or not Path(generated).is_file():
        raise CapabilityUnavailable('host image_generate did not supply a shared local raster path')
    source = Path(generated).resolve(strict=True)
    target = root / 'title.png'
    if source != target:
        try:
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                image.save(target, format='PNG')
        except (OSError, UnidentifiedImageError) as exc:
            raise RenderFailed('host image is not a valid raster') from exc
    return _asset(target, root, 'title', 'image/png', dispatch_id=result.dispatch_id)


def _audio_duration_ms(ctx: Any, path: Path, *, policy: MediaPolicy) -> int:
    from compositor import preflight_terminal
    preflight_terminal(ctx, path.parent, policy=policy)
    argv = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(path)]
    result = call_tool(ctx, 'terminal', {'command': shlex.join(argv), 'timeout': policy.timeout_s}, policy=policy)
    try:
        duration = float(json.loads(result.data['output'])['format']['duration'])
        if not 0 < duration <= 90:
            raise ValueError('invalid duration')
        return round(duration * 1000)
    except (ValueError, KeyError, TypeError) as exc:
        raise RenderFailed('unprobeable TTS audio') from exc


def make_speech(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> tuple[Asset, ...]:
    if not board.options.voice:
        return ()
    if not policy.network_allowed:
        raise ApprovalRequired('speech generation not approved')
    resolve_media_tools(ctx)
    root = Path(job_dir).resolve(strict=True)
    if not 1 <= len(board.scenes) <= 6:
        raise RenderFailed('voice requires 1..6 approved scenes')
    if any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', scene.scene_id) for scene in board.scenes):
        raise RenderFailed('invalid scene id')
    speech = []
    for scene in board.scenes:
        if not scene.narration.strip():
            raise RenderFailed('empty scene narration')
        result = call_tool(ctx, 'text_to_speech', {
            'text': scene.narration, 'output_path': str(root / f'speech-{scene.scene_id}.mp3')
        }, policy=policy)
        paths = result.data.get('file_paths')
        if paths is not None and (not isinstance(paths, list) or len(paths) != 1):
            raise RenderFailed('TTS returned multiple unapproved audio files')
        supplied = result.data.get('file_path')
        if not isinstance(supplied, str):
            raise RenderFailed('TTS returned no local file')
        path = _confined(Path(supplied), root)
        duration = _audio_duration_ms(ctx, path, policy=policy)
        if duration > scene.end_ms - scene.start_ms:
            raise RenderFailed('TTS exceeds approved scene; revise narration and reapprove')
        speech.append(_asset(path, root, 'speech', 'audio/mpeg',
                             scene_id=scene.scene_id, duration_ms=duration,
                             dispatch_id=result.dispatch_id))
    return tuple(speech)


def _public_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment or len(url) > 2048):
        raise ApprovalRequired('screenshot URL must be an approved public HTTPS URL')
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host.endswith(('.local', '.internal', '.localhost')) or '.' not in host:
            raise ApprovalRequired('private screenshot destination')
    else:
        if not ip.is_global:
            raise ApprovalRequired('private screenshot destination')
    return url


def capture_screenshots(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> tuple[Asset, ...]:
    if not board.options.screenshots:
        return ()
    if not policy.network_allowed:
        raise ApprovalRequired('screenshot capture not approved')
    # A storyboard contains no source URL evidence. The integration owner must bind an
    # immutable evidence witness and DNS/redirect guard before this v1 path can run.
    witness = getattr(ctx, 'demo_public_url_witness', None)
    if not callable(witness):
        raise CapabilityUnavailable('v1 screenshot provenance/SSRF witness unavailable')
    urls = tuple(_public_url(url) for url in policy.approved_urls)
    if not urls or not all(witness(url, board.evidence_snapshot_id) is True for url in urls):
        raise ApprovalRequired('screenshot URL absent from immutable approved evidence')
    raise CapabilityUnavailable('v1 browser redirect/DNS guard not established by installed host')


def make_music(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> Asset | None:
    if not board.options.music:
        return None
    if not policy.network_allowed:
        raise ApprovalRequired('music not approved')
    raise CapabilityUnavailable('v1 licensed music provider not configured; no substitute audio')
