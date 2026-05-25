"""莫兰迪蓝色主题 — 颜色常量、ttk 样式配置与 WCAG 辅助工具。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ═══════════════════════════════════════════════════════════════════
#  基础色板
# ═══════════════════════════════════════════════════════════════════

# 背景
COLOR_BG_WINDOW = "#EDF2F7"          # 窗口底色（浅灰蓝）
COLOR_BG_SURFACE = "#FFFFFF"          # 卡片 / 框架底色
COLOR_BG_SURFACE_ALT = "#F7FAFC"     # 框架备选底色（微蓝白）

# 主题蓝（莫兰迪）
COLOR_PRIMARY = "#4A6C95"            # 主色（中灰蓝，白字 ≥4.5:1）
COLOR_PRIMARY_DARK = "#3A587F"       # 主色深（悬停 / 按压）
COLOR_PRIMARY_LIGHT = "#759CC0"      # 主色浅
COLOR_PRIMARY_PALE = "#D4E3F0"       # 主色极浅（背景铺底）

# 文字
COLOR_TEXT = "#1E2D3D"               # 主文字（深藏青）
COLOR_TEXT_SECONDARY = "#486078"     # 次要文字
COLOR_TEXT_DISABLED = "#788A9C"      # 禁用文字（WCAG 豁免非活跃组件）
COLOR_TEXT_ON_PRIMARY = "#FFFFFF"    # 主色底上的文字

# 状态色
COLOR_SUCCESS = "#48755B"            # 成功 / 同步中（灰调绿，≥4.5:1）
COLOR_DANGER = "#8C5C3E"             # 危险 / 警告（灰调暖，≥4.5:1）
COLOR_WARNING = "#7A6A2E"            # 警告（灰调黄，白字 ≥4.5:1）

# 边框
COLOR_BORDER = "#D0DAE3"             # 常规边框
COLOR_BORDER_DARK = "#A8B8C8"        # 加深边框
COLOR_BORDER_FOCUS = "#6B8EAD"       # 聚焦边框（同主色）

# 树状列表角色行
COLOR_TREE_MASTER_BG = "#8DB5D0"     # 主控行背景（较饱和蓝色，突出显示）
COLOR_TREE_SLAVE_BG = "#E5ECF3"      # 受控行背景（极浅蓝灰）
COLOR_TREE_SELECTED_BG = "#A8C5DE"   # 选中行背景

# ═══════════════════════════════════════════════════════════════════
#  字体
# ═══════════════════════════════════════════════════════════════════

FONT_DEFAULT = ("Microsoft YaHei UI", 9)
FONT_SMALL = ("Microsoft YaHei UI", 8)
FONT_MONO = ("Consolas", 9)

# ═══════════════════════════════════════════════════════════════════
#  间距
# ═══════════════════════════════════════════════════════════════════

PAD_SMALL = 4
PAD_MEDIUM = 8
PAD_LARGE = 12

# ═══════════════════════════════════════════════════════════════════
#  WCAG 对比度计算
# ═══════════════════════════════════════════════════════════════════


def _relative_luminance(hex_color: str) -> float:
    """计算 sRGB 颜色的相对亮度 (WCAG 2.1)。"""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0

    def _linearize(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """返回两个颜色之间的 WCAG 对比度。"""
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def check_wcag_aa(fg: str, bg: str) -> str:
    """检查一对颜色是否满足 WCAG AA 并返回结果描述。"""
    cr = contrast_ratio(fg, bg)
    normal = "PASS" if cr >= 4.5 else "FAIL"
    large = "PASS" if cr >= 3.0 else "FAIL"
    return f"{fg} on {bg}  →  {cr:.2f}:1  (normal: {normal}, large: {large})"


# ═══════════════════════════════════════════════════════════════════
#  ttk 样式配置
# ═══════════════════════════════════════════════════════════════════


def configure_theme(root: tk.Tk) -> None:
    """在 root 上应用莫兰迪蓝色 ttk 主题。"""

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass  # 回退到系统默认

    # ── 全局默认 ──
    style.configure(
        ".",
        background=COLOR_BG_WINDOW,
        foreground=COLOR_TEXT,
        font=FONT_DEFAULT,
        borderwidth=0,
    )

    # ── TFrame ──
    style.configure(
        "TFrame",
        background=COLOR_BG_WINDOW,
    )

    # ── 表面级 Frame（用于卡片 / LabelFrame 内部）──
    style.configure(
        "Surface.TFrame",
        background=COLOR_BG_SURFACE,
    )

    # ── TLabel ──
    style.configure(
        "TLabel",
        background=COLOR_BG_WINDOW,
        foreground=COLOR_TEXT,
        font=FONT_DEFAULT,
    )
    style.configure(
        "Secondary.TLabel",
        foreground=COLOR_TEXT_SECONDARY,
    )
    style.configure(
        "Status.TLabel",
        background=COLOR_BG_WINDOW,
        foreground=COLOR_TEXT,
        font=FONT_DEFAULT,
    )

    # ── TButton ──
    style.configure(
        "TButton",
        background=COLOR_PRIMARY,
        foreground=COLOR_TEXT_ON_PRIMARY,
        font=FONT_DEFAULT,
        borderwidth=0,
        padding=(PAD_LARGE, PAD_SMALL),
        relief="flat",
        anchor="center",
    )
    style.map(
        "TButton",
        background=[
            ("active", COLOR_PRIMARY_DARK),
            ("pressed", COLOR_PRIMARY_DARK),
            ("disabled", COLOR_PRIMARY_PALE),
        ],
        foreground=[
            ("active", COLOR_TEXT_ON_PRIMARY),
            ("pressed", COLOR_TEXT_ON_PRIMARY),
            ("disabled", COLOR_TEXT_DISABLED),
        ],
    )

    # ── Danger.TButton（同步中-停止按钮）──
    style.configure(
        "Danger.TButton",
        background=COLOR_DANGER,
    )
    style.map(
        "Danger.TButton",
        background=[
            ("active", "#6C452E"),
            ("pressed", "#6C452E"),
        ],
    )

    # ── TLabelframe ──
    style.configure(
        "TLabelframe",
        background=COLOR_BG_SURFACE,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER,
        borderwidth=1,
        relief="solid",
        padding=(PAD_MEDIUM, PAD_MEDIUM),
    )
    style.configure(
        "TLabelframe.Label",
        background=COLOR_BG_SURFACE,
        foreground=COLOR_TEXT_SECONDARY,
        font=FONT_SMALL,
    )

    # ── Treeview ──
    style.configure(
        "Treeview",
        background=COLOR_BG_SURFACE,
        foreground=COLOR_TEXT,
        fieldbackground=COLOR_BG_SURFACE,
        borderwidth=0,
        font=FONT_DEFAULT,
        rowheight=26,
    )
    style.configure(
        "Treeview.Heading",
        background=COLOR_BG_SURFACE_ALT,
        foreground=COLOR_TEXT_SECONDARY,
        font=FONT_SMALL,
        borderwidth=0,
        relief="flat",
        padding=(PAD_SMALL, 2),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", COLOR_PRIMARY_PALE)],
        foreground=[("active", COLOR_TEXT)],
    )
    style.map(
        "Treeview",
        background=[("selected", COLOR_TREE_SELECTED_BG)],
        foreground=[("selected", COLOR_TEXT)],
    )

    # ── 垂直滚动条 ──
    style.configure(
        "Vertical.TScrollbar",
        background=COLOR_BG_SURFACE_ALT,
        troughcolor=COLOR_BG_WINDOW,
        bordercolor=COLOR_BORDER,
        arrowcolor=COLOR_TEXT_SECONDARY,
        borderwidth=0,
        relief="flat",
        width=10,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", COLOR_BORDER), ("pressed", COLOR_BORDER_DARK)],
    )

    # ── 分隔线 ──
    style.configure("TSeparator", background=COLOR_BORDER)