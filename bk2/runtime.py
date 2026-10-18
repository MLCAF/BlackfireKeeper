import hashlib
import os
import json
from pathlib import Path
import threading
import time
from collections import deque
from datetime import datetime
from .memory import ProcessReader, find_game_pid, MemoryReadError
from .state import StateReader, ProgressLedger, require_fresh

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get('BLACKFIRE_EXE') or (ROOT.parent.parent / 'Blackfire Crusade.exe')).expanduser().resolve()
SUPPORTED_SHA256 = '06efee8f6cfb4953cc842404b2518b01b3536353c03115687475e0423ea7e7a9'


def verify_build():
    with EXE.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    if digest != SUPPORTED_SHA256:
        raise RuntimeError('游戏版本已变化，内存布局需要重新验证；未启用旧版读取')
    return digest


class Session:
    def __init__(self, emit=lambda kind, data: None):
        self.emit = emit
        self.snapshot = None
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.thread = None
        self.runner = None
        self.operation_stop = threading.Event()
        self.manual_ready = threading.Event()
        self.waiting_for_diamonds = False
        self.ledger = ProgressLedger()
        self.error = None
        self.last_valid_snapshot = None
        self.last_image = None
        self.last_image_at = None
        self.diagnostic_context = {}
        self.recent_events = deque(maxlen=150)
        self.diagnostic_lock = threading.Lock()
        self.last_diagnostic = None
        self.logging_error_reported = False
        self.log_lock = threading.Lock()
        self.run_id = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.log_path = ROOT / 'logs' / f'{self.run_id}.jsonl'
        self.log_path.parent.mkdir(exist_ok=True)

    def publish(self, kind, data):
        if kind == 'image':
            self.last_image, self.last_image_at = data, time.monotonic()
        if kind not in ('image', 'stats'):
            record = {'time': datetime.now().isoformat(), 'kind': kind, 'data': data}
            with self.log_lock:
                self.recent_events.append(record)
                try:
                    with self.log_path.open('a', encoding='utf-8') as f:
                        f.write(json.dumps(record, ensure_ascii=False) + '\n')
                except OSError as exc:
                    self.operation_stop.set()
                    if self.runner:
                        self.runner.reason = '日志写入失败'
                        self.runner.stop_event.set()
                    if not self.logging_error_reported:
                        self.logging_error_reported = True
                        import sys
                        warning = f'日志写入失败，已停止自动操作：{exc}'
                        print(warning, file=sys.stderr, flush=True)
                        self.emit('diagnostic_save_error', warning)
        if kind in ('error', 'memory_error'):
            self.save_diagnostic(kind, data)
        self.emit(kind, data)

    def save_diagnostic(self, stage, error):
        from .diagnostics import save_failure
        try:
            with self.diagnostic_lock:
                with self.log_lock:
                    recent = list(self.recent_events)
                context = dict(self.diagnostic_context)
                if self.runner:
                    context['runner'] = dict(state=self.runner.stats.state,
                        reason=self.runner.reason, history=list(self.runner.history),
                        counters=dict(self.runner.counters),
                        pending_action=self.runner.pending_action[1]['name'] if self.runner.pending_action else None)
                self.last_diagnostic = save_failure(ROOT, self.run_id, stage, error,
                    self.last_valid_snapshot or self.snapshot, context,
                    recent, self.last_image, self.last_image_at)
            self.publish('diagnostic_saved', str(self.last_diagnostic))
            return self.last_diagnostic
        except Exception as exc:
            # Never replace the original failure with a diagnostics failure.
            import sys
            text = f'诊断保存失败：{exc}；原错误：{error}'
            print(text, file=sys.stderr, flush=True)
            self.emit('diagnostic_save_error', text)
            return None

    def start(self):
        if self.thread:
            raise RuntimeError('会话已启动')
        self.thread = threading.Thread(target=self._observe, daemon=True)
        self.thread.start()

    def _observe(self):
        try:
            self.publish('log', '核验游戏版本，定位玩家字典；首次连接通常需要十几秒。')
            verify_build()
            with ProcessReader(find_game_pid(), EXE) as memory:
                reader = StateReader(memory)
                current = reader.bind(self.stop_event)
                previous_data = None
                while not self.stop_event.is_set():
                    self.snapshot = current
                    self.last_valid_snapshot = current
                    event = self.ledger.observe(current)
                    if event:
                        self.publish('memory_event', event)
                    # Persist changes only; always refresh the GUI and timestamp.
                    if current['data'] != previous_data:
                        self.publish('memory_change', current)
                        previous_data = current['data']
                    else:
                        self.emit('memory', current)
                    self.ready.set()
                    if self.stop_event.wait(0.5):
                        break
                    current = reader.snapshot()
        except Exception as exc:
            self.error = str(exc)
            self.snapshot = {'status': 'unknown', 'error': self.error}
            self.publish('memory_error', self.error)
            self.stop_automation('内存状态失效')
        finally:
            self.ready.set()

    def wait_ready(self, seconds=150):
        if not self.ready.wait(seconds):
            self.stop()
            raise TimeoutError('连接超时')
        if self.error:
            raise MemoryReadError(self.error)
        if self.stop_event.is_set():
            raise InterruptedError('连接已取消')
        require_fresh(self.snapshot, self.snapshot['pid'])

    def stop(self):
        self.stop_event.set()
        self.stop_automation('用户停止')

    def stop_automation(self, reason='用户停止'):
        self.operation_stop.set()
        if self.runner:
            self.runner.stop(reason)

    def acknowledge_diamonds(self):
        if not self.waiting_for_diamonds or self.operation_stop.is_set():
            return False
        self.manual_ready.set()
        return True
