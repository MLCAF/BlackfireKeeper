"""Diamond-only evidence and typed schedule result; never accepts shop clicks as success."""
from dataclasses import dataclass
from .cooldown import DeferredCooldown, confirm_countdown, parse_timer


@dataclass(frozen=True)
class DiamondResult:
    cooldown: DeferredCooldown
    collected: bool

    def valid_at(self, now):
        return (isinstance(self.collected, bool) and isinstance(self.cooldown, DeferredCooldown)
                and self.cooldown.valid_at(now)
                and 0 <= parse_timer(self.cooldown.text) <= 7200
                and self.cooldown.ready_at - now <= 7203)


def read_free_pack_timer(runner, rule):
    """Two fresh, visible free-pack anchors; OCR region follows that pack, never paid items."""
    runner.cooldown_estimate = None
    samples=[]
    boxes=[]
    for index in range(2):
        if runner.stop_event.is_set(): raise InterruptedError('钻石冷却识别已停止')
        if runner.pause_event.is_set(): return False
        if index and runner.stop_event.wait(.3): raise InterruptedError('钻石冷却识别已停止')
        frame=runner.backend.capture(runner.stop_event)
        captured=runner.clock()
        runner.emit('image',frame)
        anchor=runner.backend.recognize(frame,rule,runner.stop_event)
        if anchor is None: return False
        x,y,w,h=anchor.box
        boxes.append(anchor.box)
        if index and any(abs(a-b)>5 for a,b in zip(boxes[0],boxes[1])): return False
        roi=(x+w//2-55,y+h//2+247,110,32)
        sample=runner.backend.read_timer_roi(frame,runner.stop_event,captured,roi)
        if sample is None or sample.seconds>7200: return False
        samples.append(sample)
    if runner.pause_event.is_set() or runner.stop_event.is_set(): return False
    estimate=confirm_countdown(*samples)
    if estimate is None: return False
    runner.cooldown_estimate=estimate
    runner.record('diamond_timer',text=estimate.text,score=estimate.score)
    runner.emit('log',f'免费钻石包剩余 {estimate.text}：两帧复核通过，按实际剩余时间加 3 秒缓冲调度')
    return True
