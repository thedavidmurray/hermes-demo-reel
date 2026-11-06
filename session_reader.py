"""Explicit, read-only capture. Historical prose/arguments never leave this module.

Only recognized tool categories and explicit result bits become evidence. Invocation
and result join uses (session_id, call_id); durations are unknown, not wall gaps.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
import uuid

try:  # Standalone contract imports and host package imports use the same declarations.
    from .schemas import ContractError, Event, SessionSnapshot, SourceRef
except ImportError:
    from schemas import ContractError, Event, SessionSnapshot, SourceRef

TOOLS = frozenset(('terminal','read_file','write_file','patch','search_files','web_search',
                   'web_extract','browser_navigate','browser_screenshot','image_generate',
                   'text_to_speech','execute_code','vision_analyze','delegate_task'))
REQUIRED = {'sessions': {'id','started_at'}, 'messages':
            {'id','session_id','role','content','tool_call_id','tool_calls','tool_name','timestamp'}}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


@contextmanager
def _source(db_path):
    conn = None
    try:
        path = Path(db_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ContractError('SOURCE_UNAVAILABLE')
        conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        for table, required in REQUIRED.items():
            columns = {r['name'] for r in conn.execute('PRAGMA table_info('+table+')')}
            if not required <= columns:
                raise ContractError('UNKNOWN_SOURCE_SCHEMA')
        yield path, conn
    except (OSError, sqlite3.Error, ValueError, TypeError):
        raise ContractError('SOURCE_UNAVAILABLE') from None
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()


def list_sessions(db_path: Path, *, limit: int = 20) -> list[dict]:
    """Safe metadata only: no title, prompt, cwd or user identity."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ContractError('INVALID_LIMIT')
    with _source(db_path) as (_, conn):
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(sessions)')}
        fields = 'id,started_at' + (',profile_name' if 'profile_name' in columns else '')
        return [dict(r) for r in conn.execute(
            'SELECT '+fields+' FROM sessions ORDER BY started_at DESC,id LIMIT ?', (limit,))]


def safe_public_url(value: str) -> str | None:
    """Conservative touched-URL candidates; C must additionally validate DNS/redirects.

    Query/fragment URLs are omitted, not rewritten into a URL never actually touched.
    No DNS/network work is performed during capture or before consent.
    """
    if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s\\\x00-\x1f\x7f]', value):
        return None
    try:
        u = urlsplit(value)
        host = u.hostname or ''
        if (u.scheme not in ('http','https') or not host or u.username is not None or
                u.password is not None or u.query or u.fragment or u.port not in (None,80,443)):
            return None
        host = host.lower()
        if ('.' not in host or host.endswith(('.local','.internal','.localhost','.lan','.home','.test','.invalid','.'))
                or host == 'localhost' or '%' in host):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            # Reject alternate numeric forms, encoded hosts and non-DNS names.
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or re.fullmatch(r'[0-9.]+',host):
                return None
        return value
    except ValueError:
        return None


def _status(content):
    try:
        data = json.loads(content or '{}')
    except (ValueError, TypeError):
        return 'unknown'
    if not isinstance(data, dict):
        return 'unknown'
    if isinstance(data.get('result'), dict):
        data = data['result']
    code = data.get('exit_code')
    if type(code) is int:
        return 'ok' if code == 0 else 'error'
    if data.get('error') or data.get('success') is False or data.get('ok') is False or data.get('status') == 'error':
        return 'error'
    if data.get('success') is True or data.get('ok') is True or data.get('verified') is True:
        return 'ok'
    return 'unknown'


def redact_event(row: dict) -> Event:
    tool = row.get('tool') if row.get('tool') in TOOLS else 'other'
    kind = row.get('kind') if row.get('kind') in ('tool','user','assistant') else 'message'
    status = row.get('status') if row.get('status') in ('ok','error','unknown') else 'unknown'
    sid, rid = str(row['session_id']), int(row['row_id'])
    ordinal = int(row.get('ordinal',0))
    event_id = hashlib.sha256(f'{sid}:{rid}:{ordinal}'.encode()).hexdigest()[:24]
    label = f'{tool}: {status}' if kind == 'tool' else f'{kind} message'
    return Event(event_id, sid, rid, max(0,int(row['t_ms'])), kind,
                 tool if kind == 'tool' else '', status, label, None)


def read_session(db_path: Path, session_ids: tuple[str,...], *, max_events: int = 2000) -> SessionSnapshot:
    if (not isinstance(session_ids,tuple) or not 1 <= len(session_ids) <= 3 or
            any(not isinstance(s,str) or not s or len(s)>256 for s in session_ids) or
            len(set(session_ids)) != len(session_ids)):
        raise ContractError('EXPLICIT_SESSION_IDS_REQUIRED')
    if type(max_events) is not int or not 1 <= max_events <= 10000:
        raise ContractError('INVALID_EVENT_LIMIT')
    with _source(db_path) as (path, conn):
        marks = ','.join('?' for _ in session_ids)
        selected = [dict(r) for r in conn.execute(
            'SELECT id,started_at FROM sessions WHERE id IN ('+marks+') ORDER BY id',session_ids)]
        if len(selected) != len(session_ids):
            raise ContractError('SESSION_NOT_FOUND')
        # Cap rows and individual payloads before materializing untrusted JSON.
        count, largest = conn.execute('SELECT count(*),max(length(coalesce(content,\'\')) + '
            'length(coalesce(tool_calls,\'\'))) FROM messages WHERE session_id IN ('+marks+')',session_ids).fetchone()
        if count > max_events*4 or (largest or 0) > 4_000_000:
            raise ContractError('SOURCE_LIMIT_EXCEEDED')
        rows = [dict(r) for r in conn.execute('SELECT id,session_id,role,content,tool_call_id,'
            'tool_calls,tool_name,timestamp FROM messages WHERE session_id IN ('+marks+') '
            'ORDER BY timestamp,id',session_ids)]
        if not rows:
            raise ContractError('EMPTY_SESSION')
        if any(type(r['timestamp']) not in (int,float) or not math.isfinite(r['timestamp']) for r in rows):
            raise ContractError('INVALID_SOURCE_TIMESTAMP')
        # Fingerprint selected source fields, not changing SQLite/WAL bytes or other sessions.
        fingerprint = hashlib.sha256(_canonical({'sessions':selected,'messages':rows}).encode()).hexdigest()
        results = {}
        for row in rows:
            if row['role'] == 'tool' and row['tool_call_id']:
                key = (row['session_id'],row['tool_call_id'])
                if key in results:
                    raise ContractError('AMBIGUOUS_TOOL_RESULT')
                results[key] = row
        try:
            from .narrative import capture_excerpt
        except ImportError:
            from narrative import capture_excerpt
        events, urls, seen_calls, excerpts = [], set(), set(), []
        def add_excerpt(event, text, kind):
            item = capture_excerpt(event,text,kind)
            if item['text'] and len(excerpts)<500:
                excerpts.append(item)
        origin = min(r['timestamp'] for r in rows)
        for row in rows:
            base = dict(session_id=row['session_id'],row_id=row['id'],t_ms=int((row['timestamp']-origin)*1000))
            if row['role'] == 'assistant' and row['tool_calls']:
                try:
                    calls = json.loads(row['tool_calls'])
                    if not isinstance(calls,list) or not calls:
                        raise ValueError()
                    for i, call in enumerate(calls):
                        if not isinstance(call,dict) or not isinstance(call.get('function'),dict):
                            raise ValueError()
                        cid = call.get('id') or call.get('call_id')
                        if not isinstance(cid,str) or not cid or (row['session_id'],cid) in seen_calls:
                            raise ValueError()
                        seen_calls.add((row['session_id'],cid))
                        fn = call['function']
                        result = results.get((row['session_id'],cid))
                        events.append(redact_event(dict(base,kind='tool',tool=fn.get('name'),ordinal=i,
                            status=_status(result['content']) if result else 'unknown')))
                        args = fn.get('arguments') or {}
                        args = json.loads(args) if isinstance(args,str) else args
                        if isinstance(args,dict):
                            if fn.get('name')=='write_file' and isinstance(args.get('content'),str):
                                add_excerpt(events[-1],args['content'],'file_input')
                            elif fn.get('name')=='patch':
                                add_excerpt(events[-1],args.get('new_string') or args.get('patch') or '', 'patch_input')
                            elif fn.get('name')=='skill_manage' and isinstance(args.get('operations'),list):
                                changes=[]
                                for operation in args['operations'][:12]:
                                    if isinstance(operation,dict) and isinstance(operation.get('new_string'),str):
                                        changes.append(operation['new_string'])
                                if changes: add_excerpt(events[-1],'\n\n'.join(changes),'patch_input')
                            elif fn.get('name')=='terminal':
                                add_excerpt(events[-1],args.get('command',''),'terminal_input')
                        if result and isinstance(result['content'],str):
                            add_excerpt(events[-1],result['content'],'tool_output')
                        if fn.get('name') in ('web_extract','browser_navigate'):
                            args = fn.get('arguments') or {}
                            args = json.loads(args) if isinstance(args,str) else args
                            if isinstance(args,dict):
                                candidates = args.get('urls',[]) if fn['name']=='web_extract' else [args.get('url')]
                                if isinstance(candidates,list):
                                    urls.update(u for v in candidates if (u := safe_public_url(v)))
                except (ValueError, TypeError, KeyError):
                    raise ContractError('UNKNOWN_TOOL_CALL_SCHEMA') from None
            elif row['role'] in ('user','assistant'):
                events.append(redact_event(dict(base,kind=row['role'],status='unknown')))
                if row['role']=='user': add_excerpt(events[-1],row['content'],'ask')
            if len(events) > max_events:
                raise ContractError('EVENT_LIMIT_EXCEEDED')
        if not events:
            raise ContractError('EMPTY_EVIDENCE')
        summary = _canonical({'excerpts':excerpts})
        return SessionSnapshot(2,uuid.uuid4().hex,SourceRef(path,session_ids),datetime.now(timezone.utc).isoformat(),
                               fingerprint,tuple(events),summary,tuple(sorted(urls)))
