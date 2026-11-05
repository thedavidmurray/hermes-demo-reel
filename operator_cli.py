"""Operator-only local CLI, never registered as a model-facing tool.

Three invocations preserve the review boundary: source -> redaction/LLM consent ->
exact storyboard/media consent. Scripted answers cannot contain approval flags.
Credentials and tool implementations are always owned by the Hermes host.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shlex
from types import SimpleNamespace

try:
    from . import interview, storyboard, store
    from .host import HostAdapter
    from .schemas import InterviewDraft, ContractError, MediaPolicy
except ImportError:
    import interview, storyboard, store
    from host import HostAdapter
    from schemas import InterviewDraft, ContractError, MediaPolicy


def canary_scan(text: str) -> None:
    """Fail closed on common credential, email and private-path canaries."""
    patterns = (r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', r'/(?:Users|home|private|var|tmp)/',
                r'(?i)\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}',
                r'(?i)\bBearer\s+\S+', r'-----BEGIN .*PRIVATE KEY-----')
    if any(re.search(p,text) for p in patterns):
        raise ValueError('REDACTION_CANARY_MATCH')


def read_answers(path: Path) -> InterviewDraft:
    """Read only bounded interview settings; no paths, IDs or approvals in JSON."""
    if path.stat().st_size > 8192:
        raise ValueError('ANSWERS_TOO_LARGE')
    data = json.loads(path.read_text())
    required = {'audience','duration_s','aspect','tone','voice'}
    if not isinstance(data,dict) or set(data) != required:
        raise ValueError('ANSWERS_FIELDS_INVALID')
    draft = InterviewDraft(**data)
    if interview.validate_interview(draft) != ('source',):
        raise ValueError('ANSWERS_INVALID')
    canary_scan(json.dumps(data))
    return draft


def _emit(code,**values):
    print(json.dumps(dict(code=code,**values)))
    return {'OK':0,'NEEDS_INPUT':2,'BLOCKED_CAPABILITY':3}.get(code,3)


def run_operator(args, *, ctx=None):
    """Execute explicit local-operator intent; missing flags return immediately."""
    if not args.operator or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',args.operator):
        return _emit('NEEDS_INPUT',reason='Explicit local --operator required; no unattended defaults.')
    if args.action == 'interview' and not (args.answers and args.db and args.session_id and args.consent_source):
        return _emit('NEEDS_INPUT',reason='Explicit --answers, --db, --session-id and --consent-source required.')
    if args.action == 'draft' and not (args.job and args.consent_generation):
        return _emit('NEEDS_INPUT',reason='Review redaction-preview.json, then supply --job and exact --consent-generation hash.')
    if args.action == 'render' and not (args.job and args.approve_board and args.approve_disclosure):
        return _emit('NEEDS_INPUT',reason='Review storyboard.json, then supply --job, --approve-board and --approve-disclosure hashes.')
    if args.action != 'interview' and ctx is None:
        return _emit('BLOCKED_CAPABILITY',reason='Use the installed hermes demo-reel command for host LLM and tool dispatch; standalone Python is offline only.')
    try:
        # Preparation is strictly offline. Paid stages use the real context supplied
        # by register_cli_command; do not bootstrap/import a second agent runtime.
        host = ctx if ctx is not None else SimpleNamespace()
        invocation = dict(session_id='operator-'+args.operator,source=None,platform='cli')
        def adapter(command=''):
            return HostAdapter(host,invocation=invocation,raw_args=command)
        owner = adapter().demo_owner(session_id=invocation['session_id'])
        def command(text):
            reply = interview._handle(adapter(text),text,session_id=invocation['session_id'])
            if not reply.job_id:
                raise ValueError('CLI_JOB_REQUIRED')
            return store.load_job(owner,reply.job_id)
        if args.action == 'interview':
            settings = read_answers(Path(args.answers))
            # This is an explicit source path, never profile/latest inference.
            source = Path(args.db).expanduser().resolve(strict=True)
            job = interview.start_interview(owner,now=interview.utcnow())
            values = [shlex.join([str(source),*args.session_id]),settings.audience,
                      str(settings.duration_s),settings.aspect,settings.tone,'on' if settings.voice else 'off']
            for value in values:
                # Data-only field advancement: never attest JSON preferences as
                # authentic commands (a tone could literally start with 'consent').
                reply = interview.advance_interview(job,value,now=interview.utcnow())
                job = store.load_job(owner,reply.job_id)
            if job.state != 'AWAITING_GENERATION_CONSENT':
                raise ValueError('CLI_INTERVIEW_INCOMPLETE')
        else:
            job = store.load_job(owner,args.job)
        home = Path(os.environ['HERMES_HOME']).resolve()
        review = home/'demo-reel-review'/job.job_id
        review.mkdir(parents=True,exist_ok=True,mode=0o700)
        snapshot = store.load_snapshot(owner,job.evidence_snapshot_id)
        disclosure = interview.generation_disclosure(job,snapshot)
        canary_scan(disclosure)
        (review/'redaction-preview.json').write_text(disclosure)
        if args.action == 'interview':
            return _emit('OK',job_id=job.job_id,state=job.state,review_dir=str(review),
                         generation_scope=interview.generation_scope(job,snapshot),canary_scan='PASS',provider_calls=0)
        if args.action == 'draft':
            job = command('consent '+job.job_id+' '+args.consent_generation)
            if not job.board:
                return _emit('BLOCKED_CAPABILITY',job_id=job.job_id,state=job.state,
                             reason='Host LLM failed or uncertain. Do not retry this job.')
        if not job.board:
            raise ValueError('STORYBOARD_REQUIRED')
        tools = interview._tools(job.board)
        urls = interview._urls(job)
        media = storyboard.board_disclosure(job.board,tools,urls)
        canary_scan(media)
        (review/'storyboard.json').write_text(media)
        if args.action == 'draft':
            return _emit('OK',job_id=job.job_id,state=job.state,review_dir=str(review),
                         board_digest=job.board.digest,disclosure_sha256=store.digest(media),canary_scan='PASS')
        job = command('for '+job.job_id+' approve '+args.approve_board+' '+args.approve_disclosure)
        try:
            from .pipeline import render_job
        except ImportError:
            from pipeline import render_job
        bundle = render_job(adapter(),owner,job.job_id,policy=MediaPolicy(network_allowed=True))
        return _emit('OK',job_id=job.job_id,mp4=str(bundle.mp4.path),frames=[str(f.path) for f in bundle.frames],
                     x_caption=bundle.x_caption,live_chat_verified=False)
    except (ContractError,ValueError,TypeError,OSError,KeyError,ImportError) as exc:
        # Return only the exception class; provider messages can expose private paths.
        return _emit('BLOCKED_CAPABILITY',error_type=type(exc).__name__,reason='Host unavailable, invalid source/settings or unmatched/expired approval. No automatic provider retry.')
