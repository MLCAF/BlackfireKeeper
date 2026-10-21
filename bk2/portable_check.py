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
    from .scene_variants import SceneVariantMixin, VARIANT_GROUPS
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
        from .forbidden_vision import ForbiddenVision, RULES
        for rule in RULES.values():
            recognizer.template_metadata(root/'assets/image'/rule['template'])
        chest = Image.new('RGB',(1395,720),(35,35,35))
        for name,point in [('treasure_title',(650,260)),('free_open',(500,465))]:
            with Image.open(root/'assets/image/forbidden'/f'{name}.png') as target:
                chest.paste(target.convert('RGB'),point)
        vision=ForbiddenVision(recognizer,threading.Event())
        scene,button=vision.observe(np.asarray(chest)[:,:,::-1].copy())
        if scene!='chest_free' or button is None:
            raise RuntimeError('Portable forbidden free chest recognition failed')
        chest.paste((35,35,35),(480,440,600,510))
        scene,button=vision.observe(np.asarray(chest)[:,:,::-1].copy())
        if scene!='chest_free' or button is not None:
            raise RuntimeError('Portable forbidden accepted a missing free button')
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
        for original, (variant, region) in (item for group in VARIANT_GROUPS for item in group.items()):
            canvas = Image.new('RGB', (1400, 800), (40, 40, 40))
            with Image.open(root / 'assets/image' / variant) as target:
                canvas.paste(target.convert('RGB'), tuple(region[:2]))
            if recognizer.recognize(np.asarray(canvas)[:, :, ::-1].copy(),
                    dict(name='portable scene variant', template=original, threshold=.95), threading.Event()) is None:
                raise RuntimeError(f'Portable scene variant failed: {variant}')
        home = Image.new('RGB', (1400, 800), (40, 40, 40))
        with Image.open(root/'assets/image/主界面高亮建筑核心.png') as core:
            home.paste(core.convert('RGB'),(335,210))
        from .recovery import HOME
        if recognizer.recognize(np.asarray(home)[:,:,::-1].copy(),dict(HOME,name='highlight home'),threading.Event()) is None:
            raise RuntimeError('Portable highlighted home matching failed')
        nav=next(r for r in boundary_profile()['states']['Boundary']['rules'] if r['template']=='地下城入口主体.png')
        page=Image.new('RGB',(1400,800),(40,40,40))
        with Image.open(root/'assets/image/地下城入口主体.png') as target:
            page.paste(target.convert('RGB'),(552,599))
        if recognizer.recognize(np.asarray(page)[:,:,::-1].copy(),nav,threading.Event()) is not None:
            raise RuntimeError('Portable wrong-tab navigation accepted without selected-levels context')
        with Image.open(root/'assets/image/关卡导航选中.png') as target:
            page.paste(target.convert('RGB'),(430,590))
        if recognizer.recognize(np.asarray(page)[:,:,::-1].copy(),nav,threading.Event()) is None:
            raise RuntimeError('Portable wrong-tab navigation matching failed')
        # Pure policy probe inside the executable: no Win32 window operations.
        from contextlib import nullcontext
        from .window_geometry import standardize
        class GeometryProbe:
            hidden=True
            requests=0
            def pixels(self):return nullcontext()
            def validate(self,window):pass
            def minimized(self,window):return self.hidden
            def restored(self,window):return False
            def size(self,window):return STANDARD_CLIENT_SIZE
            def restore(self,window):self.hidden=False;self.requests+=1
            def outer_and_work(self,window):return (-8,-8,1928,1022),(0,0,1920,1040)
            def client_origin(self,window):return (0,23)
        geometry=GeometryProbe()
        restored=standardize(geometry,None,threading.Event())
        if restored['status']!='restored_standard' or geometry.requests!=1:
            raise RuntimeError('Portable minimized/maximized restore regression')
        class EdgeProbe(GeometryProbe):
            hidden=False
            rectangle=None
            def restored(self,window):return True
            def size(self,window):return STANDARD_CLIENT_SIZE if self.rectangle else (822,1008)
            def outer_and_work(self,window):return (100,0,938,1047),(0,0,1920,1040)
            def client_origin(self,window):return (0,31) if self.rectangle else (108,31)
            def resize(self,window,rectangle):self.rectangle=rectangle
            def maximize(self,window):raise RuntimeError('Exact client placement must not maximize')
        edge=EdgeProbe()
        aligned=standardize(edge,None,threading.Event())
        if edge.rectangle!=(-8,0,1936,1030) or aligned['actual_client_rect']!=[0,31,1920,1022]:
            raise RuntimeError('Portable exact-client edge alignment regression')
        from .window_geometry import fit_client_size, select_client_size
        if (fit_client_size((1366,728),(16,39))!=(1335,689)
                or select_client_size((1366,728),(16,39))!=(1280,661)):
            raise RuntimeError('Portable adaptive window sizing failed')
        from .coordinate_check import check_coordinates
        coordinate_checks=check_coordinates()
        # Exercise the frozen diagnostic writer without a game connection.
        import tempfile
        from pathlib import Path
        from .backend import DiagnosticBackend
        from .diagnostics import save_failure
        with tempfile.TemporaryDirectory(prefix='BlackfireKeeper-diagnostic-') as temporary:
            diagnostic_root=Path(temporary)/'logs/diagnostics'
            writer=DiagnosticBackend.__new__(DiagnosticBackend)
            writer.diagnostic_root=diagnostic_root
            writer.last_image=np.zeros((8,9,3),dtype=np.uint8)
            writer.last_capture=1
            writer._previous_image=writer.last_image.copy()
            writer._previous_recognition=[{'name':'portable probe'}]
            writer._previous_capture=1
            target=Path(writer.save_diagnostic('SelfTest','offline probe',{}))
            if {p.name for p in target.parent.iterdir()}!={'report.json','screen.png','previous.png'}:
                raise RuntimeError('Portable diagnostic bundle is incomplete')
            second=save_failure(Path(temporary),'self-test','probe','offline probe')
            if second.parent==target.parent or (diagnostic_root/'latest.txt').read_text(encoding='utf-8')!=str(second):
                raise RuntimeError('Portable diagnostic bundle isolation failed')
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
            import wave
            for sound in ('completed','error'):
                with wave.open(str(root/'assets/sounds'/f'{sound}.wav')) as wav:
                    assert wav.getnchannels()==1 and wav.getnframes()>0
            played=[]
            app.task_sounds.player=played.append
            app.task_sounds.enabled=True
            app.emit('error','offline notification probe')
            app.emit('batch_result',dict(succeeded=True,preview=False,completed_runs=0,reason='offline probe'))
            app.drain()
            assert played==['error','completed'], played
            assert len(app.forbidden_panel.realm_buttons) == 5
            assert app.forbidden_panel.realm.get() == 'golden_kingdom'
            assert sum(supported for _, supported in app.forbidden_panel.realm_buttons) == 1
            assert len(app.window_panel.choice['values']) == 5
            assert app.window_panel.value.get() in app.window_panel.choice['values']
        finally:
            for callback in window.tk.call('after', 'info'):
                window.after_cancel(callback)
            window.destroy()
        report = dict(application=version_info(), result='passed',
            checks=['configuration', 'native_framework', 'native_toolkit', 'template_match', 'finish_portal', 'boundary_detail', 'scene_variants', 'highlight_home', 'levels_boundary', 'restore_policy', 'client_edge_alignment', 'diagnostic_bundles', 'ocr', 'tk_dashboard'],
            standard_client_size=list(STANDARD_CLIENT_SIZE), adaptive_coordinates=coordinate_checks,
            game_connected=False, game_input=False)
        (root / 'logs').mkdir(exist_ok=True)
        (root / 'logs/portable-self-test.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        recognizer.cancel().wait()
