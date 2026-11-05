"""Persistent deterministic questions, then explicit scoped consent and board review.

Host integration must install ctx.demo_owner: a trusted callable returning Owner.
Never register it as a model-supplied field. Handler arguments and model extraction
are untrusted data and cannot provide identity, consent hashes or default sources.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shlex
from typing import Any
import uuid

try:
    from .schemas import *
    from . import store, storyboard
    from .session_reader import read_session
except ImportError:
    from schemas import *
    import store, storyboard
    from session_reader import read_session

FIELDS = ('source','audience','duration_s','aspect','tone','voice')
PROMPTS = {
    'source':'Provide an explicit database path and 1–3 session IDs: "/path/state.db" id1 [id2 id3]. No latest/profile inference.',
    'audience':'Who is the audience? (e.g. plugin developers)',
    'duration_s':'Requested duration, 15–90 seconds?',
    'aspect':'Portrait (9:16) or landscape (16:9)?',
    'tone':'What tone? (e.g. factual, playful, concise)',
    'voice':'Narration voice on or off? Answer explicitly; off is supported.',
}
TERMINAL = frozenset(('CANCELLED','DELIVERED','RENDERING','READY'))


def utcnow():
    return datetime.now(timezone.utc)


def _time(now):
    if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ContractError('TIMEZONE_REQUIRED')
    return now


def validate_interview(draft: InterviewDraft) -> tuple[str,...]:
    missing = []
    for field in FIELDS:
        value = getattr(draft,field)
        valid = value is not None
        if field == 'source' and valid:
            valid = (isinstance(value,SourceRef) and isinstance(value.db_path,Path) and value.db_path.is_absolute() and
                isinstance(value.session_ids,tuple) and 1<=len(value.session_ids)<=3 and
                all(isinstance(s,str) and s and len(s)<=256 for s in value.session_ids) and
                len(set(value.session_ids))==len(value.session_ids))
        elif field in ('audience','tone') and valid:
            valid = isinstance(value,str) and bool(value.strip()) and len(value)<=200 and not re.search(r'[\x00-\x1f\x7f]',value)
        elif field == 'duration_s' and valid: valid = type(value) is int and 15<=value<=90
        elif field == 'aspect' and valid: valid = value in ('9:16','16:9')
        elif field == 'voice' and valid: valid = type(value) is bool
        if not valid: missing.append(field)
    return tuple(missing)


def start_interview(owner: Owner, *, now: datetime) -> Job:
    store.validate_owner(owner)
    _time(now)
    job = Job(1,uuid.uuid4().hex,owner,'AWAITING_INPUT',0,InterviewDraft(),None,None,None,
              None,None,None,None,(now+timedelta(minutes=30)).isoformat(),None)
    return store.save_job(job,expected_job_version=0)


def expire_job(job: Job, *, now: datetime) -> Job:
    _time(now)
    if job.error_code == 'GENERATION_UNKNOWN' or job.state in TERMINAL or job.state == 'WAITING_INPUT_EXPIRED' or now<datetime.fromisoformat(job.expires_at):
        return job
    return store.save_job(replace(job,state='WAITING_INPUT_EXPIRED',approval=None,consent=None,
                                 error_code='EXPIRED'),expected_job_version=job.job_version)


def generation_scope(job,snapshot):
    return store.digest(dict(job_id=job.job_id,owner=job.owner,evidence_snapshot_id=snapshot.snapshot_id,
        evidence_sha256=store.digest(snapshot),options=job.options,
        payload=storyboard.generation_payload(snapshot,job.options)))


def generation_disclosure(job,snapshot) -> str:
    return store.canonical(dict(operation='one storyboard LLM draft through ctx.llm',
        payload=storyboard.generation_payload(snapshot,job.options),
        provider='host-selected provider/model; exact costs unknown; host opt-in required',
        calls=1,timeout_s=60,media_calls_before_board_approval=0,
        destination=dict(platform=job.owner.platform,conversation_id=job.owner.conversation_id),
        scope_sha256=generation_scope(job,snapshot)))


def record_generation_consent(job: Job, *, owner: Owner, disclosure: str,
                              scope_sha256: str, now: datetime) -> Job:
    _time(now)
    if (job.owner != owner or job.state != 'AWAITING_GENERATION_CONSENT' or job.consent is not None or
            now >= datetime.fromisoformat(job.expires_at) or not job.evidence_snapshot_id or not job.options):
        raise ApprovalRequired('GENERATION_CONSENT_REQUIRED')
    snapshot = store.load_snapshot(owner,job.evidence_snapshot_id)
    if disclosure != generation_disclosure(job,snapshot) or scope_sha256 != generation_scope(job,snapshot):
        raise ApprovalRequired('GENERATION_SCOPE_MISMATCH')
    consent = GenerationConsent(uuid.uuid4().hex,owner,snapshot.snapshot_id,disclosure,
        store.digest(disclosure),scope_sha256,now.isoformat())
    return store.save_job(replace(job,consent=consent),expected_job_version=job.job_version)


def _parse(field,answer):
    text = answer.strip()
    lower = text.lower()
    if field == 'source':
        words = shlex.split(text)
        if not 2<=len(words)<=4 or not Path(words[0]).expanduser().is_absolute():
            raise ValueError()
        return SourceRef(Path(words[0]).expanduser().resolve(),tuple(words[1:]))
    if field in ('audience','tone'): return text
    if field == 'duration_s':
        match = re.fullmatch(r'(?:about |around )?(\d{1,2})(?:\s*(?:s|sec|seconds?))?',lower)
        if not match: raise ValueError()
        return int(match[1])
    if field == 'aspect':
        if lower in ('9:16','portrait','vertical'): return '9:16'
        if lower in ('16:9','landscape','horizontal'): return '16:9'
        raise ValueError()
    if field == 'voice':
        if lower in ('yes','on','voice on','narration on','with narration'): return True
        if lower in ('no','off','voice off','narration off','no narration','silent'): return False
        raise ValueError()
    raise ValueError()


def _reply(job,text,code='NEEDS_INPUT'):
    return CommandReply(text,job.job_id,job.state,code)


def _prompt(job):
    prefix = f'Job {job.job_id} — {job.state}\n'
    if job.error_code == 'GENERATION_UNKNOWN':
        return prefix+'Generation outcome UNKNOWN. Never retry this job; cancel and start a new interview. A previous call may have spent funds.'
    if job.state == 'AWAITING_INPUT':
        missing = validate_interview(job.draft)
        return prefix+(PROMPTS[missing[0]] if missing else 'All answers saved. Send retry capture to retry the explicit read-only source.')
    if job.state == 'AWAITING_GENERATION_CONSENT':
        snapshot = store.load_snapshot(job.owner,job.evidence_snapshot_id)
        return prefix+'Generation consent needed. Review exact outbound redacted payload and disclosure:\n'+generation_disclosure(job,snapshot)+f'\nTo affirm: /demo consent {job.job_id} {generation_scope(job,snapshot)}'
    if job.state == 'STORYBOARD_REVIEW' and job.board:
        tools = _tools(job.board)
        urls = _urls(job)
        disclosure = storyboard.board_disclosure(job.board,tools,urls)
        return prefix+'Review exact outbound title/captions/narration/URLs:\n'+disclosure+f'\nTo approve: /demo approve {job.board.digest} {store.digest(disclosure)}\nEdit: /demo edit scene-1 title Recorded session'
    if job.state == 'STORYBOARD_REVIEW':
        return prefix+'Storyboard generation is in progress or uncertain. Never automatically retry. Cancel this job and start a new interview; an earlier call may still have spent funds.'
    return prefix+'Use /demo status '+job.job_id


def advance_interview(job: Job, answer: str, *, now: datetime) -> CommandReply:
    _time(now)
    job = expire_job(job,now=now)
    if job.state != 'AWAITING_INPUT': return _reply(job,_prompt(job))
    if not isinstance(answer,str) or not 1<=len(answer)<=4096:
        raise NeedsInput('BOUNDED_ANSWER_REQUIRED')
    missing = validate_interview(job.draft)
    draft = job.draft
    if missing:
        field = missing[0]
        try:
            candidate = replace(draft,**{field:_parse(field,answer)})
            if field not in validate_interview(candidate): draft = candidate
        except (ValueError,TypeError): pass
    updated = replace(job,draft=draft,expires_at=(now+timedelta(minutes=30)).isoformat())
    # Persist the answer BEFORE capture: source errors must not erase a partial draft.
    job = store.save_job(updated,expected_job_version=job.job_version)
    updated = job
    if not validate_interview(draft):
        options = DemoOptions(draft.source,draft.audience,draft.duration_s,draft.aspect,draft.tone,
                              draft.voice,draft.screenshots,draft.music)
        snapshot = read_session(options.source.db_path,options.source.session_ids)
        sid = store.persist_snapshot(job.owner,snapshot)
        updated = replace(updated,options=options,evidence_snapshot_id=sid,state='AWAITING_GENERATION_CONSENT')
    saved = store.save_job(updated,expected_job_version=job.job_version) if updated != job else job
    return _reply(saved,_prompt(saved))


def _tools(board):
    tools = ['image_generate','terminal']
    if board.options.voice: tools.append('text_to_speech')
    if board.options.screenshots: tools.extend(('browser_navigate','browser_screenshot'))
    # Optional music needs D's actual discovered provider; no invented tool binding.
    if board.options.music: raise CapabilityUnavailable('MUSIC_BINDING_REQUIRED')
    return tuple(sorted(tools))


def _urls(job):
    return store.load_snapshot(job.owner,job.evidence_snapshot_id).public_urls if job.options.screenshots else ()


def _trusted_owner(ctx,session_id,kwargs):
    resolver = getattr(ctx,'demo_owner',None)
    if not callable(resolver) or not callable(getattr(ctx,'demo_confirm',None)):
        raise NeedsInput('TRUSTED_HOST_ADAPTERS_REQUIRED')
    owner = resolver(session_id=session_id,**kwargs)
    store.validate_owner(owner)
    return owner


def _resume(job,now):
    if job.error_code == 'GENERATION_UNKNOWN': raise NeedsInput('GENERATION_UNCERTAIN_CANCEL_REQUIRED')
    if job.state in TERMINAL: raise ContractError('CANNOT_RESUME_STATE')
    # A fresh generation requires explicit fresh consent. Immutable snapshot is reused.
    state = 'AWAITING_INPUT' if validate_interview(job.draft) else 'AWAITING_GENERATION_CONSENT'
    return store.save_job(replace(job,state=state,consent=None,board=None,approval=None,error_code=None,
        expires_at=(now+timedelta(minutes=30)).isoformat()),expected_job_version=job.job_version)


def _edit_draft(job,field,value,now):
    if job.error_code == 'GENERATION_UNKNOWN': raise NeedsInput('GENERATION_UNCERTAIN_CANCEL_REQUIRED')
    if job.state in TERMINAL or job.state in ('CANCELLED','WAITING_INPUT_EXPIRED'):
        raise NeedsInput('RESUME_BEFORE_EDIT')
    draft = replace(job.draft,**{field:_parse(field,value)})
    if field in validate_interview(draft): raise NeedsInput('INVALID_DRAFT_EDIT')
    if field == 'source' and job.evidence_snapshot_id:
        # Source selection creates a new job. Previous evidence remains immutable.
        new = start_interview(job.owner,now=now)
        new = store.save_job(replace(new,draft=draft),expected_job_version=new.job_version)
        store.save_job(replace(job,state='CANCELLED',consent=None,approval=None),expected_job_version=job.job_version)
        return new
    options = None
    state = 'AWAITING_INPUT'
    if not validate_interview(draft) and job.evidence_snapshot_id:
        options = DemoOptions(draft.source,draft.audience,draft.duration_s,draft.aspect,draft.tone,
                              draft.voice,draft.screenshots,draft.music)
        state = 'AWAITING_GENERATION_CONSENT'
    return store.save_job(replace(job,draft=draft,options=options,state=state,board=None,consent=None,
        approval=None,error_code=None,expires_at=(now+timedelta(minutes=30)).isoformat()),
        expected_job_version=job.job_version)


def _confirm(ctx, action, job, scope, raw_args, kwargs):
    confirm = getattr(ctx,'demo_confirm',None)
    if not callable(confirm) or confirm(action=action,job=job,scope=scope,raw_args=raw_args,**kwargs) is not True:
        raise ApprovalRequired('TRUSTED_AFFIRMATIVE_ACTION_REQUIRED')


def _handle(ctx,raw_args,session_id='',**kwargs) -> CommandReply:
    owner = _trusted_owner(ctx,session_id,kwargs)
    now = utcnow()
    if kwargs.get('interactive') is False or kwargs.get('cron') is True:
        raise NeedsInput('NEEDS_INPUT: unattended approval cannot be fabricated')
    if not isinstance(raw_args,str) or len(raw_args)>8000: raise NeedsInput('BOUNDED_COMMAND_REQUIRED')
    raw = raw_args.strip()
    if not raw or raw == 'start':
        try: job = store.active_job(owner)
        except ContractError as exc:
            if str(exc) != 'NO_ACTIVE_JOB': raise
            job = start_interview(owner,now=now)
        job = expire_job(job,now=now)
        return _reply(job,_prompt(job))
    verb, _, tail = raw.partition(' ')
    selected_job = None
    if verb == 'for':
        selected_id, separator, raw = tail.partition(' ')
        if not separator: raise NeedsInput('JOB_AND_ANSWER_REQUIRED')
        selected_job = store.load_job(owner,selected_id)
        verb, _, tail = raw.partition(' ')
    if verb in ('resume','status','cancel'):
        job = store.load_job(owner,tail.strip()) if tail.strip() else store.active_job(owner)
        if verb == 'status':
            job = expire_job(job,now=now)
            return _reply(job,_prompt(job),'OK')
        if verb == 'cancel':
            if job.state in ('RENDERING','READY','DELIVERED'): raise ContractError('CANNOT_CANCEL_STATE')
            if job.state != 'CANCELLED':
                job = store.save_job(replace(job,state='CANCELLED',approval=None,consent=None),expected_job_version=job.job_version)
            return _reply(job,f'Job {job.job_id}: CANCELLED','OK')
        if job.state not in ('WAITING_INPUT_EXPIRED','BLOCKED_APPROVAL','BLOCKED_CAPABILITY','FAILED'):
            job = expire_job(job,now=now)
            return _reply(job,_prompt(job))
        job = _resume(job,now)
        return _reply(job,_prompt(job))
    if verb == 'consent':
        parts = tail.split()
        if len(parts)!=2: raise NeedsInput('EXPLICIT_GENERATION_SCOPE_REQUIRED')
        job = store.load_job(owner,parts[0])
        snapshot = store.load_snapshot(owner,job.evidence_snapshot_id)
        _confirm(ctx,'generation',job,parts[1],raw,kwargs)
        job = record_generation_consent(job,owner=owner,disclosure=generation_disclosure(job,snapshot),scope_sha256=parts[1],now=now)
        try:
            board = storyboard.build_storyboard(ctx,snapshot,job.options,job.consent,job_id=job.job_id)
            current = store.load_job(owner,job.job_id)
            job = store.save_job(replace(current,board=board,error_code=None),expected_job_version=current.job_version)
        except (ApprovalRequired,CapabilityUnavailable,ContractError) as exc:
            # Never log provider text; report only a bounded contract code/class.
            import logging
            import re
            code = str(exc)
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code):
                code = 'REDACTED_ERROR'
            logging.getLogger(__name__).warning(
                'demo-reel storyboard failed: step=build_storyboard error_class=%s code=%s',
                type(exc).__name__, code)
            current = store.load_job(owner,job.job_id)
            if current.error_code == 'GENERATION_UNKNOWN':
                if isinstance(exc,ApprovalRequired):
                    current = store.save_job(replace(current,state='BLOCKED_APPROVAL'),expected_job_version=current.job_version)
                return _reply(current,_prompt(current),'NEEDS_INPUT')
            state = 'BLOCKED_APPROVAL' if isinstance(exc,ApprovalRequired) else ('BLOCKED_CAPABILITY' if isinstance(exc,CapabilityUnavailable) else 'FAILED')
            job = store.save_job(replace(current,state=state,error_code=state),expected_job_version=current.job_version)
            return _reply(job,state,state)
        return _reply(job,_prompt(job))
    job = expire_job(selected_job or store.active_job(owner),now=now)
    if verb == 'approve':
        parts = tail.split()
        if len(parts)!=2: raise NeedsInput('EXACT_BOARD_AND_DISCLOSURE_HASH_REQUIRED')
        _confirm(ctx,'board',job,parts[0],raw,kwargs)
        job = storyboard.approve_storyboard(job,owner=owner,digest=parts[0],disclosure_sha256=parts[1],
            allowed_external_tools=_tools(job.board),approved_urls=_urls(job),now=now)
        return _reply(job,'APPROVED. Render must be separately requested through the host pipeline.','OK')
    if verb == 'edit':
        field, separator, value = tail.partition(' ')
        if field in FIELDS and separator:
            if field == 'source':
                source = _parse(field,value)
                _confirm(ctx,'source',job,store.digest(source),raw,kwargs)
            job = _edit_draft(job,field,value,now)
            return _reply(job,_prompt(job))
        if job.state not in ('STORYBOARD_REVIEW','APPROVED') or not job.board:
            raise NeedsInput('BOARD_REQUIRED')
        parts = tail.split(' ',2)
        if len(parts)!=3: raise NeedsInput('EDIT_SCENE_FIELD_VALUE_REQUIRED')
        board = storyboard.edit_storyboard(job.board,(Edit(*parts),))
        storyboard.validate_storyboard(board,store.load_snapshot(owner,job.evidence_snapshot_id))
        job = store.save_job(replace(job,board=board,approval=None,state='STORYBOARD_REVIEW'),expected_job_version=job.job_version)
        return _reply(job,_prompt(job))
    if job.state == 'AWAITING_INPUT' and job.draft.source is None:
        try: source = _parse('source',raw)
        except (ValueError,TypeError): source = None
        if source is not None:
            _confirm(ctx,'source',job,store.digest(source),raw,kwargs)
    return advance_interview(job,raw,now=now)


def _safe_handle(ctx,raw_args,session_id='',**kwargs):
    try: return _handle(ctx,raw_args,session_id,**kwargs)
    except Conflict: return CommandReply('CONFLICT: reload job status before retrying.',None,'CONFLICT','CONFLICT')
    except ApprovalRequired: return CommandReply('NEEDS_INPUT: explicit matching approval required.',None,'NEEDS_INPUT','NEEDS_INPUT')
    except (NeedsInput,ContractError,TypeError,ValueError,AttributeError):
        return CommandReply('NEEDS_INPUT: trusted owner, explicit source/job and valid answers required.',None,'NEEDS_INPUT','NEEDS_INPUT')
    except CapabilityUnavailable:
        return CommandReply('BLOCKED_CAPABILITY: required host capability unavailable.',None,'BLOCKED_CAPABILITY','BLOCKED_CAPABILITY')


def handle_demo(ctx: Any, raw_args: str, *, session_id: str = '', **kwargs) -> str:
    return _safe_handle(ctx,raw_args,session_id,**kwargs).text


def handle_reply(ctx: Any, args: dict, **kwargs) -> str:
    # Extraction is data only. args can neither supply owner nor interactive/approval flags.
    answer = args.get('answer','') if isinstance(args,dict) else None
    if not isinstance(answer,str):
        return store.canonical(CommandReply('NEEDS_INPUT: answer must be text.',None,'NEEDS_INPUT','NEEDS_INPUT'))
    if isinstance(args,dict) and args.get('job_id'):
        try: store._id(args['job_id'])
        except ContractError:
            return store.canonical(CommandReply('NEEDS_INPUT: invalid job ID.',None,'NEEDS_INPUT','NEEDS_INPUT'))
        answer = 'for '+args['job_id']+' '+answer
    return store.canonical(_safe_handle(ctx,answer,**kwargs))

# Normative prototypes also exposed here for callers treating interview as the facade.
persist_snapshot = store.persist_snapshot
load_snapshot = store.load_snapshot
