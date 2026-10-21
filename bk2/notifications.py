"""Non-blocking local task sounds, independent of plans and cooldown records."""
import json
import time
import uuid
from pathlib import Path


def load_enabled(root):
    path=Path(root)/'config/runtime/notifications.json'
    if not path.exists():return True
    data=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(data,dict) or type(data.get('sound_enabled',True)) is not bool:
        raise ValueError('提示音设置格式错误')
    return data.get('sound_enabled',True)


def save_enabled(root,enabled):
    if type(enabled) is not bool:raise ValueError('提示音开关必须为布尔值')
    path=Path(root)/'config/runtime/notifications.json'
    data=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
    if not isinstance(data,dict):raise ValueError('提示音设置格式错误，保留原文件')
    data['sound_enabled']=enabled
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(f'.notifications-{uuid.uuid4().hex}.tmp')
    try:
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
        tmp.replace(path)
    finally:
        if tmp.exists():tmp.unlink()


class TaskSounds:
    def __init__(self,root,enabled=True,clock=time.monotonic,player=None,log=None):
        self.root=Path(root);self.enabled=enabled;self.clock=clock
        self.player=player or self._play;self.log=log or (lambda message:None)
        self.last_error=float('-inf');self.last_completed=float('-inf')

    def _play(self,name):
        import winsound
        winsound.PlaySound(str(self.root/'assets/sounds'/f'{name}.wav'),
                           winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)

    def play(self,name):
        if name not in ('error','completed'):raise ValueError('Unknown task sound')
        try:self.player(name)
        except Exception as exc:
            # Audio failures must not stop automation or recursively notify errors.
            self.log(f'提示音播放失败：{exc}')

    def observe(self,kind,data):
        if not self.enabled:return
        now=self.clock()
        if kind in ('error','memory_error'):
            if now-self.last_error<10:return
            self.last_error=now;self.play('error')
        elif kind=='batch_result' and data.get('succeeded') and not data.get('preview'):
            if now-self.last_completed<1:return
            self.last_completed=now;self.play('completed')
