"""Consent-reviewed, versioned excerpt catalog and narrative constraints.

Citations prove provenance, NOT semantic truth. Exact-board operator approval is
mandatory. Never execute text from a historical session or fetch its file paths.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
try:
    from .schemas import ContractError
except ImportError:
    from schemas import ContractError

KINDS = frozenset(('ask','file_input','patch_input','terminal_input','tool_output'))
# Deliberately conservative. This is a preview aid, not a DLP guarantee.
SENSITIVE = re.compile(r'(?i)(secret|token|password|passwd|api[_ -]?key|access[_ -]?token|authorization|bearer |private.key|BEGIN .* KEY|FAL_KEY|sk-[a-zA-Z0-9]|gh[pousr]_)')
DIRECTIVE = re.compile(r'(?i)(ignore (?:all |previous |these )?instructions|system prompt|developer message|you are (?:an? |the )|send (?:the )?(?:token|key|secret)|https?://)')
PATH = re.compile(r'(?:/(?:Users|home|private|var|tmp|etc|root|Volumes|opt)/[^\s\"\'<>;,})]*|[A-Za-z]:\\[^\s\"\']*|~/[^\s\"\']*)')
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')


def redact_excerpt(text: str) -> str:
    if not isinstance(text,str): return ''
    lines = []
    for line in text[:20000].splitlines()[:100]:
        if SENSITIVE.search(line) or DIRECTIVE.search(line):
            lines.append('[sensitive or directive line omitted]')
        else:
            line = EMAIL.sub('[email]',PATH.sub('[private path]',line))
            lines.append(''.join(c for c in line if c == '\t' or ord(c)>=32 and ord(c)!=127)[:240])
    return '\n'.join(lines).strip()[:1800]


def capture_excerpt(event, text: str, kind: str) -> dict:
    if kind not in KINDS: raise ContractError('UNKNOWN_EXCERPT_KIND')
    text = redact_excerpt(text)
    return dict(event_id=event.event_id,kind=kind,text=text,
                sha256=hashlib.sha256(text.encode()).hexdigest())


def excerpt_catalog(snapshot) -> list[dict]:
    if snapshot.schema_version != 2: raise ContractError('NARRATIVE_EVIDENCE_REQUIRED')
    try:
        data = json.loads(snapshot.safe_summary)
        entries = data['excerpts']
        if set(data) != {'excerpts'} or not isinstance(entries,list) or len(entries)>500:
            raise ValueError()
        known = {e.event_id:e for e in snapshot.events}
        used = set()
        for entry in entries:
            if set(entry) != {'event_id','kind','text','sha256'}: raise ValueError()
            eid, kind, text = entry['event_id'], entry['kind'], entry['text']
            if (eid not in known or (eid,kind) in used or kind not in KINDS or not isinstance(text,str)
                    or not text or len(text)>1800 or hashlib.sha256(text.encode()).hexdigest()!=entry['sha256']):
                raise ValueError()
            if kind=='ask' and known[eid].kind!='user': raise ValueError()
            if kind!='ask' and known[eid].kind!='tool': raise ValueError()
            used.add((eid,kind))
        return entries
    except (ValueError,TypeError,KeyError):
        raise ContractError('CORRUPT_NARRATIVE_EVIDENCE') from None


def caption_beats(scene) -> list[tuple[int,int,str]]:
    duration = scene.end_ms-scene.start_ms
    count = max(1,math.ceil(duration/4000))
    words = scene.caption.split()
    if duration/count<2000 or len(words)<count*2 or len(words)>count*12:
        raise ContractError('CAPTION_CADENCE_REQUIRES_REVISION')
    return [(scene.start_ms+duration*i//count, scene.start_ms+duration*(i+1)//count,
             ' '.join(words[len(words)*i//count:len(words)*(i+1)//count])) for i in range(count)]


def validate_narrative(board, snapshot) -> None:
    entries = excerpt_catalog(snapshot)
    by_event = {}
    for e in entries: by_event.setdefault(e['event_id'],[]).append(e)
    asks = {e['event_id'] for e in entries if e['kind']=='ask'}
    artifacts = {e['event_id'] for e in entries if e['kind']!='ask'}
    if not asks or len(artifacts)<3: raise ContractError('INSUFFICIENT_NARRATIVE_EVIDENCE')
    if not 5<=len(board.scenes)<=6: raise ContractError('THREE_ACT_NARRATIVE_REQUIRED')
    if not board.scenes[0].event_ids or board.scenes[0].event_ids[0] not in asks:
        raise ContractError('ASK_EVIDENCE_REQUIRED')
    if not board.scenes[-1].event_ids or not any(e['kind']=='tool_output' for e in by_event.get(board.scenes[-1].event_ids[0],[])):
        raise ContractError('RESULT_OUTPUT_REQUIRED')
    work_ids = []
    for i,s in enumerate(board.scenes):
        if not s.event_ids or not set(s.event_ids)<=set(by_event): raise ContractError('NARRATIVE_CITATION_REQUIRED')
        if i and s.event_ids[0] not in artifacts: raise ContractError('VISIBLE_ARTIFACT_REQUIRED')
        if i and i<len(board.scenes)-1: work_ids.append(s.event_ids[0])
        if s.visual!='timeline': raise ContractError('ARTIFACT_FOREGROUND_REQUIRED')
        for text,limit in ((s.title,90),(s.caption,300),(s.narration,600)):
            if (not isinstance(text,str) or not 1<=len(text)<=limit or text!=redact_excerpt(text)
                    or '[' in text or ']' in text):
                raise ContractError('UNSAFE_OR_UNBOUNDED_NARRATIVE_COPY')
            if re.search(r'(?i)recorded (?:session|terminal|write_file|.*invocation)|reported status|\d+ recorded events',text):
                raise ContractError('LOG_COPY_NOT_A_STORY')
        caption_beats(s)
    if len(set(work_ids))<3: raise ContractError('THREE_DISTINCT_WORK_MOMENTS_REQUIRED')
    if len(board.x_caption)>280 or '@NousResearch' not in board.x_caption or '\n' in board.x_caption:
        raise ContractError('INVALID_X_CAPTION')
    # Tag text is permitted only here, not in source excerpts or provider prompts.
    safe_caption = board.x_caption.replace('@NousResearch','NousResearch')
    if safe_caption!=redact_excerpt(safe_caption): raise ContractError('UNSAFE_X_CAPTION')
