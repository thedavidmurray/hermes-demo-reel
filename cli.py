"""Noninteractive entrypoint: never solicits approvals in cron/CI."""
from __future__ import annotations
import json
import shutil


def setup_cli(parser):
    parser.add_argument('action',choices=('interview','check','draft','render'),nargs='?',default='check')
    parser.add_argument('--answers',type=str)
    parser.add_argument('--db',type=str)
    parser.add_argument('--session-id',action='append',default=[])
    parser.add_argument('--operator',type=str)
    parser.add_argument('--consent-source',action='store_true')
    parser.add_argument('--job',type=str)
    parser.add_argument('--consent-generation',type=str)
    parser.add_argument('--approve-board',type=str)
    parser.add_argument('--approve-disclosure',type=str)


def run_cli(args, *, ctx=None):
    if args.action != 'check':
        try:
            from .operator_cli import run_operator
        except ImportError:
            from operator_cli import run_operator
        return run_operator(args,ctx=ctx)
    capabilities = {name:bool(shutil.which(name)) for name in ('ffmpeg','ffprobe')}
    if ctx is not None:
        try:
            from .media_tools import resolve_media_tools
        except ImportError:
            from media_tools import resolve_media_tools
        try:
            resolve_media_tools(ctx)
        except Exception as exc:
            import re
            code = str(exc)
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code):
                code = 'REDACTED_ERROR'
            print(json.dumps({'code':'BLOCKED_CAPABILITY','error_type':type(exc).__name__,
                              'error_code':code,'providers':'not called'}))
            return 3
    print(json.dumps({'code':'OK' if all(capabilities.values()) else 'BLOCKED_CAPABILITY',
                      'capabilities':capabilities,'providers':'not called','live_release_verified':False}))
    return 0 if all(capabilities.values()) else 3

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    setup_cli(parser)
    raise SystemExit(run_cli(parser.parse_args()))
