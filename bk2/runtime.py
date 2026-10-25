import hashlib
import os
import json
from pathlib import Path
import threading
import time
from collections import deque
from datetime import datetime
from .memory import ProcessReader, find_game_pid, MemoryReadError
from .state import StateReader, ProgressLedger, require_fresh, bind_when_ready

from .paths import application_root

ROOT = application_root()
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
        self.memory_recovery_until = 0.
        self.automation_active = False
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
                    guard = getattr(self.runner, 'action_guard', {})
                    context['action_guard'] = dict(guard) if isinstance(guard, dict) else {}
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
        reader = None
        try:
            self.publish('log', '核验游戏版本，定位玩家字典；首次连接通常需要十几秒。')
            verify_build()
            with ProcessReader(find_game_pid(), EXE) as memory:
                reader = StateReader(memory)
                current = bind_when_ready(reader, self.stop_event, self.publish)
                self.diagnostic_context['player_binding'] = dict(reader.binding_diagnostics)
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
                    try:
                        current = reader.snapshot()
                    except MemoryReadError:
                        passive = ((self.runner is None and not self.automation_active)
                                   or self.operation_stop.is_set())
                        current = self.rebind_after_disconnect(reader, passive=passive)
        except Exception as exc:
            if isinstance(exc, InterruptedError) and (self.stop_event.is_set() or self.operation_stop.is_set()):
                # Cancellation during a bounded bind is not a memory fault.
                # Keep the snapshot unavailable: never reuse pre-disconnect data.
                self.snapshot = {'status': 'unknown', 'error': '连接已取消'}
                self.publish('memory_cancelled', '内存连接/重连已取消；再次挂机前请重新连接')
                return
            if reader is not None:
                self.diagnostic_context['player_binding'] = dict(reader.binding_diagnostics)
            self.error = str(exc)
            self.snapshot = {'status': 'unknown', 'error': self.error}
            self.publish('memory_error', self.error)
            self.stop_automation('内存状态失效')
        finally:
            self.ready.set()

    def rebind_after_disconnect(self, reader, passive=False):
        deadline = time.monotonic()+60
        # Idle rebind is read-only: it must not enable gameplay recovery clicks
        # or clear the user's Stop flag.
        self.memory_recovery_until = 0. if passive else deadline
        self.snapshot = {'status':'unknown','error':'断线恢复中，暂停普通操作'}
        self.publish('log', '玩家字典暂时失效：60 秒内只读重新绑定，不恢复挂机或点击游戏' if passive
                     else '玩家字典暂时失效：60 秒内尝试重新绑定；仅允许已识别的登录恢复按钮')
        session = self
        class RecoveryStop:
            def is_set(self):
                return (session.stop_event.is_set() or (not passive and session.operation_stop.is_set())
                        or time.monotonic() >= deadline)
        try:
            while time.monotonic() < deadline:
                if self.stop_event.is_set() or (not passive and self.operation_stop.is_set()):
                    raise InterruptedError('内存重连已停止')
                try:
                    current = reader.bind(RecoveryStop())
                    if time.monotonic() >= deadline:
                        raise MemoryReadError('内存重新绑定超时')
                    require_fresh(current,current['pid'])
                    if self.last_valid_snapshot and current['pid'] != self.last_valid_snapshot['pid']:
                        raise MemoryReadError('重连进程身份不一致')
                    if (self.last_valid_snapshot and current.get('process_creation')
                            != self.last_valid_snapshot.get('process_creation')):
                        raise MemoryReadError('重连进程创建时间不一致')
                    self.snapshot = current
                    self.publish('log', '玩家字典已只读重新绑定；挂机保持原停止状态' if passive
                                 else '玩家字典已重新绑定，恢复普通状态核验')
                    return current
                except MemoryReadError:
                    cancel = self.stop_event if passive else self.operation_stop
                    if cancel.wait(.5): raise InterruptedError('内存重连已停止')
            raise MemoryReadError('60 秒内无法重新绑定唯一玩家字典')
        finally:
            self.memory_recovery_until = 0.

    def wait_ready(self, seconds=150):
        if not self.ready.wait(seconds):
            self.stop()
            raise TimeoutError('连接超时')
        if self.error:
            raise MemoryReadError(self.error)
        if self.stop_event.is_set():
            raise InterruptedError('连接已取消')
        require_fresh(self.snapshot, (self.snapshot or {}).get('pid'))

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
