"""Noninteractive entrypoint: never solicits approvals in cron/CI."""
from __future__ import annotations
import json
import shutil


def setup_cli(parser):
    parser.add_argument('action',choices=('interview','check','render'),nargs='?',default='check')


def run_cli(args):
    if args.action != 'check':
        print(json.dumps({'code':'NEEDS_INPUT','exit_code':2,'reason':'Use /demo in an interactive Hermes chat; CLI cannot attest chat owner or approval.'}))
        return 2
    capabilities = {name:bool(shutil.which(name)) for name in ('ffmpeg','ffprobe')}
    print(json.dumps({'code':'OK' if all(capabilities.values()) else 'BLOCKED_CAPABILITY',
                      'capabilities':capabilities,'providers':'not called','live_release_verified':False}))
    return 0 if all(capabilities.values()) else 3

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    setup_cli(parser)
    raise SystemExit(run_cli(parser.parse_args()))
