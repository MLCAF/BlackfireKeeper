"""Durable, per-failure reports; no process dumps, account dictionaries or locals."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
from uuid import uuid4


def scrub(value):
    if isinstance(value, dict):
        return {str(k): ('[redacted]' if any(word in str(k).lower()
                for word in ('token', 'secret', 'password', 'account', 'user_id')) else scrub(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def save_failure(root, run_id, stage, error, snapshot=None, context=None,
                 recent=(), image=None, image_at=None):
    root = Path(root)
    folder = root / 'logs/diagnostics' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + uuid4().hex[:6])
    folder.mkdir(parents=True)
    report = dict(version=1, run_id=run_id, time=datetime.now().isoformat(),
                  stage=stage, error=str(error), traceback=None,
                  last_valid_memory=snapshot, context=context or {}, recent_events=list(recent),
                  event_log=str(root/'logs'/f'{run_id}.jsonl'), screenshot=None,
                  screenshot_age_seconds=None, files={})
    if sys.exc_info()[0] is not None:
        report['traceback'] = traceback.format_exc()
    for path in sorted(list((root/'bk2').glob('*.py')) + list((root/'vendor_core').glob('*.py'))
                       + list((root/'config').rglob('*.json'))):
        try:
            report['files'][str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            report['files'][str(path.relative_to(root))] = {'error': str(exc)}
    if image is not None:
        try:
            from PIL import Image
            Image.fromarray(image[:, :, :3][:, :, ::-1]).save(folder/'screen.png')
            report['screenshot'] = 'screen.png'
            report['screenshot_age_seconds'] = time.monotonic() - image_at if image_at else None
        except Exception as exc:
            report['screenshot_error'] = str(exc)
    else:
        report['screenshot_unavailable'] = '当前会话尚未收到截图；诊断不会额外点击或截图'
    temp = folder/'report.tmp'
    temp.write_text(json.dumps(scrub(report), ensure_ascii=False, indent=2), encoding='utf-8')
    target = folder/'report.json'
    temp.replace(target)
    latest = root/'logs/diagnostics/latest.txt'
    latest.write_text(str(target), encoding='utf-8')
    return target
