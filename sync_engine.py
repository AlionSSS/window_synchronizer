"""同步引擎：Win32 钩子安装、窗口管理、消息转发。"""

import ctypes
from ctypes import wintypes
import threading
from dataclasses import dataclass
from enum import IntEnum
from queue import Queue

# ── Win32 API 常量 ──────────────────────────────────────────────

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_XBUTTONDBLCLK = 0x020D
WM_MOUSEHWHEEL = 0x020E

MOD_CTRL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

HC_ACTION = 0

# 鼠标消息 wParam 标志
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_SHIFT = 0x0004
MK_CONTROL = 0x0008
MK_MBUTTON = 0x0010
MK_XBUTTON1 = 0x0020
MK_XBUTTON2 = 0x0040
MK_BUTTONS = MK_LBUTTON | MK_RBUTTON | MK_MBUTTON | MK_XBUTTON1 | MK_XBUTTON2

# GetAsyncKeyState 虚拟键码
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_S = 0x53

# 窗口类样式 / 系统度量
CS_DBLCLKS = 0x0008
GCL_STYLE = -26
SM_CXDOUBLECLK = 36
SM_CYDOUBLECLK = 37
GWLP_WNDPROC = -4

# 消息泵
QS_ALLINPUT = 0x04FF
PM_REMOVE = 0x0001
PUMP_INTERVAL_MS = 50
ERROR_ALREADY_EXISTS = 183

# KBDLLHOOKSTRUCT flags
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20

# ── Win32 API 函数绑定 ──────────────────────────────────────────

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# MSLLHOOKSTRUCT
class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


# KBDLLHOOKSTRUCT
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


# RECT
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


# 窗口枚举回调类型
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# 钩子回调类型（LRESULT 在 64 位下为 8 字节）
HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)


def _setup_prototypes():
    """显式声明用到的函数签名，避免 64 位下句柄/返回值被截断。"""
    long_p = ctypes.c_longlong

    user32.CallNextHookEx.restype = long_p
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        HOOKPROC,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]

    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = long_p
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = []
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]

    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.ScreenToClient.restype = wintypes.BOOL
    user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]

    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetKeyState.restype = ctypes.c_short
    user32.GetKeyState.argtypes = [ctypes.c_int]
    user32.GetDoubleClickTime.restype = wintypes.UINT
    user32.GetDoubleClickTime.argtypes = []
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]

    class_long = (
        user32.GetClassLongPtrW
        if hasattr(user32, "GetClassLongPtrW")
        else user32.GetClassLongW
    )
    class_long.restype = ctypes.c_size_t
    class_long.argtypes = [wintypes.HWND, ctypes.c_int]

    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = long_p
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
    user32.MsgWaitForMultipleObjects.argtypes = [
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.DWORD,
    ]

    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.RegisterHotKey.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

    user32.SetWindowLongPtrW.restype = ctypes.c_longlong
    user32.SetWindowLongPtrW.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_longlong,
    ]
    user32.CallWindowProcW.restype = long_p
    user32.CallWindowProcW.argtypes = [
        ctypes.c_longlong,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]

    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetTickCount.restype = wintypes.DWORD
    kernel32.GetTickCount.argtypes = []
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]


_setup_prototypes()


def _enable_dpi_awareness():
    """声明进程 DPI 感知。

    低层钩子给出的 MSLLHOOKSTRUCT.pt 是物理屏幕像素，而 GetWindowRect 在
    非 DPI 感知进程里返回的是被系统虚拟化的逻辑坐标。两者混用会在缩放
    ≠100% 时出现坐标错位，这里是唯一的正确修法。
    """
    per_monitor_v2 = ctypes.c_void_p(-4)
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(per_monitor_v2):
            return
    except Exception:
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()


_INSTANCE_MUTEX = None


def acquire_single_instance(
    name: str = "Local\\WindowSynchronizer.SingleInstance",
) -> bool:
    """占用单实例互斥体。

    多开时两个进程会各自安装一对钩子，同一个物理点击被转发两次，对开关型
    UI 表现为"点一次生效、下一次无效"，因此必须阻止多开。
    """
    global _INSTANCE_MUTEX
    if _INSTANCE_MUTEX is not None:
        return True
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True  # 创建失败时不阻塞正常使用
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX = handle
    return True


# ── 鼠标按键定义 ────────────────────────────────────────────────


@dataclass(frozen=True)
class _ButtonSpec:
    """单个鼠标按键的常量表。"""

    mk: int  # wParam 中的 MK_ 标志位
    vk: int  # GetAsyncKeyState 虚拟键码
    down: int
    up: int
    dblclk: int
    xbutton: int = 0  # WM_XBUTTONxxx 需要的 wParam 高位（1/2）


_BUTTONS = (
    _ButtonSpec(MK_LBUTTON, VK_LBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK),
    _ButtonSpec(MK_RBUTTON, VK_RBUTTON, WM_RBUTTONDOWN, WM_RBUTTONUP, WM_RBUTTONDBLCLK),
    _ButtonSpec(MK_MBUTTON, VK_MBUTTON, WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MBUTTONDBLCLK),
    _ButtonSpec(
        MK_XBUTTON1, VK_XBUTTON1, WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK, 1
    ),
    _ButtonSpec(
        MK_XBUTTON2, VK_XBUTTON2, WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK, 2
    ),
)

_BUTTON_BY_MSG = {
    WM_LBUTTONDOWN: _BUTTONS[0],
    WM_LBUTTONUP: _BUTTONS[0],
    WM_LBUTTONDBLCLK: _BUTTONS[0],
    WM_RBUTTONDOWN: _BUTTONS[1],
    WM_RBUTTONUP: _BUTTONS[1],
    WM_RBUTTONDBLCLK: _BUTTONS[1],
    WM_MBUTTONDOWN: _BUTTONS[2],
    WM_MBUTTONUP: _BUTTONS[2],
    WM_MBUTTONDBLCLK: _BUTTONS[2],
}
_XBUTTON_BY_ID = {1: _BUTTONS[3], 2: _BUTTONS[4]}
_XBUTTON_MSGS = (WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK)


def _resolve_button(msg: int, mouse_data: int) -> _ButtonSpec | None:
    """把钩子消息解析为按键定义（非按键消息返回 None）。"""
    if msg in _XBUTTON_MSGS:
        return _XBUTTON_BY_ID.get((mouse_data >> 16) & 0xFFFF)
    return _BUTTON_BY_MSG.get(msg)


def _pack_point(x: int, y: int) -> int:
    """按 Win32 的 16 位坐标打包成 lParam。"""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _get_class_style(hwnd: int) -> int:
    """读取窗口类样式（用于判断是否支持双击消息）。"""
    try:
        func = (
            user32.GetClassLongPtrW
            if hasattr(user32, "GetClassLongPtrW")
            else user32.GetClassLongW
        )
        return int(func(hwnd, GCL_STYLE) or 0)
    except Exception:
        return 0


@dataclass
class WindowInfo:
    """窗口信息数据类。"""

    hwnd: int
    title: str
    process_name: str = ""
    is_master: bool = False
    is_slave: bool = False


class SyncMode(IntEnum):
    """同步范围：控制转发键盘 / 鼠标事件。"""

    BOTH = 0  # 全部同步（默认）
    KEYBOARD = 1  # 仅键盘
    MOUSE = 2  # 仅鼠标

    @property
    def sync_keyboard(self) -> bool:
        """是否转发键盘事件。"""
        return self in (SyncMode.BOTH, SyncMode.KEYBOARD)

    @property
    def sync_mouse(self) -> bool:
        """是否转发鼠标事件。"""
        return self in (SyncMode.BOTH, SyncMode.MOUSE)


class SyncEngine:
    """同步引擎：管理窗口枚举、钩子安装/卸载、消息转发。"""

    # 鼠标移动转发的最小间隔（毫秒）
    MOVE_THROTTLE_MS = 8
    # 丢失 UP 自愈检查的最小间隔（毫秒）
    REPAIR_INTERVAL_MS = 50

    def __init__(self):
        self._windows: list[WindowInfo] = []
        # 窗口列表跨线程读写锁（仅主线程/UI 线程持锁，钩子线程不再加锁）
        self._windows_lock = threading.Lock()

        # 供钩子线程无锁读取的不可变快照
        self._master_hwnd: int | None = None
        self._slave_hwnds: tuple[int, ...] = ()

        self._keyboard_hook_id: int | None = None
        self._mouse_hook_id: int | None = None
        self._hotkey_id: int = 1
        self._hotkey_registered: bool = False
        self._hotkey_hwnd: int = 0
        self._orig_wndproc: int = 0
        self._wndproc = None
        self._running: bool = False
        self._hooks_active: bool = False
        self._mode: SyncMode = SyncMode.BOTH
        self._hook_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # 钩子安装完成信号与失败原因（用于消除 install_hooks 的启动竞态）
        self._hooks_installed = threading.Event()
        self._hook_error: str | None = None

        # 保存 ctypes 回调引用防止被 GC
        self._keyboard_proc: HOOKPROC | None = None
        self._mouse_proc: HOOKPROC | None = None

        # 线程安全通知队列，替代直接回调（避免跨线程 tkinter 调用崩溃）
        self._notify_queue: Queue = Queue()

        # ── 鼠标配对状态 ──
        # 受控窗口中"已投递按下、尚未投递松开"的按键掩码，保证 DOWN/UP 严格成对
        self._pair_lock = threading.Lock()
        self._slave_pressed: dict[int, int] = {}
        self._slave_last_xy: dict[int, tuple[int, int]] = {}
        # 物理按键状态镜像（比 GetAsyncKeyState 更实时，且与已投递消息一致）
        self._mk_state = 0

        # 双击识别
        self._dbl_time_ms = 500
        self._dbl_min_x = 2
        self._dbl_min_y = 2
        self._last_down: dict[int, tuple[int, int, int]] = {}
        self._dblclk_cache: dict[int, bool] = {}

        # 节流 / 自愈计时器（毫秒，来自钩子时间戳）
        self._last_move_ms = 0
        self._last_repair_ms = 0

        self._wndproc_setup()

    # ── 窗口枚举 ─────────────────────────────────────────────────

    @staticmethod
    def _get_process_name(hwnd: int) -> str:
        """获取窗口所属进程的可执行文件名。"""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_process = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
            )
            if not h_process:
                return ""
            try:
                size = wintypes.DWORD(260)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(
                    h_process, 0, buffer, ctypes.byref(size)
                ):
                    path = buffer.value
                    return path.rsplit("\\", 1)[-1]
            finally:
                kernel32.CloseHandle(h_process)
        except Exception:
            pass
        return ""

    def enumerate_windows(self) -> list[WindowInfo]:
        """枚举所有可见顶层窗口。"""
        result: list[WindowInfo] = []

        def enum_callback(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd) + 1
                buffer = ctypes.create_unicode_buffer(length)
                user32.GetWindowTextW(hwnd, buffer, length)
                title = buffer.value
                if title and title.strip():
                    proc_name = SyncEngine._get_process_name(hwnd)
                    # 统一转为 int 存储，避免 ctypes 类型比较问题
                    result.append(
                        WindowInfo(hwnd=int(hwnd), title=title, process_name=proc_name)
                    )
            return True

        proc = WNDENUMPROC(enum_callback)
        user32.EnumWindows(proc, 0)
        with self._windows_lock:
            self._windows = result
        self._publish_snapshot()
        return result

    def get_window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        """获取窗口矩形 (left, top, right, bottom)。"""
        rect = self._try_rect(hwnd)
        return rect if rect is not None else (0, 0, 0, 0)

    @staticmethod
    def _try_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        """获取窗口矩形，窗口已失效时返回 None。"""
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)

    # ── 快照发布（钩子线程无锁读取） ─────────────────────────────

    def _publish_snapshot(self):
        """重建不可变快照：钩子线程只读原子引用，避免遍历中被修改。

        必须在未持有 _windows_lock 时调用。
        """
        with self._windows_lock:
            master: int | None = None
            slaves: list[int] = []
            for win in self._windows:
                if win.is_master:
                    master = win.hwnd
                elif win.is_slave:
                    slaves.append(win.hwnd)
        if master is not None:
            slaves = [h for h in slaves if h != master]
        # 单个属性赋值在 CPython 下是原子的，钩子线程不会读到半成品
        self._master_hwnd = master
        self._slave_hwnds = tuple(slaves)

    # ── 键盘钩子 ─────────────────────────────────────────────────

    def _keyboard_hook_callback(self, nCode: int, wParam: int, lParam: int) -> int:
        try:
            if nCode == HC_ACTION and self._running and self._mode.sync_keyboard:
                master_hwnd = self._master_hwnd
                if master_hwnd is not None:
                    if int(user32.GetForegroundWindow() or 0) == master_hwnd:
                        kb = ctypes.cast(
                            lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)
                        ).contents
                        # 跳过我们自己注入的事件
                        if not (kb.flags & LLKHF_INJECTED):
                            is_keydown = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                            if wParam in (WM_SYSKEYDOWN, WM_SYSKEYUP):
                                msg_type = WM_SYSKEYDOWN if is_keydown else WM_SYSKEYUP
                            else:
                                msg_type = WM_KEYDOWN if is_keydown else WM_KEYUP
                            self._forward_keyboard(
                                kb.vkCode, kb.scanCode, msg_type, kb.flags
                            )
        except Exception:
            # 钩子回调内抛异常会导致本次事件丢失转发，必须吞掉
            pass
        return user32.CallNextHookEx(self._keyboard_hook_id, nCode, wParam, lParam)

    def _forward_keyboard(self, vk_code: int, scan_code: int, msg: int, kb_flags: int):
        """将键盘事件转发到所有受控窗口（正确构造 lParam）。"""
        # KBDLLHOOKSTRUCT.flags → WM_KEYxxx lParam 映射:
        #   LLKHF_EXTENDED (bit 0) → lParam bit 24 (extended-key flag)
        #   LLKHF_ALTDOWN  (bit 5) → lParam bit 29 (context code)
        extended = (1 << 24) if (kb_flags & LLKHF_EXTENDED) else 0
        context = (1 << 29) if (kb_flags & LLKHF_ALTDOWN) else 0

        is_keydown = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if is_keydown:
            transition_prev = 0  # transition=0, previous=0
        else:
            transition_prev = (1 << 31) | (1 << 30)  # transition=1, previous=1

        lparam = 1 | (scan_code << 16) | extended | context | transition_prev

        for hwnd in self._slave_hwnds:
            user32.PostMessageW(hwnd, msg, vk_code, lparam & 0xFFFFFFFF)

    # ── 鼠标钩子 ─────────────────────────────────────────────────

    def _mouse_hook_callback(self, nCode: int, wParam: int, lParam: int) -> int:
        try:
            if nCode == HC_ACTION and self._running:
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                # 维护物理按键镜像：不受同步范围 / 前台窗口影响，始终准确
                spec = _resolve_button(wParam, ms.mouseData)
                if spec is not None:
                    if wParam == spec.up:
                        self._mk_state &= ~spec.mk
                    else:
                        self._mk_state |= spec.mk
                if self._mode.sync_mouse:
                    self._sync_mouse_event(wParam, ms)
        except Exception:
            pass
        return user32.CallNextHookEx(self._mouse_hook_id, nCode, wParam, lParam)

    def _sync_mouse_event(self, msg: int, ms):
        """按消息类型转发鼠标事件（含 DOWN/UP 配对保障）。"""
        master_hwnd = self._master_hwnd
        slave_hwnds = self._slave_hwnds
        if master_hwnd is None or not slave_hwnds:
            self._release_all_pressed()
            return

        # 自愈：任何鼠标事件都顺便补齐"物理已松开但受控端仍按住"的按键
        self._repair_missing_ups(ms.time)

        spec = _resolve_button(msg, ms.mouseData)
        is_up = spec is not None and msg == spec.up
        x, y = int(ms.pt.x), int(ms.pt.y)

        master_rect = self._try_rect(master_hwnd)
        if not is_up:
            # 按下/双击/移动/滚轮：仅在主控为前台且落点在其窗口内时转发
            if master_rect is None:
                return
            if int(user32.GetForegroundWindow() or 0) != master_hwnd:
                return
            if not (
                master_rect[0] <= x < master_rect[2]
                and master_rect[1] <= y < master_rect[3]
            ):
                return

        if msg == WM_MOUSEMOVE:
            if (ms.time - self._last_move_ms) & 0xFFFFFFFF < self.MOVE_THROTTLE_MS:
                return
            self._last_move_ms = ms.time
            wparam = self._pointer_state()
            for hwnd in slave_hwnds:
                client = self._map_point(hwnd, master_rect, x, y)
                if client is None:
                    continue
                self._remember_point(hwnd, client)
                self._post_mouse(
                    hwnd, WM_MOUSEMOVE, wparam, _pack_point(client[0], client[1])
                )
            return

        if msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            delta = ctypes.c_short((ms.mouseData >> 16) & 0xFFFF).value
            wparam = ((delta & 0xFFFF) << 16) | self._pointer_state()
            for hwnd in slave_hwnds:
                screen = self._map_screen_point(hwnd, master_rect, x, y)
                if screen is None:
                    continue
                # 滚轮消息的 lParam 是屏幕坐标
                self._post_mouse(hwnd, msg, wparam, _pack_point(screen[0], screen[1]))
            return

        if spec is None:
            return

        if is_up:
            # 松开不受前台/矩形限制：保证与已投递的 DOWN 严格配对。
            # 目标是"欠着 UP 的窗口"，而不是当前受控列表 —— 中途取消勾选的窗口
            # 也必须收到松开，否则它会一直停在按下状态。
            with self._pair_lock:
                targets = [
                    h for h, mask in self._slave_pressed.items() if mask & spec.mk
                ]
            for hwnd in targets:
                client = self._map_point(hwnd, master_rect, x, y)
                if client is None:
                    client = self._last_point(hwnd)
                wparam = (self._pointer_state() & ~spec.mk) & 0xFFFF
                wparam |= spec.xbutton << 16
                if self._post_mouse(
                    hwnd, spec.up, wparam, _pack_point(client[0], client[1])
                ):
                    self._clear_pressed(hwnd, spec.mk)
                elif not user32.IsWindow(hwnd):
                    self._forget_window(hwnd)
            return

        # 按下 / 双击
        is_dbl = self._detect_double_click(spec, x, y, ms.time)
        wparam = (self._pointer_state() | spec.mk) & 0xFFFF
        wparam |= spec.xbutton << 16
        for hwnd in slave_hwnds:
            client = self._map_point(hwnd, master_rect, x, y)
            if client is None:
                continue
            self._remember_point(hwnd, client)
            post_msg = spec.down
            if is_dbl and self._has_dblclks(hwnd):
                post_msg = spec.dblclk
            if self._post_mouse(
                hwnd, post_msg, wparam, _pack_point(client[0], client[1])
            ):
                self._mark_pressed(hwnd, spec.mk)
            elif not user32.IsWindow(hwnd):
                self._forget_window(hwnd)

    # ── 坐标映射 ─────────────────────────────────────────────────

    @staticmethod
    def _scale_point(master_rect, slave_rect, x: int, y: int) -> tuple[int, int]:
        """把主控窗口内的屏幕坐标按比例映射到受控窗口的屏幕坐标。"""
        mw = master_rect[2] - master_rect[0]
        mh = master_rect[3] - master_rect[1]
        sw = slave_rect[2] - slave_rect[0]
        sh = slave_rect[3] - slave_rect[1]
        rel_x = (x - master_rect[0]) / mw if mw > 0 else 0.0
        rel_y = (y - master_rect[1]) / mh if mh > 0 else 0.0
        return slave_rect[0] + int(rel_x * sw), slave_rect[1] + int(rel_y * sh)

    def _map_screen_point(
        self, hwnd: int, master_rect, x: int, y: int
    ) -> tuple[int, int] | None:
        """映射到受控窗口对应的屏幕坐标。"""
        if master_rect is None:  # 主控窗口已失效，无法换算
            return None
        slave_rect = self._try_rect(hwnd)
        if slave_rect is None:
            self._forget_window(hwnd)
            return None
        return self._scale_point(master_rect, slave_rect, x, y)

    def _map_point(
        self, hwnd: int, master_rect, x: int, y: int
    ) -> tuple[int, int] | None:
        """映射到受控窗口的客户区坐标。"""
        screen = self._map_screen_point(hwnd, master_rect, x, y)
        if screen is None:
            return None
        pt = wintypes.POINT(screen[0], screen[1])
        if not user32.ScreenToClient(hwnd, ctypes.byref(pt)):
            return None
        return int(pt.x), int(pt.y)

    # ── 配对状态维护 ─────────────────────────────────────────────

    def _mark_pressed(self, hwnd: int, mk: int) -> None:
        with self._pair_lock:
            self._slave_pressed[hwnd] = self._slave_pressed.get(hwnd, 0) | mk

    def _clear_pressed(self, hwnd: int, mk: int) -> None:
        with self._pair_lock:
            mask = self._slave_pressed.get(hwnd, 0) & ~mk
            if mask:
                self._slave_pressed[hwnd] = mask
            else:
                self._slave_pressed.pop(hwnd, None)

    def _is_pressed(self, hwnd: int, mk: int) -> bool:
        """该受控窗口是否仍欠着某个按键的松开消息。"""
        with self._pair_lock:
            return bool(self._slave_pressed.get(hwnd, 0) & mk)

    def _remember_point(self, hwnd: int, client: tuple[int, int]) -> None:
        with self._pair_lock:
            self._slave_last_xy[hwnd] = client

    def _last_point(self, hwnd: int) -> tuple[int, int]:
        with self._pair_lock:
            return self._slave_last_xy.get(hwnd, (0, 0))

    def _forget_window(self, hwnd: int) -> None:
        """清掉已失效窗口的所有缓存状态。"""
        self._dblclk_cache.pop(hwnd, None)
        with self._pair_lock:
            self._slave_pressed.pop(hwnd, None)
            self._slave_last_xy.pop(hwnd, None)

    def _release_all_pressed(self) -> None:
        """向所有"已按下未松开"的受控窗口补发 UP（停止同步 / 切换模式时）。"""
        with self._pair_lock:
            snapshot = dict(self._slave_pressed)
            last = dict(self._slave_last_xy)
            self._slave_pressed.clear()
        for hwnd, mask in snapshot.items():
            x, y = last.get(hwnd, (0, 0))
            for spec in _BUTTONS:
                if not mask & spec.mk:
                    continue
                wparam = spec.xbutton << 16
                self._post_mouse(hwnd, spec.up, wparam, _pack_point(x, y))

    def _pointer_state(self) -> int:
        """当前鼠标按键 / 修饰键状态（用于 wParam）。"""
        state = self._mk_state & MK_BUTTONS
        if user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
            state |= MK_SHIFT
        if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
            state |= MK_CONTROL
        return state & 0xFFFF

    def _resync_mk_state(self) -> None:
        """以物理状态为准重建按键镜像。"""
        state = 0
        for spec in _BUTTONS:
            if user32.GetAsyncKeyState(spec.vk) & 0x8000:
                state |= spec.mk
        self._mk_state = state

    def _repair_missing_ups(self, now_ms: int | None = None) -> None:
        """自愈：物理按键已松开但受控端仍处于按下状态时，补发 UP。

        钩子可能因超时、异常、消息队列满等原因丢失 UP，一旦丢失，受控端会一直
        认为按键处于按下状态，之后每次点击的 DOWN 都被忽略 —— 表现为"隔一次
        才生效一次"。这里用 GetAsyncKeyState（物理真值）与配对记录比对。
        """
        with self._pair_lock:
            if not self._slave_pressed:
                return
            if now_ms is None:
                now_ms = int(kernel32.GetTickCount())
            if (now_ms - self._last_repair_ms) & 0xFFFFFFFF < self.REPAIR_INTERVAL_MS:
                return
            self._last_repair_ms = now_ms
            snapshot = dict(self._slave_pressed)

        for hwnd, mask in snapshot.items():
            for spec in _BUTTONS:
                if not mask & spec.mk:
                    continue
                if user32.GetAsyncKeyState(spec.vk) & 0x8000:
                    continue  # 物理上仍按住，属于正常状态
                client = self._last_point(hwnd)
                wparam = (self._pointer_state() & ~spec.mk) & 0xFFFF
                wparam |= spec.xbutton << 16
                if self._post_mouse(
                    hwnd, spec.up, wparam, _pack_point(client[0], client[1])
                ):
                    self._clear_pressed(hwnd, spec.mk)
                elif not user32.IsWindow(hwnd):
                    self._forget_window(hwnd)
        self._resync_mk_state()

    @staticmethod
    def _post_mouse(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
        """投递鼠标消息并返回是否成功（失败会被上层记录，避免状态残留）。"""
        return bool(
            user32.PostMessageW(
                int(hwnd), int(msg), wparam & 0xFFFFFFFF, lparam & 0xFFFFFFFF
            )
        )

    # ── 双击 / 类样式 ────────────────────────────────────────────

    def _detect_double_click(
        self, spec: _ButtonSpec, x: int, y: int, t_ms: int
    ) -> bool:
        """按系统双击时间 / 距离判定本次按下是否为双击的第二下。"""
        prev = self._last_down.get(spec.mk)
        self._last_down[spec.mk] = (t_ms, x, y)
        if prev is None:
            return False
        prev_t, prev_x, prev_y = prev
        if ((t_ms - prev_t) & 0xFFFFFFFF) > self._dbl_time_ms:
            return False
        if abs(x - prev_x) > self._dbl_min_x or abs(y - prev_y) > self._dbl_min_y:
            return False
        # 已判定为双击，重置计时，避免三击继续叠加
        self._last_down.pop(spec.mk, None)
        return True

    def _has_dblclks(self, hwnd: int) -> bool:
        """目标窗口类是否声明了 CS_DBLCLKS。"""
        cached = self._dblclk_cache.get(hwnd)
        if cached is not None:
            return cached
        has = bool(_get_class_style(hwnd) & CS_DBLCLKS)
        self._dblclk_cache[hwnd] = has
        return has

    # ── 钩子管理 ─────────────────────────────────────────────────

    def install_hooks(self) -> bool:
        """安装键盘和鼠标钩子。返回 True 表示成功。"""
        if self._running:
            return True

        # 前置条件：必须设置了主控窗口
        if self.get_master() is None:
            self._notify("请先设置主控窗口")
            return False
        # 前置条件：必须至少有一个受控窗口
        if not self._slave_hwnds:
            self._notify("请至少勾选一个受控窗口")
            return False

        # 防重复安装：上一轮钩子线程或 hook 尚未释放时，绝不能装第二对钩子，
        # 否则每次点击会被转发两次（开关型 UI 表现为"隔一次生效一次"）。
        if self._hooks_active or (
            self._hook_thread is not None and self._hook_thread.is_alive()
        ):
            self._force_stop_hook_thread()
            if self._hooks_active or (
                self._hook_thread is not None and self._hook_thread.is_alive()
            ):
                self._notify("上一次的钩子尚未释放，请稍后重试")
                return False

        # 清掉可能残留的配对状态，避免带着脏状态开始
        self._release_all_pressed()
        self._reset_runtime_state()

        self._stop_event.clear()
        self._hooks_installed.clear()
        self._hook_error = None
        self._hook_thread = threading.Thread(
            target=self._hook_thread_proc, daemon=True, name="sync-hook"
        )
        self._hook_thread.start()

        # 等待子线程回报安装结果，消除 "_running" 启动竞态
        if not self._hooks_installed.wait(timeout=3.0):
            self._notify("钩子安装超时")
            self._force_stop_hook_thread()
            return False
        if self._hook_error is not None:
            self._notify(self._hook_error)
            self._force_stop_hook_thread()
            return False
        if not self._hooks_active:
            self._notify("钩子安装失败")
            self._force_stop_hook_thread()
            return False

        self._running = True
        self._notify("同步中")
        return True

    def _reset_runtime_state(self) -> None:
        """重置运行期状态（双击判定 / 节流计时 / 按键镜像）。"""
        self._resync_mk_state()
        self._last_down.clear()
        self._last_move_ms = 0
        self._last_repair_ms = 0

    def _hook_thread_proc(self):
        """后台线程：安装钩子并运行消息泵。"""
        installed: list[int] = []
        try:
            self._keyboard_proc = HOOKPROC(self._keyboard_hook_callback)
            self._keyboard_hook_id = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._keyboard_proc, None, 0
            )
            if not self._keyboard_hook_id:
                self._hook_error = "键盘钩子安装失败"
                return
            installed.append(int(self._keyboard_hook_id))

            self._mouse_proc = HOOKPROC(self._mouse_hook_callback)
            self._mouse_hook_id = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._mouse_proc, None, 0
            )
            if not self._mouse_hook_id:
                self._hook_error = "鼠标钩子安装失败"
                return
            installed.append(int(self._mouse_hook_id))

            # 双击参数取自系统设置
            self._dbl_time_ms = int(user32.GetDoubleClickTime() or 500)
            self._dbl_min_x = max(user32.GetSystemMetrics(SM_CXDOUBLECLK) // 2, 1)
            self._dbl_min_y = max(user32.GetSystemMetrics(SM_CYDOUBLECLK) // 2, 1)

            self._hooks_active = True
            self._hooks_installed.set()
            self._notify("钩子已安装, 等待输入...")

            self._message_loop()
        finally:
            for hook_id in installed:
                user32.UnhookWindowsHookEx(hook_id)
            self._keyboard_hook_id = None
            self._mouse_hook_id = None
            self._hooks_active = False
            self._running = False
            # 保证 install_hooks 不会因为线程提前退出而永久阻塞
            self._hooks_installed.set()

    def _message_loop(self):
        """消息泵：定期醒来检查停止标志，避免 GetMessage 永久阻塞。"""
        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            user32.MsgWaitForMultipleObjects(
                0, None, False, PUMP_INTERVAL_MS, QS_ALLINPUT
            )
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_QUIT:
                    return
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

    def _force_stop_hook_thread(self):
        """停止钩子线程并回收资源（幂等）。"""
        self._running = False
        self._stop_event.set()
        thread = self._hook_thread
        if thread is not None and thread.is_alive():
            # 加速退出：唤醒消息泵
            user32.PostThreadMessageW(wintypes.DWORD(thread.ident or 0), WM_QUIT, 0, 0)
            thread.join(timeout=3.0)
        if thread is None or not thread.is_alive():
            self._hook_thread = None
            self._hooks_active = False

    def uninstall_hooks(self):
        """卸载钩子，并补齐受控端残留的按下状态。"""
        thread = self._hook_thread
        was_active = (
            self._running
            or self._hooks_active
            or (thread is not None and thread.is_alive())
        )
        self._force_stop_hook_thread()
        # 必须在钩子线程停止后再补发 UP，否则会有新的 DOWN 插进来
        self._release_all_pressed()
        self._keyboard_proc = None
        self._mouse_proc = None
        if thread is not None and thread.is_alive():
            self._notify("钩子线程未能在超时内退出")
        elif was_active:
            self._notify("已停止")

    # ── 热键管理 ─────────────────────────────────────────────────

    def register_hotkey(self, hwnd: int):
        """注册全局热键 Ctrl+Shift+S，并子类化窗口过程以接收 WM_HOTKEY。"""
        if self._hotkey_registered:
            return

        result = user32.RegisterHotKey(
            hwnd, self._hotkey_id, MOD_CTRL | MOD_SHIFT | MOD_NOREPEAT, VK_S
        )
        if not result:
            self._notify("热键注册失败(Ctrl+Shift+S可能已被占用)")
            return

        self._hotkey_registered = True
        self._hotkey_hwnd = hwnd

        # 子类化窗口过程以拦截 WM_HOTKEY
        self._orig_wndproc = user32.SetWindowLongPtrW(
            hwnd,
            GWLP_WNDPROC,
            ctypes.cast(self._wndproc, ctypes.c_void_p).value if self._wndproc else 0,
        )

    def unregister_hotkey(self, hwnd: int):
        """注销热键并恢复原始窗口过程。"""
        if self._hotkey_registered:
            user32.UnregisterHotKey(hwnd, self._hotkey_id)
            self._hotkey_registered = False
            # 恢复原始窗口过程
            if self._orig_wndproc:
                user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, self._orig_wndproc)
                self._orig_wndproc = 0

    def _wndproc_setup(self):
        """创建窗口过程回调（必须在 __init__ 后调用）。"""
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        # 缓存 GIL 函数引用
        _gil_ensure = ctypes.pythonapi.PyGILState_Ensure
        _gil_ensure.restype = ctypes.c_void_p
        _gil_ensure.argtypes = []
        _gil_release = ctypes.pythonapi.PyGILState_Release
        _gil_release.restype = None
        _gil_release.argtypes = [ctypes.c_void_p]

        @WNDPROC
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_HOTKEY:
                gstate = _gil_ensure()
                try:
                    self.handle_hotkey()
                finally:
                    _gil_release(gstate)
                return 0
            return user32.CallWindowProcW(self._orig_wndproc, hwnd, msg, wparam, lparam)

        self._wndproc = wndproc

    def handle_hotkey(self):
        """处理热键消息，切换同步状态。"""
        if self._running:
            self.uninstall_hooks()
        else:
            self.install_hooks()

    # ── 窗口管理 ─────────────────────────────────────────────────

    def set_master(self, hwnd: int):
        """设置主控窗口（不自动修改其他窗口的受控状态）。"""
        with self._windows_lock:
            for win in self._windows:
                win.is_master = win.hwnd == hwnd
                if win.is_master:
                    win.is_slave = False
        self._publish_snapshot()
        self.validate_windows()

    def toggle_slave(self, hwnd: int, checked: bool):
        """切换受控窗口状态。"""
        with self._windows_lock:
            for win in self._windows:
                if win.hwnd == hwnd:
                    win.is_slave = checked and not win.is_master
                    break
        self._publish_snapshot()

    def set_all_slaves(self, checked: bool):
        """批量设置受控窗口（除主控外）。"""
        with self._windows_lock:
            for win in self._windows:
                win.is_slave = bool(checked) and not win.is_master
        self._publish_snapshot()

    def get_master(self) -> WindowInfo | None:
        """返回当前主控窗口（无主控时返回 None）。"""
        with self._windows_lock:
            for win in self._windows:
                if win.is_master:
                    return win
        return None

    def get_windows(self) -> list[WindowInfo]:
        with self._windows_lock:
            return list(self._windows)

    def validate_windows(self):
        """验证窗口有效性，移除已关闭的窗口。

        注意：停止同步（uninstall_hooks）必须放在锁外执行。因为其内部会
        join 钩子线程，而锁也可能被其它路径持有。
        """
        master_closed = False
        removed: list[int] = []
        with self._windows_lock:
            for win in self._windows[:]:
                if not user32.IsWindow(win.hwnd):
                    if win.is_master:
                        master_closed = True
                    self._windows.remove(win)
                    removed.append(win.hwnd)
        for hwnd in removed:
            self._forget_window(hwnd)
        if removed:
            self._publish_snapshot()
        if master_closed:
            self.uninstall_hooks()
            self._notify("主控窗口已关闭，同步已停止")

    def cleanup(self):
        """清理所有资源。"""
        self.uninstall_hooks()

    # ── 内部方法 ─────────────────────────────────────────────────

    def _notify(self, status: str):
        """将状态消息放入线程安全队列（可由任意线程调用）。"""
        self._notify_queue.put(("status", status))

    def get_notifications(self) -> list[tuple[str, str]]:
        """获取所有待处理通知（仅限主线程调用）。"""
        items = []
        while not self._notify_queue.empty():
            try:
                items.append(self._notify_queue.get_nowait())
            except Exception:
                break
        return items

    @property
    def mode(self) -> SyncMode:
        """当前同步范围（键盘 / 鼠标 / 全部）。"""
        return self._mode

    @mode.setter
    def mode(self, value: SyncMode) -> None:
        previous = self._mode
        self._mode = value
        # 关闭鼠标同步时，避免受控端停留在"按下未松开"状态
        if previous.sync_mouse and not value.sync_mouse:
            self._release_all_pressed()

    @property
    def is_running(self) -> bool:
        return self._running
