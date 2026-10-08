"""PixelRAG 桌面客户端设计令牌 —— 方向 A「Notion 纸感」。

这是全项目唯一的"样式表"。所有颜色 / 字号 / 间距 / 圆角 / 动效时长都必须
从这里取,业务代码里不允许再出现硬编码色值或魔法数字。

浅色(light)为默认主题,深色(dark)是等价反转的备选主题。
两个色板必须拥有完全相同的键,否则切换时会 KeyError。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 色板
# ---------------------------------------------------------------------------
PALETTES: dict[str, dict[str, str]] = {
    # 取色原则:正文与提示文字对底色的对比度需 ≥ 4.5:1(WCAG AA 普通文本)。
    # 括号里是对"文字落到自己常见底色上"的实测对比度,改动色值时请一并复核。
    # ---- 浅色:Notion 纸感(默认)----
    "light": {
        "bg": "#FFFFFF",  # 页面底色
        "surface": "#F7F7F5",  # 侧栏 / 顶栏 / 底栏
        "card": "#FFFFFF",  # 卡片表面
        "card_hover": "#FAFAF9",  # 卡片 hover
        "border": "#E9E9E7",  # 常规分隔线
        "border_strong": "#D3D1CB",  # hover / focus 时的边框
        "text": "#37352F",  # 正文(14.9:1 on bg)
        "text_muted": "#6B6A67",  # 次要信息(5.4:1 on bg)
        "text_faint": "#767571",  # 极淡提示(4.6:1 on bg)
        "accent": "#1A73CC",  # 主色(4.8:1 on bg;白字落其上同为 4.8:1)
        "accent_hover": "#155FB0",
        "accent_soft": "#E7F1FB",  # 主色淡底(hover 态底,非正文底)
        "on_accent": "#FFFFFF",  # 主色之上的文字
        "user_bubble": "#F1F1EF",  # 用户消息淡灰块
        "user_text": "#37352F",  # 用户消息文字
        "code_bg": "#F7F6F3",  # 行内代码 / 代码块底
        "success": "#0F7B6C",  # 5.2:1 on bg
        "warning": "#8A6100",  # 5.5:1 on bg
        "danger": "#C0392B",  # 5.0:1 on danger_bg
        "danger_bg": "#FDF2F2",  # 错误提示底
        "input_bg": "#FFFFFF",
        "input_line": "#E9E9E7",  # 输入框底部 1px 线(未聚焦)
        "focus": "#1A73CC",  # 键盘焦点环
        "scrollbar": "#D3D1CB",
        "skeleton": "#F1F1EF",  # 加载骨架
    },
    # ---- 深色:等价反转 ----
    "dark": {
        "bg": "#191919",
        "surface": "#202020",
        "card": "#252525",
        "card_hover": "#2C2C2C",
        "border": "#333331",
        "border_strong": "#4A4A47",
        "text": "#E6E6E4",
        "text_muted": "#A8A7A3",
        "text_faint": "#8F8E8B",
        "accent": "#5B9BE8",
        "accent_hover": "#7CB0F0",
        "accent_soft": "#1E2A3A",
        "on_accent": "#0F1419",  # 深色底上主色偏亮,配深墨字才够对比(6.4:1)
        "user_bubble": "#2F2F2E",
        "user_text": "#E6E6E4",
        "code_bg": "#202020",
        "success": "#4ADE80",
        "warning": "#FACC15",
        "danger": "#F87171",
        "danger_bg": "#3A1D1D",
        "input_bg": "#202020",
        "input_line": "#333331",
        "focus": "#5B9BE8",
        "scrollbar": "#4A4A47",
        "skeleton": "#2F2F2E",
    },
}

DEFAULT_MODE = "light"

_mode = DEFAULT_MODE


def mode() -> str:
    """当前主题名(light / dark)。"""
    return _mode


def set_mode(name: str) -> str:
    """切换主题并返回生效的主题名。"""
    global _mode
    if name not in PALETTES:
        raise ValueError(f"未知主题:{name!r}(可选:{sorted(PALETTES)})")
    _mode = name
    return _mode


def other_mode() -> str:
    return "dark" if _mode == "light" else "light"


def c(key: str) -> str:
    """按语义键取当前主题色。"""
    try:
        return PALETTES[_mode][key]
    except KeyError:
        raise KeyError(f"色板 {_mode!r} 中没有键 {key!r}") from None


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------
FONT_FAMILY = "Microsoft YaHei UI"  # 中文优先;缺失时 tk 会自行回退
FONT_MONO = "Consolas"

# token -> (字号 pt, 字重)。方向 A 的层级:20 / 16 / 15 / 14 / 12 / 11 px,
# 换算成 tk 的点值并整体略放大以保证中文可读性。
TYPE: dict[str, tuple[int, str]] = {
    "title": (15, "bold"),  # 顶栏标题
    "h2": (12, "bold"),  # 区块标题 / 引用来源
    "body": (11, "normal"),  # 正文
    "body_lg": (12, "normal"),  # 输入框
    "label": (11, "bold"),  # 卡片标题
    "button": (11, "bold"),  # 按钮
    "caption": (9, "normal"),  # 卡片元信息
    "micro": (8, "normal"),  # 角标
}


# 全局字号倍率:**嫌字小就调大这一个数**(1.15 ≈ 正文 11→12.6pt、标题 15→17pt)。
# tk 绘制与 Pillow 离屏绘制都从这里换算,保证两边的字一样大。
FONT_SCALE = 1.15


def pt(token: str) -> int:
    """取某个 token 的实际字号(点值,已乘 FONT_SCALE)。

    tk 的字体元组只接受整数点值,所以在这里就取整;Pillow 那边也从这里换算,
    保证界面上两种绘制方式的字一样大。
    """
    return max(1, round(TYPE[token][0] * FONT_SCALE))


def font(token: str) -> tuple:
    """取 tk 字体元组,如 ('Microsoft YaHei UI', 12.6, 'normal')。"""
    return (FONT_FAMILY, pt(token), TYPE[token][1])


def font_mono(size: int = 10) -> tuple:
    """等宽字体(代码块)。``size`` 是设计字号,同样会乘 FONT_SCALE。"""
    return (FONT_MONO, max(1, round(size * FONT_SCALE)), "normal")


# ---------------------------------------------------------------------------
# 间距(4px 基准) / 圆角 / 动效
# ---------------------------------------------------------------------------
_BASE_SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "2xl": 32}
_BASE_RADIUS = {"sm": 6, "md": 8, "lg": 12}
_BASE_CONTENT_MAX_WIDTH = 720

# 下面三个是"逻辑值 × UI_SCALE"的结果;启动时由 set_ui_scale 按屏幕 DPI 重算,
# 以便在高分屏上字变大的同时,内边距/圆角/行宽等比跟上,不会显得局促。
SPACE: dict[str, int] = dict(_BASE_SPACE)
RADIUS: dict[str, int] = dict(_BASE_RADIUS)
CONTENT_MAX_WIDTH = _BASE_CONTENT_MAX_WIDTH
UI_SCALE = 1.0


def set_ui_scale(factor: float) -> float:
    """按屏幕缩放比例重算所有像素级令牌(0.75x ~ 2.0x)。"""
    global UI_SCALE, CONTENT_MAX_WIDTH
    UI_SCALE = max(0.75, min(2.0, float(factor)))
    for k, v in _BASE_SPACE.items():
        SPACE[k] = max(1, round(v * UI_SCALE))
    for k, v in _BASE_RADIUS.items():
        RADIUS[k] = max(2, round(v * UI_SCALE))
    CONTENT_MAX_WIDTH = round(_BASE_CONTENT_MAX_WIDTH * UI_SCALE)
    return UI_SCALE


# 动效时长(毫秒)。tkinter 无 CSS,统一用 root.after 驱动。
MOTION: dict[str, int] = {
    "fast": 120,
    "base": 180,
    "stream_throttle": 60,  # 流式重绘节流间隔
    "resize_debounce": 120,  # 窗口尺寸变化防抖
}

# 正文最大宽度(px):超过则两侧留白,保证长文阅读行宽舒适
CONTENT_MAX_WIDTH = 720
