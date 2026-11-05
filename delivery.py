"""Originating-chat MEDIA output contract; transport receipts belong to integration."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from schemas import Asset, RenderBundle, RenderFailed


def _caption(caption: str) -> None:
    if (not caption or len(caption) > 280 or '@NousResearch' not in caption or
        re.search(r'(?i)(?:^|\s)(?:MEDIA|VOICE|AUDIO|FILE):', caption) or
        any(ord(char) < 32 and char not in '\n\t' for char in caption)):
        raise RenderFailed('unsafe or invalid X caption')


def _check(asset: Asset, root: Path, *, expected_kind: str, expected_mime: str) -> None:
    path = asset.path.resolve(strict=False)
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise RenderFailed('delivery asset missing or outside job root')
    if (asset.kind != expected_kind or asset.mime_type != expected_mime or
        not 0 < asset.size_bytes <= 24 * 1024 * 1024 or
        path.stat().st_size != asset.size_bytes or
        hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256):
        raise RenderFailed('delivery asset metadata/hash mismatch')
    if asset.width is None or asset.height is None or asset.width <= 0 or asset.height <= 0:
        raise RenderFailed('delivery asset has no dimensions')


def validate_delivery(bundle: RenderBundle, job_root: Path) -> None:
    root = job_root.resolve(strict=True)
    _caption(bundle.x_caption)
    _check(bundle.mp4, root, expected_kind='video', expected_mime='video/mp4')
    if (bundle.mp4.duration_ms is None or not 0 < bundle.mp4.duration_ms <= 90_000 or
        len(bundle.frames) != 3 or len({a.path for a in bundle.frames}) != 3):
        raise RenderFailed('video duration or exactly-three-grab contract invalid')
    for grab in bundle.frames:
        _check(grab, root, expected_kind='frame', expected_mime='image/png')


def format_delivery(bundle: RenderBundle) -> str:
    # MEDIA extraction is not transport acknowledgement. Integration D records
    # DeliveryReceipt only after the authorized originating adapter confirms send.
    validate_delivery(bundle, bundle.mp4.path.parent)
    caption = ' '.join(bundle.x_caption.split())
    return f'{caption}\nMEDIA:{bundle.mp4.path.absolute()}'
