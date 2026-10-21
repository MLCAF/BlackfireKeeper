"""Independent window preference; never rewrites dungeon plans or cooldowns."""
import json
import uuid
from pathlib import Path
from .window_geometry import VALIDATED_CLIENT_SIZES

CHOICES = {'自动适配工作区': None, **{f'{w}×{h}': (w,h) for w,h in VALIDATED_CLIENT_SIZES}}

def validate_size(value):
    if value is None:return None
    if not isinstance(value,(list,tuple)) or len(value)!=2 or any(type(n) is not int for n in value):
        raise ValueError('请选择列表中的游戏客户区尺寸')
    size=tuple(value)
    if size not in VALIDATED_CLIENT_SIZES:raise ValueError('不支持该游戏客户区尺寸')
    return size

def load_resolution(root):
    path=Path(root)/'config/runtime/window_options.json'
    if not path.exists():return None
    data=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(data,dict):raise ValueError('窗口设置格式错误')
    return validate_size(data.get('client_size'))

def save_resolution(root,size):
    size=validate_size(size)
    path=Path(root)/'config/runtime/window_options.json'
    data=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
    if not isinstance(data,dict):raise ValueError('窗口设置格式错误，保留原文件')
    data['client_size']=size
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(f'.window-{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()
