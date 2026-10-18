"""Windows x64 process reader. No write/inject APIs; no persisted memory dumps."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import struct
import time


class MemoryReadError(RuntimeError):
    pass


class MBI(C.Structure):
    _fields_ = [('BaseAddress', C.c_void_p), ('AllocationBase', C.c_void_p),
                ('AllocationProtect', W.DWORD), ('PartitionId', W.WORD),
                ('RegionSize', C.c_size_t), ('State', W.DWORD),
                ('Protect', W.DWORD), ('Type', W.DWORD)]


def find_game_pid():
    class Entry(C.Structure):
        _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('th32ProcessID', W.DWORD),
                    ('th32DefaultHeapID', C.c_size_t), ('th32ModuleID', W.DWORD),
                    ('cntThreads', W.DWORD), ('th32ParentProcessID', W.DWORD),
                    ('pcPriClassBase', W.LONG), ('dwFlags', W.DWORD), ('szExeFile', W.WCHAR * 260)]
    k = C.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    k.CreateToolhelp32Snapshot.restype = W.HANDLE
    k.CloseHandle.argtypes = [W.HANDLE]
    for name in ('Process32FirstW', 'Process32NextW'):
        getattr(k, name).argtypes = [W.HANDLE, C.POINTER(Entry)]
        getattr(k, name).restype = W.BOOL
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle == C.c_void_p(-1).value:
        raise MemoryReadError('无法枚举进程')
    found = []
    try:
        entry = Entry(); entry.dwSize = C.sizeof(entry)
        ok = k.Process32FirstW(handle, C.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == 'blackfire crusade.exe':
                found.append(entry.th32ProcessID)
            ok = k.Process32NextW(handle, C.byref(entry))
    finally:
        k.CloseHandle(handle)
    if len(found) != 1:
        raise MemoryReadError(f'找到 {len(found)} 个游戏进程，需要恰好 1 个')
    return found[0]


class ProcessReader:
    def __init__(self, pid, expected_exe):
        if C.sizeof(C.c_void_p) != 8:
            raise RuntimeError('需要 64 位 Python')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        specs = {
            'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'CloseHandle': ([W.HANDLE], W.BOOL),
            'ReadProcessMemory': ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL),
            'VirtualQueryEx': ([W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t], C.c_size_t),
            'QueryFullProcessImageNameW': ([W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
            'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
            'GetProcessTimes': ([W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL),
        }
        for name, (args, result) in specs.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result
        self.pid = int(pid)
        self.handle = self.k.OpenProcess(0x1000 | 0x10 | 0x100000, False, pid)
        if not self.handle:
            raise MemoryReadError(f'打开进程失败: {C.get_last_error()}')
        try:
            size, buf = W.DWORD(32768), C.create_unicode_buffer(32768)
            if not self.k.QueryFullProcessImageNameW(self.handle, 0, buf, C.byref(size)):
                raise MemoryReadError('无法验证进程路径')
            self.path = Path(buf.value)
            if self.path.resolve() != Path(expected_exe).resolve():
                raise MemoryReadError('目标进程路径不匹配')
            times = [W.FILETIME() for _ in range(4)]
            if not self.k.GetProcessTimes(self.handle, *(C.byref(v) for v in times)):
                raise MemoryReadError('无法读取进程启动时间')
            self.creation = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        except Exception:
            self.close()
            raise

    def close(self):
        if getattr(self, 'handle', None):
            self.k.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def alive(self):
        return bool(self.handle) and self.k.WaitForSingleObject(self.handle, 0) == 258

    def read(self, address, size):
        if not self.alive():
            raise MemoryReadError('游戏进程已退出')
        if not (0x10000 <= address < 0x7FFFFFFFFFFF) or not (0 < size <= 8 * 1024 * 1024):
            raise MemoryReadError('无效地址或读取长度')
        buf, n = C.create_string_buffer(size), C.c_size_t()
        if not self.k.ReadProcessMemory(self.handle, address, buf, size, C.byref(n)) or n.value != size:
            raise MemoryReadError(f'内存不可读: 0x{address:x}')
        return buf.raw

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def regions(self):
        address = 0x10000
        while address < 0x7FFFFFFFFFFF:
            mbi = MBI()
            if not self.k.VirtualQueryEx(self.handle, address, C.byref(mbi), C.sizeof(mbi)):
                break
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if size <= 0 or base + size <= address:
                break
            # Godot heap: committed, private, readable, not guarded or executable.
            if mbi.State == 0x1000 and mbi.Type == 0x20000 and mbi.Protect in (0x02, 0x04, 0x08):
                yield base, size
            address = base + size

    def find(self, patterns, timeout=60, max_hits=10000, stop=None):
        """One streaming pass; bounded time, cancellation and cross-chunk overlap."""
        patterns = list(dict.fromkeys(patterns))
        if not patterns or any(not p for p in patterns):
            raise ValueError('扫描模式不能为空')
        result = {p: [] for p in patterns}
        deadline, count = time.monotonic() + timeout, 0
        overlap = max(map(len, patterns)) - 1
        for base, length in self.regions():
            for offset in range(0, length, 4 * 1024 * 1024):
                if time.monotonic() > deadline:
                    raise MemoryReadError('扫描超时，未使用不完整结果')
                if stop and stop.is_set():
                    raise InterruptedError('扫描已取消')
                size = min(4 * 1024 * 1024 + overlap, length - offset)
                try:
                    data = self.read(base + offset, size)
                except MemoryReadError:
                    continue
                for pattern in patterns:
                    start = 0
                    while True:
                        pos = data.find(pattern, start)
                        if pos < 0 or pos >= 4 * 1024 * 1024:
                            break
                        result[pattern].append(base + offset + pos)
                        count += 1
                        if count > max_hits:
                            raise MemoryReadError('候选过多，拒绝截断后选取')
                        start = pos + 1
        return result
