"""Fit the client to its monitor before capture; keep the recognition aspect ratio."""
import ctypes
from ctypes import wintypes as W
from contextlib import contextmanager
from pathlib import Path
import time

STANDARD_CLIENT_SIZE = (1920, 991)
VALIDATED_CLIENT_SIZES = ((1920, 991), (1600, 826), (1440, 743), (1280, 661))


def fit_client_size(work_size, frame_size):
    """Largest reference-aspect client inside the outer-window budget (pixels).

    Rounding is limited to one physical pixel; there are no resolution buckets.
    Invisible side-border overflow for the full-size reference is handled by
    the placement policy, not by reducing the small-window frame allowance.
    """
    available = tuple(work_size[i] - frame_size[i] for i in range(2))
    if min(available) <= 0 or min(frame_size) < 0:
        raise RuntimeError('显示器工作区或窗口边框尺寸异常，无法容纳游戏客户区')
    scale = min(1., *(available[i] / STANDARD_CLIENT_SIZE[i] for i in range(2)))
    return tuple(max(1, min(available[i], round(STANDARD_CLIENT_SIZE[i] * scale))) for i in range(2))


def select_client_size(work_size, frame_size):
    """Select a tested raster size below the calculated physical-pixel ceiling.

    Equal aspect ratios alone do not preserve glyph rasterization. Live testing
    found a 1335x689 title miss at unchanged thresholds; don't silently promise
    arbitrary intermediate or smaller untested rendering sizes.
    """
    fit_client_size(work_size, frame_size)  # Validate the physical measurements.
    for width,height in VALIDATED_CLIENT_SIZES:
        if width+frame_size[0]<=work_size[0] and height+frame_size[1]<=work_size[1]:
            return width,height
    raise RuntimeError('当前工作区扣除边框和标题栏后容不下最小已验证客户区 1280×661；请增大工作区后重试')


class Geometry:
    def __init__(self, windows):
        self.windows = windows
        self.u = windows.u
        for name, args, result in [
            ('GetWindowRect', [W.HWND, ctypes.POINTER(W.RECT)], W.BOOL),
            ('ClientToScreen', [W.HWND, ctypes.POINTER(W.POINT)], W.BOOL),
            ('IsZoomed', [W.HWND], W.BOOL),
            ('ShowWindowAsync', [W.HWND, ctypes.c_int], W.BOOL),
            ('SetWindowPos', [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT], W.BOOL),
            ('MonitorFromWindow', [W.HWND, W.DWORD], W.HANDLE),
            ('GetMonitorInfoW', [W.HANDLE, ctypes.c_void_p], W.BOOL),
            ('SetThreadDpiAwarenessContext', [ctypes.c_void_p], ctypes.c_void_p),
        ]:
            method=getattr(self.u,name);method.argtypes=args;method.restype=result

    @contextmanager
    def pixels(self):
        old=self.u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not old:raise RuntimeError('无法建立窗口物理像素坐标环境')
        try:yield
        finally:self.u.SetThreadDpiAwarenessContext(old)

    def validate(self, window):
        pid,exe=self.windows.identity(window.hwnd)
        if pid!=window.pid or exe.casefold()!=window.executable.casefold() or Path(exe).name.casefold()!='blackfire crusade.exe':
            raise RuntimeError('调整尺寸前游戏窗口身份核验失败')

    def size(self, window):return self.windows.size(window.hwnd)
    def client_origin(self, window):
        point=W.POINT(0,0)
        if not self.u.ClientToScreen(window.hwnd,ctypes.byref(point)):
            raise RuntimeError('无法读取游戏客户区的屏幕位置')
        return point.x,point.y
    def restored(self, window):return not (self.u.IsIconic(window.hwnd) or self.u.IsZoomed(window.hwnd))
    def minimized(self, window):return bool(self.u.IsIconic(window.hwnd))
    def restore(self, window):self.u.ShowWindowAsync(window.hwnd,9)
    def maximize(self, window):self.u.ShowWindowAsync(window.hwnd,3)

    def outer_and_work(self, window):
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',W.DWORD),('rcMonitor',W.RECT),('rcWork',W.RECT),('dwFlags',W.DWORD)]
        outer=W.RECT();info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
        monitor=self.u.MonitorFromWindow(window.hwnd,2)
        if not self.u.GetWindowRect(window.hwnd,ctypes.byref(outer)) or not self.u.GetMonitorInfoW(monitor,ctypes.byref(info)):
            raise RuntimeError('无法读取窗口边框或显示器工作区')
        return tuple(getattr(outer,k) for k in ('left','top','right','bottom')),tuple(getattr(info.rcWork,k) for k in ('left','top','right','bottom'))

    def resize(self, window, rectangle):
        if not self.u.SetWindowPos(window.hwnd,None,*rectangle,0x0014): # no Z-order/activation change
            raise RuntimeError('设置游戏窗口尺寸失败')


def standardize(adapter, window, stop, clock=time.monotonic, preferred=None):
    from .window_options import validate_size
    preferred=validate_size(preferred)
    report=dict(target_client_size=list(STANDARD_CLIENT_SIZE),status='checking',resize_requests=0,
                restore_requests=0, observations=[], requested_client_size=preferred)
    try:
        return _standardize(adapter, window, stop, clock, report, preferred)
    except Exception as exc:
        report.update(status='stopped' if isinstance(exc, InterruptedError) else 'failed',error=str(exc))
        exc.window_geometry=report
        raise


def _standardize(adapter, window, stop, clock, report, preferred):
    deadline=clock()+3
    def guard():
        if stop.is_set():raise InterruptedError('窗口尺寸调整已停止')
        adapter.validate(window)
    def visible(actual):
        outer,work=adapter.outer_and_work(window)
        cx,cy=adapter.client_origin(window)
        client=(cx,cy,cx+actual[0],cy+actual[1])
        report.update(actual_client_rect=list(client),work_area=list(work))
        # Maximized invisible borders may lie outside the monitor; all client
        # pixels must be visible. Normal windows also keep the title bar visible.
        return (client[0]>=work[0] and client[1]>=work[1]
                and client[2]<=work[2] and client[3]<=work[3]
                and (not adapter.restored(window) or outer[1]>=work[1]))
    guard()
    with adapter.pixels():
        was_minimized=adapter.minimized(window)
        before=None if was_minimized else adapter.size(window)
        report['initial_minimized']=bool(was_minimized)
        report['before_client_size']=None if before is None else list(before)
        if before==STANDARD_CLIENT_SIZE and preferred in (None, STANDARD_CLIENT_SIZE) and visible(before):
            report.update(status='already_standard',actual_client_size=list(before));return report
        if was_minimized or not adapter.restored(window):
            report['phase']='restore'
            guard();adapter.restore(window);report['restore_requests']=1
            stable=0
            while True:
                guard()
                if clock()>=deadline:raise RuntimeError('恢复游戏普通窗口超时')
                minimized=adapter.minimized(window)
                restored=adapter.restored(window)
                actual=None if minimized else adapter.size(window)
                report['observations'].append(dict(minimized=bool(minimized),normal=bool(restored),
                    client_size=None if actual is None else list(actual)))
                standard_visible=actual==STANDARD_CLIENT_SIZE and preferred in (None, STANDARD_CLIENT_SIZE) and visible(actual)
                stable=stable+1 if standard_visible else 0
                if stable>=2:
                    report.update(status='restored_standard',actual_client_size=list(actual));return report
                if restored and actual is not None:
                    before=actual
                    break
                if (was_minimized and not minimized and not standard_visible
                        and report['restore_requests']==1):
                    guard();adapter.restore(window);report['restore_requests']=2
                stop.wait(.1)
        report['before_client_size']=list(before)
        outer,work=adapter.outer_and_work(window)
        frame=(outer[2]-outer[0]-before[0],outer[3]-outer[1]-before[1])
        if min(frame)<0:raise RuntimeError('窗口边框尺寸异常，无法选择客户区尺寸')
        work_size=(work[2]-work[0],work[3]-work[1])
        edge_aligned=(work_size[0]>=STANDARD_CLIENT_SIZE[0]
                      and work_size[0]<STANDARD_CLIENT_SIZE[0]+frame[0]
                      and work_size[1]>=STANDARD_CLIENT_SIZE[1]+frame[1])
        if preferred is not None:
            target=preferred
            edge_aligned=edge_aligned and target==STANDARD_CLIENT_SIZE
            if not edge_aligned and any(target[i]+frame[i]>work_size[i] for i in range(2)):
                raise RuntimeError(f'所选客户区 {target[0]}×{target[1]} 加上边框与标题栏后放不进当前工作区；请选择更小尺寸或自动适配')
        else:
            target=STANDARD_CLIENT_SIZE if edge_aligned else select_client_size(work_size,frame)
        ow,oh=target[0]+frame[0],target[1]+frame[1]
        report.update(phase='resize',outer_rect=list(outer),work_area=list(work),
            target_client_size=list(target),frame_size=list(frame),
            adaptive=target!=STANDARD_CLIENT_SIZE,
            calculated_client_ceiling=list(fit_client_size(work_size,frame)),
            size_policy='manual' if preferred else 'largest_validated_fit',
            scale=target[1]/STANDARD_CLIENT_SIZE[1])
        if before==target and visible(before):
            report.update(status='already_fitted',actual_client_size=list(before));return report
        if edge_aligned:
            cx,cy=adapter.client_origin(window)
            left=cx-outer[0]
            right=outer[2]-cx-before[0]
            if left<0 or right<0:raise RuntimeError('窗口边框尺寸异常，无法对齐客户区')
            x=max(work[0],min(cx,work[2]-target[0]))-left
            report['placement']='client_edges'
        else:
            x=max(work[0],min(outer[0],work[2]-ow))
            report['placement']='outer_window'
        y=max(work[1],min(outer[1],work[3]-oh))
        report['requested_outer_rect']=[x,y,ow,oh]
        guard();adapter.resize(window,(x,y,ow,oh));report['resize_requests']=1
        stable=0
        actual=before
        while clock()<deadline:
            guard();actual=adapter.size(window)
            report['observations'].append(dict(client_size=list(actual)))
            fully_visible=visible(actual)
            same_work=report['work_area']==list(work)
            stable=stable+1 if actual==target and fully_visible and same_work else 0
            if stable>=2:
                report.update(status='resized',actual_client_size=list(actual));return report
            stop.wait(.1)
        raise RuntimeError(f'游戏客户区未能稳定到 {target[0]}×{target[1]}（当前 {actual[0]}×{actual[1]}）或未完整显示，请检查游戏最小窗口尺寸与工作区；已停止连接')


def standardize_open_game(stop, emit, expected_exe, preferred=None):
    from vendor_core.windows import Windows
    windows=Windows()
    candidates=[w for w in windows.list_game_windows() if Path(w.executable).resolve()==Path(expected_exe).resolve()]
    if not candidates:
        emit('log','暂未找到游戏窗口；下次连接或开始识别时会应用已选窗口尺寸')
        return None
    if len(candidates)!=1:raise RuntimeError('发现多个游戏窗口，无法确定需要调整的窗口')
    result=standardize(Geometry(windows),candidates[0],stop,preferred=preferred)
    emit('window_geometry',result)
    width,height=result['actual_client_size']
    emit('log',f'游戏客户区已核验为 {width}×{height} 物理像素；截图短边统一为 720，未改变系统显示分辨率')
    return result
