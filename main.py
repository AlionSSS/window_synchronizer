"""多游戏窗口同步器 - GUI 主界面。"""

import tkinter as tk
from tkinter import ttk, messagebox
from sync_engine import SyncEngine, WindowInfo
import theme

import sys
import os

def resource_path(relative_path):
    """获取资源的正确路径，兼容开发环境和 PyInstaller 打包后的环境"""
    try:
        # PyInstaller 在运行时会将所有资源文件“存放”在这个路径指向的目录下
        # 对于 -D 模式，这会是 {exe所在目录}/_internal
        base_path = sys._MEIPASS  # pyright: ignore[reportAttributeAccessIssue]
    except AttributeError:
        # 如果不是在 PyInstaller 打包后的环境中运行（比如直接运行 .py 文件）
        base_path = os.path.abspath(".")

    # 将目标子目录（例如 "resources"）拼接到 base_path 后面
    return os.path.join(base_path, relative_path)

class WindowSyncApp:
    """窗口同步器主应用。"""

    HOTKEY_ID = 1

    def __init__(self):
        self.engine = SyncEngine()

        self._skip_single_click = False

        self.root = tk.Tk()
        self.root.title("多游戏窗口同步器 v0.1.0 By: 菠萝包 QQ444066154")
        self.root.geometry("700x500")
        self.root.minsize(500, 350)
        self.root.iconbitmap(resource_path(os.path.join("resources", "icon.ico")))

        theme.configure_theme(self.root)
        self.root.configure(bg=theme.COLOR_BG_WINDOW)

        self._build_ui()
        self._refresh_window_list()

        # 注册全局热键
        self.root.after(100, self._register_hotkey)

        # 窗口关闭时清理
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI 构建 ───────────────────────────────────────────────────

    def _build_ui(self):
        """构建 GUI 布局。"""
        # ── 顶部工具栏 ──
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Button(toolbar, text="刷新窗口列表", command=self._refresh_window_list).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(toolbar, text="设为主控", command=self._set_master).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        self.select_all_btn = ttk.Button(
            toolbar, text="全选受控", command=self._select_all_slaves
        )
        self.select_all_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.deselect_all_btn = ttk.Button(
            toolbar, text="取消全选", command=self._deselect_all_slaves
        )
        self.deselect_all_btn.pack(side=tk.LEFT, padx=(0, 4))

        # ── 窗口列表区域 ──
        list_frame = ttk.LabelFrame(self.root, text="桌面窗口列表")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Treeview: 勾选 | 角色 | 进程 | 窗口标题 | 句柄
        columns = ("checked", "role", "process", "title", "hwnd")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse"
        )
        self.tree.heading("checked", text="同步")
        self.tree.heading("role", text="角色")
        self.tree.heading("process", text="进程")
        self.tree.heading("title", text="窗口标题")
        self.tree.heading("hwnd", text="句柄")

        self.tree.column("checked", width=40, anchor=tk.CENTER, stretch=False)
        self.tree.column("role", width=60, anchor=tk.CENTER, stretch=False)
        self.tree.column("process", width=100, anchor=tk.W, stretch=False)
        self.tree.column("title", width=350)
        self.tree.column("hwnd", width=80, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 单击"同步"列切换受控状态，双击任意列也可切换
        self.tree.bind("<ButtonRelease-1>", self._on_checkbox_click)
        self.tree.bind("<Double-1>", self._on_double_click)

        # ── 同步控制区域 ──
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=8, pady=4)

        self.sync_btn = ttk.Button(
            control_frame, text="开始同步", command=self._toggle_sync, width=12
        )
        self.sync_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.status_label = ttk.Label(
            control_frame, text="已停止",
            foreground=theme.COLOR_TEXT_SECONDARY, style="Status.TLabel"
        )
        self.status_label.pack(side=tk.LEFT, padx=(0, 16))

        self.count_label = ttk.Label(control_frame, text="主控: 0  |  受控: 0")
        self.count_label.pack(side=tk.LEFT)

        ttk.Label(
            control_frame, text="热键: Ctrl+Shift+S",
            foreground=theme.COLOR_TEXT_SECONDARY, style="Secondary.TLabel"
        ).pack(
            side=tk.RIGHT
        )

        # ── 底部状态栏 ──
        status_bar = ttk.Frame(self.root, style="Surface.TFrame")
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_bar_label = ttk.Label(status_bar, text="就绪")
        self.status_bar_label.pack(side=tk.LEFT, padx=4, pady=2)

    # ── 窗口列表操作 ─────────────────────────────────────────────

    def _refresh_window_list(self):
        """刷新窗口列表（保留主控和受控状态）。"""
        # 保存当前主控和受控状态
        old_master = None
        old_slaves = set()
        for item in self.tree.get_children():
            values = self.tree.item(item, "values")
            hwnd = int(values[4])
            if values[1] == "主控":
                old_master = hwnd
            if values[0] == "☑":
                old_slaves.add(hwnd)

        # 重新枚举
        self.engine.enumerate_windows()

        # 仅恢复主控和手动设置的受控状态（不自动将所有窗口设为受控）
        if old_master:
            self.engine.set_master(old_master)
        for hwnd in old_slaves:
            self.engine.toggle_slave(hwnd, True)

        # 刷新显示
        self._refresh_list_display()
        self.status_bar_label.config(
            text=f"已枚举 {len(self.engine.get_windows())} 个可见窗口"
        )

    def _insert_window_item(self, win: WindowInfo):
        """插入单行窗口条目。"""
        checked = ""
        if win.is_master:
            role = "主控"
        elif win.is_slave:
            checked = "☑"
            role = "受控"
        else:
            checked = "☐"
            role = ""

        item = self.tree.insert(
            "",
            tk.END,
            values=(checked, role, win.process_name, win.title, str(win.hwnd)),
            tags=(role.lower(),),
        )

        # 设置角色颜色
        if win.is_master:
            self.tree.item(item, tags=("master",))
        elif win.is_slave:
            self.tree.item(item, tags=("slave",))

    def _set_master(self):
        """将选中窗口设为主控。"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先在列表中选择一个窗口")
            return
        values = self.tree.item(selection[0], "values")
        hwnd = int(values[4])
        self.engine.set_master(hwnd)
        self._refresh_list_display()

    def _select_all_slaves(self):
        """将所有窗口设为受控（除主控外）。"""
        master = self._get_master_hwnd()
        windows = self.engine.get_windows()
        for win in windows:
            if win.hwnd != master:
                win.is_slave = True
        self._refresh_list_display()

    def _deselect_all_slaves(self):
        """取消所有受控窗口。"""
        for win in self.engine.get_windows():
            if not win.is_master:
                win.is_slave = False
        self._refresh_list_display()

    def _on_double_click(self, event):
        """双击切换受控状态。"""
        self._skip_single_click = True
        item = self.tree.identify_row(event.y)
        if not item:
            return
        values = self.tree.item(item, "values")
        hwnd = int(values[4])
        is_checked = values[0] == "☑"
        self.engine.toggle_slave(hwnd, not is_checked)
        self._refresh_list_display()

    def _on_checkbox_click(self, event):
        """单击'同步'列切换受控状态。"""
        if self._skip_single_click:
            self._skip_single_click = False
            return
        column = self.tree.identify_column(event.x)
        if column != "#1":  # #1 = 第一列（同步列）
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        values = self.tree.item(item, "values")
        hwnd = int(values[4])
        is_checked = values[0] == "☑"
        self.engine.toggle_slave(hwnd, not is_checked)
        self._refresh_list_display()

    def _refresh_list_display(self):
        """刷新列表显示（不重新枚举窗口）。"""
        self._validate_and_cleanup()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for win in self.engine.get_windows():
            self._insert_window_item(win)
        self._update_counts()

    def _validate_and_cleanup(self):
        """验证窗口有效性。"""
        self.engine._validate_windows()
        # 主控窗口关闭时更新 UI
        if self.engine._get_master() is None and self.engine.is_running:
            self.engine.uninstall_hooks()
            self._update_sync_button()

    def _get_master_hwnd(self) -> int | None:
        master = self.engine._get_master()
        return master.hwnd if master else None

    def _update_counts(self):
        """更新计数显示。"""
        master_count = sum(1 for w in self.engine.get_windows() if w.is_master)
        slave_count = sum(1 for w in self.engine.get_windows() if w.is_slave)
        self.count_label.config(text=f"主控: {master_count}  |  受控: {slave_count}")

    # ── 同步控制 ─────────────────────────────────────────────────

    def _toggle_sync(self):
        """切换同步状态。"""
        if self.engine.is_running:
            self.engine.uninstall_hooks()
        else:
            if not self.engine.install_hooks():
                return
        self._update_sync_button()

    def _update_sync_button(self):
        """更新同步按钮和状态标签。"""
        running = self.engine.is_running
        if running:
            self.sync_btn.config(
                text="停止同步", style="Danger.TButton"
            )
            self.status_label.config(
                text="同步中", foreground=theme.COLOR_SUCCESS
            )
        else:
            self.sync_btn.config(
                text="开始同步", style="TButton"
            )
            self.status_label.config(
                text="已停止", foreground=theme.COLOR_TEXT_SECONDARY
            )
        state = tk.DISABLED if running else tk.NORMAL
        self.select_all_btn.config(state=state)
        self.deselect_all_btn.config(state=state)

    def _poll_notifications(self):
        """轮询引擎通知队列（主线程安全）。"""
        items = self.engine.get_notifications()
        for kind, msg in items:
            if kind == "status":
                self._update_sync_button()
                self.status_bar_label.config(text=msg)
            elif kind == "debug":
                self.status_bar_label.config(text=msg)
        self.root.after(100, self._poll_notifications)

    # ── 热键处理 ─────────────────────────────────────────────────

    def _register_hotkey(self):
        """注册全局热键。"""
        self.engine.register_hotkey(int(self.root.frame(), 16))

    # ── 窗口关闭 ─────────────────────────────────────────────────

    def _on_close(self):
        """关闭窗口时的清理。"""
        self.engine.unregister_hotkey(int(self.root.frame(), 16))
        self.engine.cleanup()
        if self.root:
            self.root.destroy()

    def run(self):
        """运行主循环。"""
        self.tree.tag_configure(
            "master", background=theme.COLOR_TREE_MASTER_BG, foreground=theme.COLOR_TEXT
        )
        self.tree.tag_configure(
            "slave", background=theme.COLOR_TREE_SLAVE_BG, foreground=theme.COLOR_TEXT
        )
        self.root.after(200, self._poll_notifications)
        self.root.mainloop()


def main():
    app = WindowSyncApp()
    app.run()


if __name__ == "__main__":
    main()