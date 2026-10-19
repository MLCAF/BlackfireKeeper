"""Standardize the client area before capturing; never change screen resolution."""
import ctypes
from ctypes import wintypes as W
from contextlib import contextmanager
from pathlib import Path
import time

STANDARD_CLIENT_SIZE = (1920, 991)


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


def standardize(adapter, window, stop, clock=time.monotonic):
    report=dict(target_client_size=list(STANDARD_CLIENT_SIZE),status='checking',resize_requests=0,
                restore_requests=0, observations=[])
    try:
        return _standardize(adapter, window, stop, clock, report)
    except Exception as exc:
        report.update(status='stopped' if isinstance(exc, InterruptedError) else 'failed',error=str(exc))
        exc.window_geometry=report
        raise


def _standardize(adapter, window, stop, clock, report):
    deadline=clock()+3
    def guard():
        if stop.is_set():raise InterruptedError('窗口尺寸调整已停止')
        adapter.validate(window)
    guard()
    with adapter.pixels():
        was_minimized=adapter.minimized(window)
        before=None if was_minimized else adapter.size(window)
        report['initial_minimized']=bool(was_minimized)
        report['before_client_size']=None if before is None else list(before)
        if before==STANDARD_CLIENT_SIZE:
            report.update(status='already_standard',before_client_size=list(before),actual_client_size=list(before));return report
        if not adapter.restored(window):
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
                # SW_RESTORE may restore a minimized window to its previous
                # maximized state. Exact target pixels are sufficient evidence.
                stable=stable+1 if actual==STANDARD_CLIENT_SIZE else 0
                if stable>=2:
                    report.update(status='restored_standard',actual_client_size=list(actual));return report
                if restored and actual is not None:
                    before=actual
                    break
                if (was_minimized and not minimized and actual!=STANDARD_CLIENT_SIZE
                        and report['restore_requests']==1):
                    guard();adapter.restore(window);report['restore_requests']=2
                stop.wait(.1)
        report['before_client_size']=list(before)
        if before==STANDARD_CLIENT_SIZE:
            report.update(status='already_standard',actual_client_size=list(before));return report
        outer,work=adapter.outer_and_work(window)
        report.update(phase='resize',outer_rect=list(outer),work_area=list(work))
        ow=STANDARD_CLIENT_SIZE[0]+outer[2]-outer[0]-before[0]
        oh=STANDARD_CLIENT_SIZE[1]+outer[3]-outer[1]-before[1]
        edge_aligned=False
        if (ow>work[2]-work[0] and oh<=work[3]-work[1]
                and STANDARD_CLIENT_SIZE[0]<=work[2]-work[0]):
            # The resize borders may lie outside the work area while every
            # client pixel and the title bar remain visible. Maximization is
            # not equivalent: on another PC it can yield e.g. 1920 x 1017.
            cx,cy=adapter.client_origin(window)
            left=cx-outer[0]
            right=outer[2]-cx-before[0]
            if left<0 or right<0:raise RuntimeError('窗口边框尺寸异常，无法对齐客户区')
            x=max(work[0],min(cx,work[2]-STANDARD_CLIENT_SIZE[0]))-left
            y=max(work[1],min(outer[1],work[3]-oh))
            guard();adapter.resize(window,(x,y,ow,oh));report['resize_requests']=1
            report['placement']='client_edges';edge_aligned=True
        elif ow>work[2]-work[0] or oh>work[3]-work[1]:
            if STANDARD_CLIENT_SIZE[0]>work[2]-work[0] or STANDARD_CLIENT_SIZE[1]>work[3]-work[1]:
                raise RuntimeError('当前显示器工作区容不下标准游戏客户区 1920×991；请使用更大的显示器或工作区后重试')
            # The reference PC uses a maximized window: its invisible borders
            # extend past the work area. Accept only the exact client dimensions.
            guard();adapter.maximize(window);report['maximize_requested']=True
        else:
            x=max(work[0],min(outer[0],work[2]-ow));y=max(work[1],min(outer[1],work[3]-oh))
            guard();adapter.resize(window,(x,y,ow,oh));report['resize_requests']=1
        stable=0
        actual=before
        while clock()<deadline:
            guard();actual=adapter.size(window)
            report['observations'].append(dict(client_size=list(actual)))
            visible=True
            if edge_aligned:
                cx,cy=adapter.client_origin(window)
                client=(cx,cy,cx+actual[0],cy+actual[1])
                report['actual_client_rect']=list(client)
                visible=client[0]>=work[0] and client[1]>=work[1] and client[2]<=work[2] and client[3]<=work[3]
            stable=stable+1 if actual==STANDARD_CLIENT_SIZE and visible else 0
            if stable>=2:
                report.update(status='resized',actual_client_size=list(actual));return report
            stop.wait(.1)
        raise RuntimeError(f'游戏客户区未能稳定到 1920×991（当前 {actual[0]}×{actual[1]}）或未完整显示，请检查屏幕工作区与缩放；已停止连接')


def standardize_open_game(stop, emit, expected_exe):
    from vendor_core.windows import Windows
    windows=Windows()
    candidates=[w for w in windows.list_game_windows() if Path(w.executable).resolve()==Path(expected_exe).resolve()]
    if not candidates:
        emit('log','暂未找到游戏窗口；开始识别时会自动统一为客户区 1920×991 物理像素')
        return None
    if len(candidates)!=1:raise RuntimeError('发现多个游戏窗口，无法确定需要调整的窗口')
    result=standardize(Geometry(windows),candidates[0],stop)
    emit('window_geometry',result)
    emit('log','游戏客户区已核验为 1920×991 物理像素；未改变系统显示分辨率')
    return result
