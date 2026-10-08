"""查询历史:落盘存储 + 左侧历史栏 + 输入框上下键召回。

历史写在用户目录下的 ``~/.pixelrag/history.json``(不放仓库里,免得污染项目),
一条记录 = 时间 / 问题 / 命中页数 / 答案 / 引用页。存储只做增删查,不涉及任何检索逻辑。

答案与引用页都存下来,所以点开一条历史 = 直接还原当时那轮问答,不必重跑模型。

界面是主窗口左侧的一栏(DeepSeek 那种排版),不是弹窗 —— 弹窗会挡住内容,
而看历史本来就是为了对照着看。
"""

from __future__ import annotations

import json
import os
import time
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from datetime import datetime, timezone

import pixelrag_theme as T
import pixelrag_widgets as W

HISTORY_PATH = os.path.join(os.path.expanduser("~"), ".pixelrag", "history.json")
MAX_ITEMS = 200  # 只留最近这么多条
REUSE_WINDOW = 300  # 秒;同一个问题在这段时间内重复提交(含"重新生成")算同一条
SIDEBAR_W = 260  # 侧栏逻辑宽度(会按屏幕 DPI 缩放)


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


class History:
    """查询历史。读写都容错 —— 历史存不下来不应该影响问答。"""

    def __init__(self, path: str | None = None):
        # 取调用时的模块级常量(而不是把它当默认参数固化),改 HISTORY_PATH 能生效
        self.path = path or HISTORY_PATH
        self._items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict) and d.get("question")]

    def items(self) -> list[dict]:
        """新的在前(侧栏按这个顺序显示)。"""
        return list(reversed(self._items))

    def questions(self) -> list[str]:
        """问题列表,新的在前(输入框 ↑/↓ 用)。"""
        return [d["question"] for d in self.items()]

    def remember(self, question: str) -> dict:
        """记一条提问。刚问过的同一个问题(比如点"重新生成")复用原条目。"""
        now = time.time()
        if self._items:
            last = self._items[-1]
            if (
                last.get("question") == question
                and now - float(last.get("ts") or 0) < REUSE_WINDOW
            ):
                last["time"] = _iso(now)
                last["ts"] = now
                self._save()
                return last
        item = {
            "question": question,
            "time": _iso(now),
            "ts": now,
            "hits": None,
            "answer": "",
            "refs": [],
        }
        self._items.append(item)
        if len(self._items) > MAX_ITEMS:
            del self._items[:-MAX_ITEMS]
        self._save()
        return item

    def update(
        self,
        item: dict | None,
        hits: int | None = None,
        answer: str | None = None,
        refs: list[dict] | None = None,
    ):
        """问答结束后回填命中页数、答案与引用页。"""
        if not item:
            return
        if hits is not None:
            item["hits"] = int(hits)
        if answer is not None:
            item["answer"] = answer
        if refs is not None:
            item["refs"] = list(refs)
        self._save()

    def clear(self) -> None:
        self._items = []
        self._save()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)  # 原子替换,写一半断电也不会留半截文件
        except Exception:
            pass


def _iso(ts: float) -> str:
    """带时区偏移的 ISO 时间戳(换时区/夏令时也不会看错)。"""
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )


def _pretty(iso: str) -> str:
    """显示成本地时间。统一用 ``MM-DD HH:MM``:每行等宽,看起来才齐。"""
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except Exception:
        return ""
    return dt.strftime("%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 左侧历史栏
# ---------------------------------------------------------------------------


def _elide(text: str, font_spec: tuple, max_px: int) -> str:
    """单行截断:太长就砍到放得下再加省略号(侧栏一行只放得下一个问题)。"""
    f = tkfont.Font(font=font_spec)
    text = " ".join(text.split())  # 换行/多余空白压成单空格,免得标签里折行
    if f.measure(text) <= max_px:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if f.measure(text[:mid] + "…") <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


class HistorySidebar(tk.Frame):
    """主窗口左侧的历史栏:新对话 / 历史列表 / 清空。

    - 左键点一条 = ``on_open(item)``,由调用方还原那轮问答
    - 行尾「重问」= ``on_reask(question)``,用同一个问题重新检索生成
    - 右键 = 打开 / 重问 / 复制问题 / 复制答案
    """

    def __init__(
        self,
        parent,
        history: History,
        on_open: Callable[[dict], None],
        on_reask: Callable[[str], None] | None = None,
        on_new: Callable[[], None] | None = None,
        on_clear: Callable[[], None] | None = None,
    ):
        super().__init__(parent, bg=T.c("surface"), width=int(SIDEBAR_W * T.UI_SCALE))
        self.pack_propagate(False)  # 固定宽度,别被里面的长文本撑开
        self.history = history
        self.on_open = on_open
        self.on_reask = on_reask
        self.on_new = on_new
        self.on_clear = on_clear
        self._current: dict | None = None
        # 一行放问题的宽度:去掉左右留白和行尾「重问」的位置
        self._text_w = max(
            80,
            int(SIDEBAR_W * T.UI_SCALE) - 3 * T.SPACE["md"] - int(34 * T.UI_SCALE),
        )
        self._build()
        self.refresh()

    # ---------- 骨架 ----------

    def _build(self):
        top = tk.Frame(self, bg=T.c("surface"))
        top.pack(side="top", fill="x", padx=T.SPACE["sm"], pady=T.SPACE["sm"])
        self.new_btn = tk.Label(
            top,
            text="＋   新对话",
            bg=T.c("surface"),
            fg=T.c("accent"),
            font=T.font("button"),
            anchor="w",
            cursor="hand2",
            padx=T.SPACE["sm"],
            pady=T.SPACE["sm"],
        )
        self.new_btn.pack(fill="x")
        self.new_btn.bind("<Button-1>", lambda _e: self._new())
        W.bind_hover(
            self.new_btn,
            on_enter=lambda: self.new_btn.configure(bg=T.c("accent_soft")),
            on_leave=lambda: self.new_btn.configure(bg=T.c("surface")),
        )
        W.make_focusable(self.new_btn, T.c("surface"), self._new)
        tk.Frame(self, bg=T.c("border"), height=1).pack(side="top", fill="x")

        head = tk.Frame(self, bg=T.c("surface"))
        head.pack(side="top", fill="x", padx=T.SPACE["md"], pady=(T.SPACE["md"], 0))
        tk.Label(
            head,
            text="查询历史",
            bg=T.c("surface"),
            fg=T.c("text_faint"),
            font=T.font("caption"),
        ).pack(side="left")
        self.count_lbl = tk.Label(
            head,
            text="",
            bg=T.c("surface"),
            fg=T.c("text_faint"),
            font=T.font("micro"),
        )
        self.count_lbl.pack(side="right")

        # 先 pack 底部,再让列表吃掉剩下的空间 —— 否则底部会被挤出去
        foot = tk.Frame(self, bg=T.c("surface"))
        foot.pack(side="bottom", fill="x")
        tk.Frame(foot, bg=T.c("border"), height=1).pack(side="top", fill="x")
        self.clear_btn = tk.Label(
            foot,
            text="清空历史",
            bg=T.c("surface"),
            fg=T.c("text_faint"),
            font=T.font("caption"),
            anchor="w",
            cursor="hand2",
            padx=T.SPACE["sm"],
            pady=T.SPACE["sm"],
        )
        self.clear_btn.pack(fill="x", padx=T.SPACE["sm"], pady=T.SPACE["xs"])
        self.clear_btn.bind("<Button-1>", lambda _e: self._clear())
        W.bind_hover(
            self.clear_btn,
            on_enter=lambda: self.clear_btn.configure(fg=T.c("danger")),
            on_leave=lambda: self.clear_btn.configure(fg=T.c("text_faint")),
        )
        W.make_focusable(self.clear_btn, T.c("surface"), self._clear)

        body = tk.Frame(self, bg=T.c("surface"))
        body.pack(side="top", fill="both", expand=True, pady=(T.SPACE["xs"], 0))
        self.canvas = tk.Canvas(body, bg=T.c("surface"), highlightthickness=0, bd=0)
        self.scroll = tk.Scrollbar(
            body,
            orient="vertical",
            command=self.canvas.yview,
            bg=T.c("scrollbar"),
            troughcolor=T.c("surface"),
            activebackground=T.c("border_strong"),
            borderwidth=0,
            width=10,
            relief="flat",
            elementborderwidth=0,
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.list = tk.Frame(self.canvas, bg=T.c("surface"))
        self._win = self.canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.list.bind("<Configure>", self._on_list_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        for w in (self, self.canvas, self.list):
            w.bind("<MouseWheel>", self._on_wheel, add="+")

    def _on_list_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_scrollbar()

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        self._sync_scrollbar()

    def _sync_scrollbar(self):
        """列表放得下就收起滚动条 —— 空列表边上杵一根槽看着像坏了。

        重新 pack 时必须 ``before=self.canvas``:pack 顺序代表"谁先占位置",
        追加到末尾的话滚动条会被撑满的画布挤成 0 宽。
        """
        try:
            need = self.list.winfo_reqheight() > self.canvas.winfo_height() + 1
            shown = bool(self.scroll.winfo_ismapped())
        except tk.TclError:
            return
        if need and not shown:
            self.scroll.pack(side="right", fill="y", before=self.canvas)
        elif not need and shown:
            self.scroll.pack_forget()

    def _on_wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120) * 2, "units")
        except Exception:
            pass

    # ---------- 列表 ----------

    def refresh(self):
        """整表重绘。条数上限 200,重建的开销可以忽略。"""
        for w in self.list.winfo_children():
            w.destroy()
        # 请求高度要等布局算完才准,所以放到空闲时再决定滚动条要不要露面
        self.canvas.after_idle(self._sync_scrollbar)
        items = self.history.items()
        self.count_lbl.configure(text=f"{len(items)} 条" if items else "")
        if not items:
            tk.Label(
                self.list,
                text="还没有查询记录。\n问过的问题会记在这里,\n关掉程序也不会丢。",
                bg=T.c("surface"),
                fg=T.c("text_faint"),
                font=T.font("caption"),
                justify="left",
                anchor="w",
                wraplength=int(SIDEBAR_W * T.UI_SCALE) - 2 * T.SPACE["md"],
            ).pack(anchor="w", fill="x", padx=T.SPACE["md"], pady=T.SPACE["md"])
            return
        for it in items:
            self._row(it)

    def set_current(self, item: dict | None):
        """高亮"当前打开的那一轮问答",并整表重绘(顺带并入新记的一条)。"""
        self._current = item
        self.refresh()

    def _row(self, item: dict):
        cur = item is self._current
        base = T.c("accent_soft") if cur else T.c("surface")
        row = tk.Frame(self.list, bg=base)
        row.pack(fill="x", padx=T.SPACE["xs"], pady=1)

        # 行尾「重问」:固定宽度占位,平时留空 —— 显隐时不会挤动左边的问题
        ask = tk.Label(
            row,
            text="重问" if (cur and self.on_reask) else "",
            width=4,
            bg=base,
            fg=T.c("accent"),
            font=T.font("micro"),
            cursor="hand2",
            anchor="e",
        )
        ask.pack(side="right", padx=(0, T.SPACE["sm"]))
        if self.on_reask:
            ask.bind("<Button-1>", lambda _e, q=item["question"]: self._reask(q))

        box = tk.Frame(row, bg=base)
        box.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(T.SPACE["sm"], 0),
            pady=T.SPACE["xs"],
        )
        tk.Label(
            box,
            text=_elide(item["question"], T.font("side"), self._text_w),
            bg=base,
            fg=T.c("accent") if cur else T.c("text"),
            font=T.font("side"),
            anchor="w",
            justify="left",
        ).pack(anchor="w", fill="x")
        n = item.get("hits")
        meta = _pretty(item.get("time", ""))
        meta += f" · {n} 页" if n is not None else " · 未完成"
        if item.get("answer"):
            meta += " · 有答案"
        tk.Label(
            box,
            text=meta,
            bg=base,
            fg=T.c("text_faint"),
            font=T.font("micro"),
            anchor="w",
            justify="left",
        ).pack(anchor="w", fill="x")

        def enter(_e=None):
            if item is not self._current:
                W.set_bg_recursive(row, T.c("card_hover"))
            if self.on_reask:
                ask.configure(text="重问")

        def leave(_e=None):
            if item is not self._current:
                W.set_bg_recursive(row, base)
            if not (item is self._current):
                ask.configure(text="")

        W.bind_hover(row, on_enter=enter, on_leave=leave)
        W.bind_click_recursive(box, lambda i=item: self._open(i))
        W.set_cursor_recursive(box)
        W.make_focusable(row, T.c("surface"), lambda i=item: self._open(i))

        menu = tk.Menu(row, tearoff=0, font=T.font("caption"))
        menu.add_command(label="打开这轮问答", command=lambda i=item: self._open(i))
        if self.on_reask:
            menu.add_command(
                label="用这个问题重问",
                command=lambda q=item["question"]: self._reask(q),
            )
        menu.add_separator()
        menu.add_command(
            label="复制问题", command=lambda t=item["question"]: self._copy(t)
        )
        if item.get("answer"):
            menu.add_command(
                label="复制答案", command=lambda t=item["answer"]: self._copy(t)
            )
        row.bind(
            "<Button-3>",
            lambda e, m=menu: self._popup(e, m),
            add="+",
        )

    # ---------- 动作 ----------

    def _popup(self, event, menu: tk.Menu):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _open(self, item: dict):
        self.set_current(item)
        self.on_open(item)

    def _reask(self, question: str):
        if self.on_reask:
            self.on_reask(question)

    def _new(self):
        if self.on_new:
            self.on_new()

    def _copy(self, text: str):
        try:
            self.winfo_toplevel().clipboard_clear()
            self.winfo_toplevel().clipboard_append(text)
        except tk.TclError:
            pass

    def _clear(self):
        from tkinter import messagebox

        try:
            ok = messagebox.askyesno(
                "清空历史",
                "确定清空全部查询历史?此操作不可撤销。",
                parent=self.winfo_toplevel(),
            )
        except Exception:
            ok = True
        if not ok:
            return
        self.history.clear()
        self._current = None
        self.refresh()
        if self.on_clear:
            self.on_clear()
