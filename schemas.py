"""Frozen demo-reel wire declarations only; implementation belongs to lane D."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

@dataclass(frozen=True)
class Owner:
    profile: str
    session_id: str
    platform: str
    conversation_id: str
    user_id: str

@dataclass(frozen=True)
class Event:
    event_id: str
    session_id: str
    row_id: int
    t_ms: int
    kind: str
    tool: str
    status: Literal['ok','error','unknown']
    label: str
    duration_ms: int | None

@dataclass(frozen=True)
class DemoOptions:
    source: SourceRef
    audience: str
    duration_s: int
    aspect: Literal['9:16','16:9']
    tone: str
    voice: bool
    screenshots: bool = False
    music: bool = False

@dataclass(frozen=True)
class Scene:
    scene_id: str
    start_ms: int
    end_ms: int
    title: str
    caption: str
    narration: str
    event_ids: tuple[str,...]
    visual: Literal['title','timeline','art','screenshot']

@dataclass(frozen=True)
class Storyboard:
    schema_version: int
    job_id: str
    board_revision: int
    evidence_snapshot_id: str
    source_sha256: str
    options: DemoOptions
    scenes: tuple[Scene,...]
    x_caption: str
    digest: str

@dataclass(frozen=True)
class Approval:
    owner: Owner
    storyboard_digest: str
    board_revision: int
    disclosure_sha256: str
    approved_at: str
    allowed_external_tools: tuple[str,...]
    approved_urls: tuple[str,...]

@dataclass(frozen=True)
class ArtPlan:
    seed: int
    source_sha256: str
    event_ids: tuple[str,...]
    width: int
    height: int
    fps: int
    duration_ms: int

@dataclass(frozen=True)
class SourceRef:
    db_path: Path
    session_ids: tuple[str, ...]

@dataclass(frozen=True)
class InterviewDraft:
    source: SourceRef | None = None
    audience: str | None = None
    duration_s: int | None = None
    aspect: Literal["9:16", "16:9"] | None = None
    tone: str | None = None
    voice: bool | None = None
    screenshots: bool = False
    music: bool = False

@dataclass(frozen=True)
class SessionSnapshot:
    schema_version: int
    snapshot_id: str
    source: SourceRef
    captured_at: str
    source_sha256: str
    events: tuple[Event, ...]
    safe_summary: str
    public_urls: tuple[str, ...]

@dataclass(frozen=True)
class GenerationConsent:
    consent_id: str
    owner: Owner
    evidence_snapshot_id: str
    disclosure: str
    disclosure_sha256: str
    scope_sha256: str
    approved_at: str

@dataclass(frozen=True)
class Asset:
    asset_id: str
    kind: Literal[
        "title", "speech", "screenshot", "music", "art",
        "video", "frame"
    ]
    path: Path
    sha256: str
    mime_type: str
    size_bytes: int
    duration_ms: int | None
    width: int | None
    height: int | None
    scene_id: str | None
    provenance_event_ids: tuple[str, ...]
    dispatch_id: str | None

@dataclass(frozen=True)
class RenderSnapshot:
    schema_version: int
    render_snapshot_id: str
    evidence: SessionSnapshot
    board: Storyboard
    approval: Approval
    assets: tuple[Asset, ...]
    art_plan: "ArtPlan"
    fps: int
    width: int
    height: int
    digest: str

@dataclass(frozen=True)
class DeliveryReceipt:
    owner: Owner
    render_snapshot_id: str
    video_sha256: str
    platform_message_id: str
    delivered_at: str

@dataclass(frozen=True)
class Job:
    schema_version: int
    job_id: str
    owner: Owner
    state: str
    job_version: int
    draft: InterviewDraft
    options: DemoOptions | None
    evidence_snapshot_id: str | None
    consent: GenerationConsent | None
    board: Storyboard | None
    approval: Approval | None
    render_snapshot_id: str | None
    delivery: DeliveryReceipt | None
    expires_at: str
    error_code: str | None

@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str
    tool: str
    args_sha256: str
    status: str
    asset_sha256: str | None
    elapsed_ms: int

@dataclass(frozen=True)
class RenderBundle:
    job_id: str
    board_digest: str
    render_snapshot_id: str
    mp4: Asset
    frames: tuple[Asset,...]
    x_caption: str
    manifest_path: Path

@dataclass(frozen=True)
class CommandReply:
    text: str
    job_id: str | None
    state: str
    code: str

@dataclass(frozen=True)
class Edit:
    scene_id: str
    field: Literal['title','caption','narration']
    value: str

@dataclass(frozen=True)
class MediaPolicy:
    timeout_s: int = 120
    max_calls: int = 24
    network_allowed: bool = False
    approved_urls: tuple[str,...] = ()

@dataclass(frozen=True)
class ToolResult:
    ok: bool
    dispatch_id: str
    data: dict
    error_code: str | None

class ContractError(Exception): pass
class Conflict(ContractError): pass
class NeedsInput(ContractError): pass
class ApprovalRequired(ContractError): pass
class CapabilityUnavailable(ContractError): pass
class RenderFailed(ContractError): pass
