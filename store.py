"""Profile-local durable state. No live source reads and no fallback global home.

JSON is canonical and typed on read; CAS is separate from board revision. Host
plugin_db supplies the connection. Tests inject that boundary, not a live profile.
"""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import types
from typing import get_args, get_origin, get_type_hints, Literal, Union

try:
    from . import schemas
    from .schemas import ContractError, Conflict, Job, Owner, SessionSnapshot
except ImportError:
    import schemas
    from schemas import ContractError, Conflict, Job, Owner, SessionSnapshot

STATES = frozenset(('NEW','AWAITING_INPUT','AWAITING_GENERATION_CONSENT','STORYBOARD_REVIEW',
    'APPROVED','RENDERING','READY','DELIVERED','CANCELLED','WAITING_INPUT_EXPIRED',
    'BLOCKED_APPROVAL','BLOCKED_CAPABILITY','FAILED'))
TRANSITIONS = {
    'NEW': {'AWAITING_INPUT','CANCELLED'},
    'AWAITING_INPUT': {'AWAITING_INPUT','AWAITING_GENERATION_CONSENT'},
    'AWAITING_GENERATION_CONSENT': {'AWAITING_GENERATION_CONSENT','STORYBOARD_REVIEW'},
    'STORYBOARD_REVIEW': {'STORYBOARD_REVIEW','APPROVED','AWAITING_GENERATION_CONSENT'},
    'APPROVED': {'APPROVED','STORYBOARD_REVIEW','RENDERING','AWAITING_GENERATION_CONSENT'},
    'RENDERING': {'READY','FAILED'}, 'READY': {'DELIVERED'}, 'DELIVERED': set(),
    'CANCELLED': set(), 'WAITING_INPUT_EXPIRED': {'AWAITING_INPUT','AWAITING_GENERATION_CONSENT'},
    'BLOCKED_APPROVAL': {'AWAITING_INPUT','AWAITING_GENERATION_CONSENT'},
    'BLOCKED_CAPABILITY': {'AWAITING_INPUT','AWAITING_GENERATION_CONSENT'},
    'FAILED': {'AWAITING_INPUT','AWAITING_GENERATION_CONSENT','APPROVED'},
}


def _plain(value):
    if is_dataclass(value):
        return {f.name:_plain(getattr(value,f.name)) for f in fields(value)}
    if isinstance(value,Path): return str(value.expanduser().resolve())
    if isinstance(value,(tuple,list)): return [_plain(v) for v in value]
    if isinstance(value,dict): return {k:_plain(v) for k,v in value.items()}
    return value


def canonical(value) -> str:
    return json.dumps(_plain(value),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256((value if isinstance(value,str) else canonical(value)).encode('utf-8')).hexdigest()


def _decode(kind, value):
    origin, args = get_origin(kind), get_args(kind)
    if origin in (types.UnionType, Union):
        for choice in args:
            try: return _decode(choice,value)
            except ContractError: pass
        raise ContractError('INVALID_STORED_TYPE')
    if kind is type(None):
        if value is not None: raise ContractError('INVALID_STORED_TYPE')
        return None
    if origin is Literal:
        if value not in args: raise ContractError('INVALID_STORED_LITERAL')
        return value
    if origin is tuple:
        if not isinstance(value,list): raise ContractError('INVALID_STORED_TYPE')
        return tuple(_decode(args[0],v) for v in value)
    if kind is Path:
        if not isinstance(value,str) or not Path(value).is_absolute(): raise ContractError('INVALID_STORED_PATH')
        return Path(value)
    if isinstance(kind,type) and is_dataclass(kind):
        hints = get_type_hints(kind, vars(schemas))
        if not isinstance(value,dict) or set(value) != set(hints): raise ContractError('UNKNOWN_STORED_SCHEMA')
        return kind(**{k:_decode(t,value[k]) for k,t in hints.items()})
    if type(value) is not kind: raise ContractError('INVALID_STORED_TYPE')
    return value


def decode(kind, payload):
    try: return _decode(kind,json.loads(payload))
    except (ValueError,TypeError,KeyError): raise ContractError('INVALID_STORED_PAYLOAD') from None


def validate_owner(owner):
    if not isinstance(owner,Owner) or any(not isinstance(v,str) or not v.strip() or len(v)>512 for v in asdict(owner).values()):
        raise ContractError('TRUSTED_OWNER_REQUIRED')


def _key(owner):
    validate_owner(owner)
    return digest(owner)


def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):
        raise ContractError('INVALID_ID')
    return value


def open_store() -> sqlite3.Connection:
    from plugins.plugin_storage import plugin_db
    conn = plugin_db('demo-reel')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=10000')
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0,1):
        conn.close()
        raise ContractError('UNKNOWN_STORE_SCHEMA')
    conn.executescript('''CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY, owner_key TEXT NOT NULL, job_version INTEGER NOT NULL,
        state TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner_key);
        CREATE TABLE IF NOT EXISTS snapshots (
        snapshot_id TEXT PRIMARY KEY, owner_key TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS render_leases (
        job_id TEXT NOT NULL, board_digest TEXT NOT NULL, state TEXT NOT NULL,
        expires_at TEXT NOT NULL, PRIMARY KEY(job_id,board_digest));
        PRAGMA user_version=1;''')
    return conn


def _job_row(owner,job_id,conn):
    row = conn.execute('SELECT payload,payload_sha256,job_version,state FROM jobs '
        'WHERE job_id=? AND owner_key=?',(_id(job_id),_key(owner))).fetchone()
    if row is None: raise ContractError('JOB_NOT_FOUND')
    job = decode(Job,row[0])
    if digest(job) != row[1] or job.job_version != row[2] or job.state != row[3] or job.owner != owner or job.job_id != job_id:
        raise ContractError('CORRUPT_JOB')
    _validate_job(job)
    return job


def load_job(owner: Owner, job_id: str) -> Job:
    conn = open_store()
    try: return _job_row(owner,job_id,conn)
    finally: conn.close()


def active_job(owner: Owner) -> Job:
    """Conversation-local interview continuation; never chooses source sessions."""
    conn = open_store()
    try:
        rows = conn.execute("SELECT job_id FROM jobs WHERE owner_key=? AND state NOT IN "
            "('CANCELLED','DELIVERED','FAILED') ORDER BY rowid DESC LIMIT 2",(_key(owner),)).fetchall()
        if not rows: raise ContractError('NO_ACTIVE_JOB')
        if len(rows) != 1: raise ContractError('EXPLICIT_JOB_REQUIRED')
        return _job_row(owner,rows[0][0],conn)
    finally: conn.close()


def _validate_job(job):
    validate_owner(job.owner)
    _id(job.job_id)
    if job.schema_version != 1 or job.state not in STATES or type(job.job_version) is not int or job.job_version < 0:
        raise ContractError('INVALID_JOB_SCHEMA')
    # Roundtrip enforces every wire type even for direct dataclass constructors.
    decode(Job,canonical(job))
    if job.consent and (job.consent.owner != job.owner or job.consent.evidence_snapshot_id != job.evidence_snapshot_id):
        raise ContractError('CONSENT_MISMATCH')
    if job.board and (job.board.job_id != job.job_id or job.board.options != job.options or
                      job.board.evidence_snapshot_id != job.evidence_snapshot_id):
        raise ContractError('BOARD_MISMATCH')
    if job.approval:
        if (not job.board or job.approval.owner != job.owner or job.approval.storyboard_digest != job.board.digest or
                job.approval.board_revision != job.board.board_revision):
            raise ContractError('APPROVAL_MISMATCH')
    if job.state in ('APPROVED','RENDERING','READY','DELIVERED') and not job.approval:
        raise ContractError('APPROVAL_REQUIRED')


def save_job(job: Job, *, expected_job_version: int) -> Job:
    _validate_job(job)
    if type(expected_job_version) is not int or expected_job_version < 0 or job.job_version != expected_job_version:
        raise Conflict('JOB_VERSION_CONFLICT')
    if job.state == 'STORYBOARD_REVIEW' and job.board is None:
        job = replace(job,error_code='GENERATION_UNKNOWN')
    elif job.board is not None and job.error_code == 'GENERATION_UNKNOWN':
        job = replace(job,error_code=None)
    saved = replace(job,job_version=expected_job_version+1)
    conn = open_store()
    try:
        conn.execute('BEGIN IMMEDIATE')
        exists = conn.execute('SELECT owner_key FROM jobs WHERE job_id=?',(job.job_id,)).fetchone()
        if exists:
            if exists[0] != _key(job.owner): raise ContractError('OWNER_MISMATCH')
            old = _job_row(job.owner,job.job_id,conn)
            if old.job_version != expected_job_version: raise Conflict('JOB_VERSION_CONFLICT')
            allowed = TRANSITIONS[old.state] | {'CANCELLED','WAITING_INPUT_EXPIRED','BLOCKED_APPROVAL','BLOCKED_CAPABILITY','FAILED'}
            if old.state in ('CANCELLED','DELIVERED'): allowed = set()
            if job.state not in allowed: raise ContractError('INVALID_STATE_TRANSITION')
            if old.evidence_snapshot_id and job.evidence_snapshot_id != old.evidence_snapshot_id:
                raise ContractError('IMMUTABLE_EVIDENCE')
            if old.error_code == 'GENERATION_UNKNOWN' and job.state != 'CANCELLED':
                if job.board is None and (job.error_code != 'GENERATION_UNKNOWN' or
                        job.state not in ('STORYBOARD_REVIEW','BLOCKED_APPROVAL')):
                    raise ContractError('GENERATION_UNCERTAIN_CANCEL_REQUIRED')
            if old.board != job.board and job.approval:
                raise ContractError('EDIT_REVOKES_APPROVAL')
            conn.execute('UPDATE jobs SET job_version=?,state=?,payload=?,payload_sha256=? WHERE job_id=? AND job_version=?',
                (saved.job_version,saved.state,canonical(saved),digest(saved),saved.job_id,expected_job_version))
        else:
            if expected_job_version != 0: raise Conflict('JOB_VERSION_CONFLICT')
            if job.state not in ('NEW','AWAITING_INPUT'): raise ContractError('INVALID_INITIAL_STATE')
            conn.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                (saved.job_id,_key(saved.owner),saved.job_version,saved.state,canonical(saved),digest(saved)))
        conn.commit()
        return saved
    except Exception:
        conn.rollback()
        raise
    finally: conn.close()


def persist_snapshot(owner: Owner, snapshot: SessionSnapshot) -> str:
    key = _key(owner)
    _id(snapshot.snapshot_id)
    if snapshot.schema_version != 1 or not re.fullmatch('[0-9a-f]{64}',snapshot.source_sha256):
        raise ContractError('INVALID_EVIDENCE_SCHEMA')
    decode(SessionSnapshot,canonical(snapshot))
    conn = open_store()
    try:
        with conn:
            row = conn.execute('SELECT owner_key,payload_sha256 FROM snapshots WHERE snapshot_id=?',(snapshot.snapshot_id,)).fetchone()
            if row and row != (key,digest(snapshot)): raise ContractError('IMMUTABLE_EVIDENCE')
            if not row:
                conn.execute('INSERT INTO snapshots VALUES(?,?,?,?)',(snapshot.snapshot_id,key,canonical(snapshot),digest(snapshot)))
        return snapshot.snapshot_id
    finally: conn.close()


def load_snapshot(owner: Owner, snapshot_id: str) -> SessionSnapshot:
    conn = open_store()
    try:
        row = conn.execute('SELECT payload,payload_sha256 FROM snapshots WHERE snapshot_id=? AND owner_key=?',
            (_id(snapshot_id),_key(owner))).fetchone()
        if not row: raise ContractError('EVIDENCE_NOT_FOUND')
        snap = decode(SessionSnapshot,row[0])
        if snap.schema_version != 1 or snap.snapshot_id != snapshot_id or digest(snap) != row[1]:
            raise ContractError('CORRUPT_EVIDENCE')
        return snap
    finally: conn.close()
