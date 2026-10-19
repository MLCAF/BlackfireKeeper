"""Durable, per-failure reports; no process dumps, account dictionaries or locals."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
from uuid import uuid4
from .version import version_info


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


def create_bundle(diagnostic_root):
    root = Path(diagnostic_root)
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        folder = root / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + uuid4().hex[:8])
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise FileExistsError('无法创建独立诊断目录')


def write_bundle_report(folder, report):
    folder = Path(folder)
    target = folder/'report.json'
    temporary = folder/'report.tmp'
    temporary.write_text(json.dumps(scrub(report), ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    # Each writer owns its temporary pointer; concurrent diagnostics cannot
    # truncate latest.txt or point at a report that is still being written.
    pointer = folder/'latest.tmp'
    pointer.write_text(str(target), encoding='utf-8')
    pointer.replace(folder.parent/'latest.txt')
    return target


def save_failure(root, run_id, stage, error, snapshot=None, context=None,
                 recent=(), image=None, image_at=None):
    root = Path(root)
    context = dict(context or {})
    if hasattr(error, 'window_geometry'):
        context['window_geometry'] = error.window_geometry
    folder = create_bundle(root/'logs/diagnostics')
    report = dict(version=1, application=version_info(), run_id=run_id, time=datetime.now().isoformat(),
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
    return write_bundle_report(folder, report)
