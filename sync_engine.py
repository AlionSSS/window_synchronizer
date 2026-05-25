"""同步引擎：Win32 钩子安装、窗口管理、消息转发。"""

import ctypes
from ctypes import wintypes
import threading
from dataclasses import dataclass
from queue import Queue
import time

# ── Win32 API 常量 ──────────────────────────────────────────────

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEMOVE = 0x0200

MOD_CTRL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

HC_ACTION = 0

# 鼠标消息对应的 wParam 虚拟键状态标志
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

_MOUSE_WPARAM_MAP = {
    WM_LBUTTONDOWN: MK_LBUTTON,
    WM_LBUTTONUP: 0,
    WM_RBUTTONDOWN: MK_RBUTTON,
    WM_RBUTTONUP: 0,
    WM_MBUTTONDOWN: MK_MBUTTON,
    WM_MBUTTONUP: 0,
}

# GetAsyncKeyState 虚拟键码
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04


def _mouse_wparam(msg: int) -> int:
    """返回鼠标消息对应的 wParam 值（WM_MOUSEMOVE 时检测按钮状态）。"""
    if msg == WM_MOUSEMOVE:
        wparam = 0
        if user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
            wparam |= MK_LBUTTON
        if user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000:
            wparam |= MK_RBUTTON
        if user32.GetAsyncKeyState(VK_MBUTTON) & 0x8000:
            wparam |= MK_MBUTTON
        return wparam
    return _MOUSE_WPARAM_MAP.get(msg, 0)

# KBDLLHOOKSTRUCT flags
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20

# ── Win32 API 函数绑定 ──────────────────────────────────────────

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 设置关键函数的返回类型和参数类型（防止 64 位截断）
user32.CallNextHookEx.restype = ctypes.c_longlong
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowLongPtrW.restype = ctypes.c_longlong
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
user32.CallWindowProcW.restype = ctypes.c_longlong
user32.CallWindowProcW.argtypes = [ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]

# 窗口枚举回调类型
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# 钩子回调类型（LRESULT 在 64 位下为 8 字节）
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

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
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

# RECT
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


@dataclass
class WindowInfo:
    """窗口信息数据类。"""
    hwnd: int
    title: str
    process_name: str = ""
    is_master: bool = False
    is_slave: bool = False


class SyncEngine:
    """同步引擎：管理窗口枚举、钩子安装/卸载、消息转发。"""

    def __init__(self):
        self._windows: list[WindowInfo] = []
        self._keyboard_hook_id: int | None = None
        self._mouse_hook_id: int | None = None
        self._hotkey_id: int = 1
        self._hotkey_registered: bool = False
        self._orig_wndproc: int = 0
        self._wndproc = None
        self._running: bool = False
        self._hook_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 保存 ctypes 回调引用防止被 GC
        self._keyboard_proc: HOOKPROC | None = None
        self._mouse_proc: HOOKPROC | None = None

        # 调试计数器
        self._kb_count: int = 0
        self._mouse_fired: int = 0      # 钩子回调被调用次数
        self._mouse_action: int = 0      # nCode==HC_ACTION 次数
        self._mouse_nonmove: int = 0     # 非移动事件次数
        self._mouse_forwarded: int = 0   # 实际转发次数
        self._mouse_rejected: int = 0    # 被条件拒绝次数
        self._last_debug_time: float = 0

        # 线程安全通知队列，替代直接回调（避免跨线程 tkinter 调用崩溃）
        self._notify_queue: Queue = Queue()

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
            h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not h_process:
                return ""
            try:
                size = wintypes.DWORD(260)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(h_process, 0, buffer, ctypes.byref(size)):
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
                    result.append(WindowInfo(hwnd=int(hwnd), title=title, process_name=proc_name))
            return True

        proc = WNDENUMPROC(enum_callback)
        user32.EnumWindows(proc, 0)
        self._windows = result
        return result

    def get_window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        """获取窗口矩形 (left, top, right, bottom)。"""
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)

    # ── 键盘钩子 ─────────────────────────────────────────────────

    def _keyboard_hook_callback(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode == HC_ACTION and self._running:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk_code = kb.vkCode

            # 确定消息类型
            is_keydown = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            msg_type = WM_KEYDOWN if is_keydown else WM_KEYUP
            if wParam in (WM_SYSKEYDOWN, WM_SYSKEYUP):
                msg_type = WM_SYSKEYDOWN if is_keydown else WM_SYSKEYUP

            # 仅当焦点窗口为主控窗口时转发
            master = self._get_master()
            if master and int(user32.GetForegroundWindow()) == master.hwnd:
                # 跳过注入事件
                if not (kb.flags & LLKHF_INJECTED):
                    self._forward_keyboard(vk_code, kb.scanCode, msg_type, kb.flags)

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

        slave_hwnds = [
            w.hwnd for w in self._windows if w.is_slave and user32.IsWindow(w.hwnd)
        ]
        for hwnd in slave_hwnds:
            user32.PostMessageW(hwnd, msg, vk_code, lparam)

        # 调试日志
        self._kb_count += 1
        self._emit_debug()

    # ── 鼠标钩子 ─────────────────────────────────────────────────

    def _mouse_hook_callback(self, nCode: int, wParam: int, lParam: int) -> int:
        self._mouse_fired += 1
        if nCode == HC_ACTION and self._running:
            self._mouse_action += 1
            master = self._get_master()
            if master and int(user32.GetForegroundWindow()) == master.hwnd:
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if wParam != WM_MOUSEMOVE:
                    self._mouse_nonmove += 1
                self._forward_mouse(wParam, ms.pt.x, ms.pt.y)
        return user32.CallNextHookEx(self._mouse_hook_id, nCode, wParam, lParam)

    def _forward_mouse(self, msg: int, click_x: int, click_y: int):
        """将鼠标点击事件转发到所有受控窗口（坐标转换）。"""
        master = self._get_master()
        if master is None:
            self._mouse_rejected += 1
            return
        if not user32.IsWindow(master.hwnd):
            self._mouse_rejected += 1
            return

        rect = self.get_window_rect(master.hwnd)
        master_w = rect[2] - rect[0]
        master_h = rect[3] - rect[1]

        # 仅当点击位置在主控窗口范围内时转发
        if not (rect[0] <= click_x <= rect[2] and rect[1] <= click_y <= rect[3]):
            self._mouse_rejected += 1
            return

        # 相对坐标 [0, 1]
        rel_x = (click_x - rect[0]) / max(master_w, 1)
        rel_y = (click_y - rect[1]) / max(master_h, 1)

        # 转发到每个受控窗口
        for win in self._windows:
            if win.is_slave and user32.IsWindow(win.hwnd):
                sr = self.get_window_rect(win.hwnd)
                sw = sr[2] - sr[0]
                sh = sr[3] - sr[1]
                # 先用比例算出屏幕坐标，再用 ScreenToClient 转客户端坐标
                screen_x = sr[0] + int(rel_x * sw)
                screen_y = sr[1] + int(rel_y * sh)
                pt = wintypes.POINT(screen_x, screen_y)
                user32.ScreenToClient(win.hwnd, ctypes.byref(pt))
                lparam = (pt.y << 16) | (pt.x & 0xFFFF)
                wparam = _mouse_wparam(msg)
                user32.PostMessageW(win.hwnd, msg, wparam, lparam)

        self._mouse_forwarded += 1
        self._emit_debug()

    # ── 钩子管理 ─────────────────────────────────────────────────

    def install_hooks(self) -> bool:
        """安装键盘和鼠标钩子。返回 True 表示成功，False 表示前置条件不满足。"""
        if self._running:
            return True

        # 前置条件：必须设置了主控窗口
        if self._get_master() is None:
            self._notify("请先设置主控窗口")
            return False
        # 前置条件：必须至少有一个受控窗口
        slave_count = sum(1 for w in self._windows if w.is_slave)
        if slave_count == 0:
            self._notify("请至少勾选一个受控窗口")
            return False
        self._mouse_fired = 0
        self._mouse_action = 0
        self._mouse_nonmove = 0
        self._mouse_forwarded = 0
        self._mouse_rejected = 0
        self._stop_event.clear()
        self._hook_thread = threading.Thread(target=self._hook_thread_proc, daemon=True)
        self._hook_thread.start()
        self._running = True
        self._notify("同步中")
        return True

    def _hook_thread_proc(self):
        """后台线程：安装钩子并运行消息泵。"""
        # 低层钩子使用 NULL 作为 hMod（当前进程）
        hinst = 0

        # 安装键盘钩子
        self._keyboard_proc = HOOKPROC(self._keyboard_hook_callback)
        self._keyboard_hook_id = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._keyboard_proc, hinst, 0
        )
        if not self._keyboard_hook_id:
            self._notify("键盘钩子安装失败")
            self._running = False
            return

        # 安装鼠标钩子
        self._mouse_proc = HOOKPROC(self._mouse_hook_callback)
        self._mouse_hook_id = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_proc, hinst, 0
        )
        if not self._mouse_hook_id:
            self._notify("鼠标钩子安装失败")
            user32.UnhookWindowsHookEx(self._keyboard_hook_id)
            self._keyboard_hook_id = None
            self._running = False
            return

        self._notify("钩子已安装, 等待输入...")

        # 消息循环
        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if result <= 0:
                break  # WM_QUIT
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # 清理
        if self._keyboard_hook_id:
            user32.UnhookWindowsHookEx(self._keyboard_hook_id)
            self._keyboard_hook_id = None
        if self._mouse_hook_id:
            user32.UnhookWindowsHookEx(self._mouse_hook_id)
            self._mouse_hook_id = None

    def uninstall_hooks(self):
        """卸载钩子。"""
        if not self._running:
            return
        self._stop_event.set()
        # 向钩子线程发送 WM_QUIT 使其退出消息循环
        if self._hook_thread and self._hook_thread.is_alive():
            user32.PostThreadMessageW(self._hook_thread.ident, 0x0012, 0, 0)  # WM_QUIT
            self._hook_thread.join(timeout=3.0)
        self._running = False
        self._keyboard_proc = None
        self._mouse_proc = None
        self._notify("已停止")

    # ── 热键管理 ─────────────────────────────────────────────────

    def register_hotkey(self, hwnd: int):
        """注册全局热键 Ctrl+Shift+S，并子类化窗口过程以接收 WM_HOTKEY。"""
        if self._hotkey_registered:
            return

        result = user32.RegisterHotKey(hwnd, self._hotkey_id,
                                       MOD_CTRL | MOD_SHIFT | MOD_NOREPEAT, 0x53)
        if not result:
            self._notify("热键注册失败(Ctrl+Shift+S可能已被占用)")
            return

        self._hotkey_registered = True
        self._hotkey_hwnd = hwnd

        # 子类化窗口过程以拦截 WM_HOTKEY
        GWLP_WNDPROC = -4
        self._orig_wndproc = user32.SetWindowLongPtrW(
            hwnd, GWLP_WNDPROC, ctypes.cast(self._wndproc, ctypes.c_void_p).value if self._wndproc else 0
        )

    def unregister_hotkey(self, hwnd: int):
        """注销热键并恢复原始窗口过程。"""
        if self._hotkey_registered:
            user32.UnregisterHotKey(hwnd, self._hotkey_id)
            self._hotkey_registered = False
            # 恢复原始窗口过程
            if self._orig_wndproc:
                GWLP_WNDPROC = -4
                user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, self._orig_wndproc)
                self._orig_wndproc = 0

    def _wndproc_setup(self):
        """创建窗口过程回调（必须在 __init__ 后调用）。"""
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
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
            return user32.CallWindowProcW(
                self._orig_wndproc, hwnd, msg, wparam, lparam
            )

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
        for win in self._windows:
            win.is_master = (win.hwnd == hwnd)
            # 主控窗口不能同时是受控
            if win.is_master:
                win.is_slave = False
        self._validate_windows()

    def toggle_slave(self, hwnd: int, checked: bool):
        """切换受控窗口状态。"""
        for win in self._windows:
            if win.hwnd == hwnd:
                win.is_slave = checked and not win.is_master
                break
        self._validate_windows()

    def _update_slaves(self):
        """更新受控窗口：除主控外所有勾选的窗口为受控。"""
        master = self._get_master()
        for win in self._windows:
            if master and win.hwnd != master.hwnd:
                win.is_slave = True
        self._validate_windows()

    def _get_master(self) -> WindowInfo | None:
        for win in self._windows:
            if win.is_master:
                return win
        return None

    def get_windows(self) -> list[WindowInfo]:
        return self._windows

    def _validate_windows(self):
        """验证窗口有效性，移除已关闭的窗口。"""
        removed = False
        for win in self._windows[:]:
            if not user32.IsWindow(win.hwnd):
                if win.is_master:
                    self.uninstall_hooks()
                    self._notify("主控窗口已关闭，同步已停止")
                self._windows.remove(win)
                removed = True
        if removed and self._windows:
            self._update_slaves()

    def cleanup(self):
        """清理所有资源。"""
        self.uninstall_hooks()

    # ── 内部方法 ─────────────────────────────────────────────────

    def _notify(self, status: str):
        """将状态消息放入线程安全队列（可由任意线程调用）。"""
        self._notify_queue.put(("status", status))

    def _emit_debug(self):
        """每秒输出一次调试信息。"""
        now = time.time()
        if now - self._last_debug_time >= 1.0:
            self._last_debug_time = now
            info = (
                f"kb={self._kb_count} "
                f"m_fired={self._mouse_fired} "
                f"m_act={self._mouse_action} "
                f"m_nonmove={self._mouse_nonmove} "
                f"m_rej={self._mouse_rejected} "
                f"m_fwd={self._mouse_forwarded}"
            )
            self._notify_queue.put(("debug", info))

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
    def is_running(self) -> bool:
        return self._running