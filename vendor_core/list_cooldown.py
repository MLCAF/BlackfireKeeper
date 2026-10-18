"""Bounded list-only scan. Names bind clocks to tasks; no detail/building clicks."""
import copy
import time
from .cooldown import confirm_countdown

NAMES = {i: name for i, name in enumerate(('一本','二本','三本','四本','五本','六本'),1)}
LIST_RULE = dict(name='无遮挡地下城列表', template='地下城列表通用标识.png',
                 threshold=.95, min_contrast_ratio=.65)


def timer_roi(box, shape):
    x,y,w,h=box
    roi=(x+w//2-50, y+h//2-190, 100, 32)
    left,top,width,height=roi
    return roi if min(left,top)>=0 and left+width<=shape[1] and top+height<=shape[0] else None


def list_navigation_profile(base, task, tasks):
    from .unified import build_dungeon_profile
    data=build_dungeon_profile(base,task,tasks)
    entry=copy.deepcopy(data['states']['Entry'])
    for rule in entry['rules']:
        if rule.get('next')=='SelectDungeon': rule['next']='ListReady'
    done=dict(LIST_RULE, action='none',event='task_complete',confirm_frames=2,foreground=True)
    entry['rules'].insert(0,copy.deepcopy(done))
    data.update(initial_state='Entry',max_rounds=0,max_minutes=1,
                states={'Entry':entry,'ListReady':{'timeout_seconds':30,'rules':[done]}})
    return data


class ListCooldownScanner:
    def __init__(self, backend, tasks, stop, pause, emit, clock=time.monotonic, wall=time.time, wait=None):
        self.backend,self.stop,self.pause,self.emit=backend,stop,pause,emit
        self.clock,self.wall,self.wait=clock,wall,wait or stop.wait
        self.tasks=[t for t in tasks if t.get('entrance_template')==NAMES.get(t['order'],'')+'名称.png']
        self.selected=[t for t in self.tasks if t['enabled'] and not t['fallback']]
        self.swipes=0

    def active(self):
        return not self.stop.is_set() and not self.pause.is_set()

    def capture(self):
        if not self.active(): return None
        frame=self.backend.capture(self.stop)
        captured=self.clock()
        self.emit('image',frame)
        if not self.backend.recognize(frame,LIST_RULE,self.stop): return None
        return frame,captured

    def anchors(self,frame):
        result={}
        for task in self.tasks:
            if not self.active(): break
            rule=dict(name=task['display_name']+'列表定位', template=task['entrance_template'],
                      threshold=.95,min_contrast_ratio=.65)
            match=self.backend.recognize(frame,rule,self.stop)
            if match: result[task['id']]=match.box
        return result

    def observe(self,frame,captured,task,box):
        roi=timer_roi(box,frame.shape)
        if roi is None: return None
        try:
            value=self.backend.read_timer_roi(frame,self.stop,captured,roi)
        except InterruptedError:
            raise
        except Exception as exc:
            self.emit('log',f"{task['display_name']}列表 OCR 不可用：{exc}；保留已有冷却记录")
            return None
        if value is not None: return value
        # Absence of text is NOT availability. Match positive door artwork instead.
        if task['order'] in (2,3,4,5):
            x,y,w,h=box
            ready=dict(name=task['display_name']+'列表可用门口',template='列表'+NAMES[task['order']]+'可用.png',
                       roi=[x+w//2-48,y+h//2-198,96,52],threshold=.96,min_contrast_ratio=.65)
            if min(ready['roi'][:2])>=0 and self.backend.recognize(frame,ready,self.stop):
                return 'ready'
        return None

    def scan(self):
        if not self.selected or not self.active(): return {}
        selected={t['id']:t for t in self.selected}
        seen,values=set(),{}
        started=self.clock()
        previous_signature=None
        for view in range(9):
            if not self.active() or self.clock()-started>60: break
            first=self.capture()
            if first is None: break
            image,t1=first; anchors1=self.anchors(image)
            samples={}
            for key,task in selected.items():
                if not self.active(): break
                if key in anchors1:
                    samples[key]=self.observe(image,t1,task,anchors1[key])
            if self.wait(.3) or not self.active(): break
            second=self.capture()
            if second is None: break
            image,t2=second; anchors2=self.anchors(image)
            for key,task in selected.items():
                if not self.active(): break
                a,b=anchors1.get(key),anchors2.get(key)
                if a is None or b is None or any(abs(x-y)>8 for x,y in zip(a,b)): continue
                if timer_roi(b,image.shape) is None: continue
                seen.add(key)
                second_value=self.observe(image,t2,task,b)
                first_value=samples.get(key)
                if first_value=='ready' and second_value=='ready':
                    values[key]='ready'
                else:
                    estimate=confirm_countdown(first_value,second_value)
                    if estimate: values[key]=estimate
            missing=[t for t in self.selected if t['id'] not in seen]
            if not missing or not self.active() or self.swipes>=8: break
            visible=[t['order'] for t in self.tasks if t['id'] in anchors2]
            if not visible: break
            target=min(t['order'] for t in missing)
            direction='right' if target<min(visible) else 'left'
            signature=(direction,tuple(sorted((key,tuple(box)) for key,box in anchors2.items())))
            if signature==previous_signature: break  # list did not move / reached edge
            previous_signature=signature
            # OCR can take time: re-capture and revalidate the list before every drag.
            fresh=self.capture()
            if fresh is None or not self.active(): break
            if self.clock()-started>60: break
            self.backend.swipe(direction,self.stop,self.pause)
            self.swipes+=1
            self.emit('log',f'列表冷却扫描：向{ "左" if direction=="left" else "右" }拖动（{self.swipes}/8），不打开副本详情')
            if self.wait(.8): break
        if self.stop.is_set(): return {}
        result={key:(value if value=='ready' else value.to_schedule(self.clock(),self.wall()))
                for key,value in values.items()}
        for key,task in selected.items():
            if key not in result:
                self.emit('log',f"{task['display_name']}：列表未取得可靠冷却/可用证据，保留已有记录")
        return result
