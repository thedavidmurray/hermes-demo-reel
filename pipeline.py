"""Owner-scoped, at-most-once media orchestration. Never auto-posts to X."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
try:
    from . import store
    from .schemas import Owner, MediaPolicy, ApprovalRequired, Conflict, ContractError, DeliveryReceipt, RenderBundle
    from .media_tools import bind_media_approval, make_title, make_speech, resolve_media_tools, _asset
    from .compositor import preflight_terminal, freeze_render, compose
    from .art import plan_art, render_art_sequence
    from .delivery import validate_delivery
except ImportError:
    import store
    from schemas import Owner, MediaPolicy, ApprovalRequired, Conflict, ContractError, DeliveryReceipt, RenderBundle
    from media_tools import bind_media_approval, make_title, make_speech, resolve_media_tools, _asset
    from compositor import preflight_terminal, freeze_render, compose
    from art import plan_art, render_art_sequence
    from delivery import validate_delivery


def job_directory(owner, job_id):
    from plugins.plugin_storage import plugin_data_dir
    store.validate_owner(owner)
    store._id(job_id)
    base = plugin_data_dir('demo-reel').resolve()
    root = base/'renders'/store.digest(owner)/job_id
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not root.resolve().is_relative_to(base):
        raise ContractError('JOB_DIRECTORY_ESCAPE')
    return root.resolve()


def render_job(ctx, owner: Owner, job_id: str, *, policy: MediaPolicy) -> RenderBundle:
    job = store.load_job(owner,job_id)
    if job.state != 'APPROVED' or not job.approval or not job.board or job.approval.owner != owner:
        raise ApprovalRequired('EXACT_APPROVED_BOARD_REQUIRED')
    if job.error_code == 'RENDER_UNKNOWN':
        raise Conflict('RENDER_UNCERTAIN_NEW_JOB_REQUIRED')
    if datetime.fromisoformat(job.expires_at) <= datetime.now(timezone.utc):
        raise ApprovalRequired('APPROVAL_EXPIRED')
    if job.board.options.screenshots or job.board.options.music:
        raise ContractError('V1_SCREENSHOT_MUSIC_NOT_IMPLEMENTED')
    bind_media_approval(policy,job.board,job.approval)
    resolve_media_tools(ctx)
    root = job_directory(owner,job_id)
    # Cheap preflight cannot spend provider money. Locality asserted from host scope.
    preflight_terminal(ctx,root,policy=policy)
    snapshot = store.load_snapshot(owner,job.evidence_snapshot_id)
    # Persistent compare-and-swap BEFORE any paid media call. Never clear uncertain
    # state on exception, expiry, resume or transport interruption. No retry lease.
    conn = store.open_store()
    try:
        with conn:
            conn.execute('INSERT INTO render_leases VALUES(?,?,?,?)',
                         (job_id,job.board.digest,'CLAIMED',job.expires_at))
    except Exception:
        raise Conflict('RENDER_ALREADY_CLAIMED_DO_NOT_RETRY') from None
    finally:
        conn.close()
    claimed = store.save_job(replace(job,state='RENDERING',error_code='RENDER_UNKNOWN'),
                             expected_job_version=job.job_version)
    title = make_title(ctx,job.board,root,policy=policy)
    speech = make_speech(ctx,job.board,root,policy=policy) if job.board.options.voice else ()
    width, height = (720,1280) if job.board.options.aspect == '9:16' else (1280,720)
    plan = plan_art(snapshot,job.board,width=width,height=height)
    frames = render_art_sequence(plan,snapshot.events,root/'art')
    art = tuple(_asset(path,root,'art','image/png') for path in frames)
    frozen = freeze_render(snapshot,job.board,job.approval,(title,)+tuple(speech)+art,plan)
    (root/'frozen.json').write_text(store.canonical(frozen),encoding='utf-8')
    bundle = compose(ctx,frozen,root,policy=policy)
    # Long-running generation/encoding must not turn an expired or revoked
    # approval into a deliverable. Preserve the durable UNKNOWN claim on failure.
    current = store.load_job(owner,job_id)
    if (current.job_version != claimed.job_version or current.state != 'RENDERING'
            or current.approval != job.approval or current.board != job.board):
        raise ApprovalRequired('APPROVAL_CHANGED_DURING_RENDER')
    if datetime.fromisoformat(current.expires_at) <= datetime.now(timezone.utc):
        raise ApprovalRequired('APPROVAL_EXPIRED')
    validate_delivery(bundle,root)
    (root/'bundle.json').write_text(store.canonical(bundle),encoding='utf-8')
    store.save_job(replace(claimed,state='READY',render_snapshot_id=frozen.render_snapshot_id,error_code=None),
                   expected_job_version=claimed.job_version)
    return bundle


def deliver_job(ctx, owner: Owner, job_id: str) -> DeliveryReceipt:
    job = store.load_job(owner,job_id)
    if job.state == 'DELIVERED' and job.delivery:
        return job.delivery
    if job.state != 'READY' or not job.render_snapshot_id:
        raise ContractError('READY_RENDER_REQUIRED')
    root = job_directory(owner,job_id)
    bundle = store.decode(RenderBundle,(root/'bundle.json').read_text(encoding='utf-8'))
    validate_delivery(bundle,root)
    if bundle.render_snapshot_id != job.render_snapshot_id or bundle.board_digest != job.board.digest:
        raise ContractError('RENDER_BUNDLE_MISMATCH')
    # Sending itself is irreversible. A durable unique claim survives lost ACK;
    # never use an expiring lease or resume to automatically resend.
    conn = store.open_store()
    try:
        with conn:
            conn.execute('INSERT INTO render_leases VALUES(?,?,?,?)',
                         (job_id,'delivery:'+bundle.board_digest,'CLAIMED',job.expires_at))
    except Exception:
        raise Conflict('DELIVERY_UNCERTAIN_DO_NOT_RETRY') from None
    finally:
        conn.close()
    message_id = ctx.send_bundle(bundle,owner)
    receipt = DeliveryReceipt(owner,bundle.render_snapshot_id,bundle.mp4.sha256,message_id,
                              datetime.now(timezone.utc).isoformat())
    store.save_job(replace(job,state='DELIVERED',delivery=receipt),expected_job_version=job.job_version)
    return receipt
