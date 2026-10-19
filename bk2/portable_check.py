"""Offline packaging probe: actual native recognition, no game process access."""
import json
import threading
from .paths import application_root
from .window_geometry import STANDARD_CLIENT_SIZE


def run():
    import tkinter as tk
    import numpy as np
    from PIL import Image, ImageDraw
    from maa.controller import CustomController
    from maa.pipeline import JOCR, JRecognitionType
    from maa.library import Library
    from vendor_core.backend import MaaRecognizer
    from .scene_variants import SceneVariantMixin, SCENE_VARIANTS
    from .automation import load_plan
    from .gui import App
    from .version import version_info

    class Offline(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'portable-offline-self-test'
        def get_features(self): return 0
        def screencap(self): raise AssertionError('Self-test cannot capture the game')
        def click(self, *args): raise AssertionError('Self-test cannot click')

    root = application_root()
    plan = load_plan()
    # Force ctypes to load both framework and Windows toolkit dependencies.
    Library.framework()
    Library.toolkit()
    controller = Offline()
    controller.post_connection().wait()
    class PortableRecognizer(SceneVariantMixin, MaaRecognizer):
        pass
    recognizer = PortableRecognizer(root / 'assets', controller)
    try:
        template = Image.open(root / 'assets/image/一本名称.png').convert('RGB')
        canvas = Image.new('RGB', (1400, 800), (40, 40, 40))
        canvas.paste(template, (100, 100))
        frame = np.asarray(canvas)[:, :, ::-1].copy()
        hit = recognizer.recognize(frame, dict(name='portable template probe',
            template='一本名称.png', roi=[0, 0, 600, 300], threshold=.95), threading.Event())
        if hit is None or hit.box[:2] != (100, 100):
            raise RuntimeError('Portable template matching failed')
        # Exercise the patched finish rule from the frozen executable itself.
        from .profiles import build_dungeon_profile
        base = json.loads((root / 'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        profile = build_dungeon_profile(base, plan['tasks'][5], plan['tasks'])
        finish = next(r for r in profile['states']['Progress']['rules']
                      if r['template'] == '完成冒险传送门核心.png')
        portal = Image.new('RGB', (1400, 800), (40, 40, 40))
        with Image.open(root / 'assets/image/完成冒险传送门核心.png') as core:
            portal.paste(core.convert('RGB'), (669, 610))
        if recognizer.recognize(np.asarray(portal)[:, :, ::-1].copy(), finish, threading.Event()) is not None:
            raise RuntimeError('Portable finish rule accepted a portal without completion text')
        with Image.open(root / 'assets/image/完成冒险文字.png') as label:
            portal.paste(label.convert('RGB'), (656, 684))
        if recognizer.recognize(np.asarray(portal)[:, :, ::-1].copy(), finish, threading.Event()) is None:
            raise RuntimeError('Portable completion portal matching failed')
        from .recovery import boundary_profile
        close = next(r for r in boundary_profile()['states']['Boundary']['rules']
                     if r['template'] == '副本详情关闭.png')
        detail = Image.new('RGB', (1400, 800), (40, 40, 40))
        with Image.open(root / 'assets/image/副本详情关闭.png') as button:
            detail.paste(button.convert('RGB'), (910, 130))
        if recognizer.recognize(np.asarray(detail)[:, :, ::-1].copy(), close, threading.Event()) is not None:
            raise RuntimeError('Portable detail close accepted without modal context')
        with Image.open(root / 'assets/image/副本详情标识.png') as label:
            detail.paste(label.convert('RGB'), (568, 359))
        if recognizer.recognize(np.asarray(detail)[:, :, ::-1].copy(), close, threading.Event()) is None:
            raise RuntimeError('Portable detail close matching failed')
        for original, (variant, region) in SCENE_VARIANTS.items():
            canvas = Image.new('RGB', (1400, 800), (40, 40, 40))
            with Image.open(root / 'assets/image' / variant) as target:
                canvas.paste(target.convert('RGB'), tuple(region[:2]))
            if recognizer.recognize(np.asarray(canvas)[:, :, ::-1].copy(),
                    dict(name='portable scene variant', template=original, threshold=.95), threading.Event()) is None:
                raise RuntimeError(f'Portable scene variant failed: {variant}')
        # An actual OCR job detects missing onnxruntime/DirectML/OpenCV dependencies.
        ocr_image = Image.new('RGB', (400, 100), 'white')
        ImageDraw.Draw(ocr_image).text((12, 12), '01:23:45', fill='black', font_size=42)
        job = recognizer.tasker.post_recognition(JRecognitionType.OCR,
            JOCR(roi=(0, 0, 400, 100), only_rec=True, model='cooldown', threshold=.5),
            np.asarray(ocr_image)[:, :, ::-1].copy())
        recognizer.wait(job, threading.Event(), 30)
        result = job.get()
        if not result or not result.nodes or not result.nodes[-1].recognition:
            raise RuntimeError('Portable OCR returned no result')
        best = result.nodes[-1].recognition.best_result
        if best is None or best.text.replace('：', ':').strip() != '01:23:45':
            raise RuntimeError(f'Portable OCR mismatch: {best}')
        window = tk.Tk()
        window.withdraw()
        try:
            app = App(window)
            window.update_idletasks()
            assert app.session is None, 'Self-test must not connect to game'
        finally:
            for callback in window.tk.call('after', 'info'):
                window.after_cancel(callback)
            window.destroy()
        report = dict(application=version_info(), result='passed',
            checks=['configuration', 'native_framework', 'native_toolkit', 'template_match', 'finish_portal', 'boundary_detail', 'scene_variants', 'ocr', 'tk_dashboard'],
            standard_client_size=list(STANDARD_CLIENT_SIZE), game_connected=False, game_input=False)
        (root / 'logs').mkdir(exist_ok=True)
        (root / 'logs/portable-self-test.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        recognizer.cancel().wait()
