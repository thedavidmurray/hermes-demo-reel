"""Probe an actual MP4. No fabricated/provider-mocked data is accepted by the CLI."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess


def validate_probe(probe: dict) -> dict:
    duration = float(probe['format']['duration'])
    streams = probe['streams']
    if not math.isfinite(duration) or not 0 < duration <= 90.0:
        raise ValueError('DURATION_MUST_BE_AT_MOST_90_SECONDS')
    if not all(any(s.get('codec_type') == kind and s.get('codec_name') == codec for s in streams)
               for kind,codec in (('video','h264'),('audio','aac'))):
        raise ValueError('H264_VIDEO_AND_AAC_AUDIO_REQUIRED')
    return dict(duration_s=duration,video=True,audio=True)


def validate_volume(stderr: str) -> float:
    import re
    match = re.search(r'mean_volume:\s*(-?(?:\d+(?:\.\d+)?|inf)) dB',stderr)
    level = float(match[1]) if match else float('-inf')
    if not math.isfinite(level) or level < -60:
        raise ValueError('AUDIO_EMPTY_OR_TOO_QUIET')
    return level


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mp4',type=Path,required=True)
    args = parser.parse_args()
    try:
        path = args.mp4.resolve(strict=True)
        if path.suffix.lower() != '.mp4':
            raise ValueError('MP4_REQUIRED')
        output = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],
                                capture_output=True,text=True,timeout=30,check=True)
        result = validate_probe(json.loads(output.stdout))
        subprocess.run(['ffmpeg','-v','error','-xerror','-err_detect','explode','-i',str(path),'-f','null','-'],
                       capture_output=True,timeout=180,check=True)
        audio = subprocess.run(['ffmpeg','-hide_banner','-i',str(path),'-vn','-af','volumedetect','-f','null','-'],
                               capture_output=True,text=True,timeout=90,check=True)
        result['mean_volume_db'] = validate_volume(audio.stderr)
        print(json.dumps(dict(code='PASS',full_decode=True,
                              scope='media sanity only; does not prove narration, provenance, redaction or live delivery',**result)))
        return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        print(json.dumps(dict(code='FAIL',reason='Actual MP4 probe/decode failed; video, audio and strict <=90s required.')))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
