"""查询历史:落盘存储 + 查看面板 + 输入框上下键召回。

历史写在用户目录下的 ``~/.pixelrag/history.json``(不放仓库里,免得污染项目),
一条记录 = 时间 / 问题 / 命中页数 / 答案。存储只做增删查,不涉及任何检索逻辑。
"""

from __future__ import annotations

import json
import os
import time
import tkinter as tk
from collections.abc import Callable
from datetime import datetime, timezone

import pixelrag_theme as T
import pixelrag_widgets as W

HISTORY_PATH = os.path.join(os.path.expanduser("~"), ".pixelrag", "history.json")
MAX_ITEMS = 200  # 只留最近这么多条
REUSE_WINDOW = 300  # 秒;同一个问题在这段时间内重复提交(含"重新生成")算同一条


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
        """新的在前(面板按这个顺序显示)。"""
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
        }
        self._items.append(item)
        if len(self._items) > MAX_ITEMS:
            del self._items[:-MAX_ITEMS]
        self._save()
        return item

    def update(
        self, item: dict | None, hits: int | None = None, answer: str | None = None
    ):
        """问答结束后回填命中页数与答案。"""
        if not item:
            return
        if hits is not None:
            item["hits"] = int(hits)
        if answer is not None:
            item["answer"] = answer
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
    """显示成本地时间。统一用 ``MM-DD HH:MM``:每行等宽,问题那列才对得齐。"""
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except Exception:
        return ""
    return dt.strftime("%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 面板
# ---------------------------------------------------------------------------


class HistoryPanel:
    """历史列表面板(独立窗口)。

    ``on_pick(question)``:点某条问题 / 点「重问」时回调,由调用方决定怎么重问。
    """

    def __init__(
        self,
        root: tk.Tk,
        history: History,
        on_pick: Callable[[str], None],
        on_clear: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ):
        self.root = root
        self.history = history
        self.on_pick = on_pick
        self.on_clear = on_clear
        self.on_close = on_close
        self._closed = False

        win = tk.Toplevel(root)
        self.win = win
        win.title("查询历史")
        win.configure(bg=T.c("bg"))
        win.transient(root)
        w, h = int(620 * T.UI_SCALE), int(560 * T.UI_SCALE)
        win.geometry(f"{w}x{h}+{root.winfo_rootx() + 80}+{root.winfo_rooty() + 60}")
        win.minsize(int(420 * T.UI_SCALE), int(260 * T.UI_SCALE))
        win.bind("<Escape>", lambda _e: self.close())
        win.bind("<MouseWheel>", self._on_wheel)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._render_rows()
        win.focus_set()

    # ---------- 骨架 ----------

    def _build(self):
        head = tk.Frame(self.win, bg=T.c("surface"))
        head.pack(side="top", fill="x")
        inner = tk.Frame(head, bg=T.c("surface"))
        inner.pack(fill="x", padx=T.SPACE["lg"], pady=T.SPACE["sm"])

        tk.Label(
            inner, text="查询历史", bg=T.c("surface"), fg=T.c("text"), font=T.font("h2")
        ).pack(side="left")
        self.count_lbl = tk.Label(
            inner,
            text="",
            bg=T.c("surface"),
            fg=T.c("text_faint"),
            font=T.font("caption"),
        )
        self.count_lbl.pack(
            side="left", padx=(T.SPACE["sm"], 0), pady=(T.SPACE["xs"], 0)
        )

        self.clear_btn = tk.Label(
            inner,
            text="清空历史",
            bg=T.c("surface"),
            fg=T.c("text_muted"),
            font=T.font("caption"),
            cursor="hand2",
            padx=T.SPACE["sm"],
            pady=T.SPACE["xs"],
        )
        self.clear_btn.pack(side="right")
        self.clear_btn.bind("<Button-1>", lambda _e: self._clear())
        W.bind_hover(
            self.clear_btn,
            on_enter=lambda: self.clear_btn.configure(fg=T.c("danger")),
            on_leave=lambda: self.clear_btn.configure(fg=T.c("text_muted")),
        )
        W.make_focusable(self.clear_btn, T.c("surface"), self._clear)
        tk.Frame(self.win, bg=T.c("border"), height=1).pack(side="top", fill="x")

        body = tk.Frame(self.win, bg=T.c("bg"))
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=T.c("bg"), highlightthickness=0, bd=0)
        self.scroll = tk.Scrollbar(
            body,
            orient="vertical",
            command=self.canvas.yview,
            bg=T.c("scrollbar"),
            troughcolor=T.c("bg"),
            activebackground=T.c("border_strong"),
            borderwidth=0,
        )
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.body = tk.Frame(self.canvas, bg=T.c("bg"))
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width)
        )

        # 面板变宽时问题重新折行(只绑一次,一次改所有行)
        self._q_labels: list[tk.Label] = []
        self.win.bind("<Configure>", self._on_resize)

        tip = tk.Label(
            self.win,
            text="点问题即可重新提问  ·  ↑ / ↓ 在输入框里快速召回",
            bg=T.c("bg"),
            fg=T.c("text_faint"),
            font=T.font("micro"),
        )
        tip.pack(side="bottom", pady=(T.SPACE["xs"], T.SPACE["sm"]))

    def _on_wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120) * 3, "units")
        except Exception:
            pass

    def _on_resize(self, event):
        wrap = max(120, event.width - int(360 * T.UI_SCALE))
        for lbl in self._q_labels:
            try:
                lbl.configure(wraplength=wrap)
            except tk.TclError:
                # 列表刚重绘过,旧标签已经销毁;Configure 事件是排队来的,可能晚到
                pass

    # ---------- 列表 ----------

    def _render_rows(self):
        for w in self.body.winfo_children():
            w.destroy()
        self._q_labels = []  # 上一批标签已经销毁,别再拿去改 wraplength
        items = self.history.items()
        self.count_lbl.configure(text=f"{len(items)} 条")
        if not items:
            tk.Label(
                self.body,
                text="还没有查询记录。\n问过的问题会自动记在这里,关掉程序也不会丢。",
                bg=T.c("bg"),
                fg=T.c("text_faint"),
                font=T.font("body"),
                justify="left",
            ).pack(anchor="w", padx=T.SPACE["xl"], pady=T.SPACE["xl"])
            return
        for i, it in enumerate(items):
            self._row(it)
            if i != len(items) - 1:
                tk.Frame(self.body, bg=T.c("border"), height=1).pack(fill="x")

    def _row(self, item: dict):
        base = T.c("bg")
        row = tk.Frame(self.body, bg=base)
        row.pack(fill="x")

        meta_bits = []
        if item.get("hits") is not None:
            meta_bits.append(f"{item['hits']} 页命中")
        else:
            meta_bits.append("未完成")
        if item.get("answer"):
            meta_bits.append("有答案")
        tk.Label(
            row,
            text=" · ".join(meta_bits),
            bg=base,
            fg=T.c("text_faint"),
            font=T.font("micro"),
        ).pack(side="right", padx=(T.SPACE["md"], T.SPACE["lg"]))

        ask = tk.Label(
            row,
            text="重问",
            bg=base,
            fg=T.c("accent"),
            font=T.font("micro"),
            cursor="hand2",
            padx=T.SPACE["xs"],
        )
        ask.pack(side="right")
        ask.bind("<Button-1>", lambda _e, q=item["question"]: self._pick(q))
        if item.get("answer"):
            cp_a = tk.Label(
                row,
                text="复制答案",
                bg=base,
                fg=T.c("text_muted"),
                font=T.font("micro"),
                cursor="hand2",
                padx=T.SPACE["xs"],
            )
            cp_a.pack(side="right")
            cp_a.bind("<Button-1>", lambda _e, t=item["answer"]: self._copy(t))
        else:
            cp_a = None
        cp_q = tk.Label(
            row,
            text="复制问题",
            bg=base,
            fg=T.c("text_muted"),
            font=T.font("micro"),
            cursor="hand2",
            padx=T.SPACE["xs"],
        )
        cp_q.pack(side="right")
        cp_q.bind("<Button-1>", lambda _e, t=item["question"]: self._copy(t))

        # 左边一块(时间 + 问题)整体可点 = 重问
        hit_area = tk.Frame(row, bg=base)
        hit_area.pack(side="left", fill="x", expand=True, padx=(0, T.SPACE["md"]))
        tk.Label(
            hit_area,
            text=_pretty(item.get("time", "")),
            bg=base,
            fg=T.c("text_muted"),
            font=T.font("caption"),
        ).pack(side="left", pady=T.SPACE["sm"])
        q_lbl = tk.Label(
            hit_area,
            text=item["question"],
            bg=base,
            fg=T.c("text"),
            font=T.font("body"),
            justify="left",
            anchor="w",
            wraplength=int(340 * T.UI_SCALE),
        )
        q_lbl.pack(side="left", fill="x", expand=True, padx=(T.SPACE["md"], 0))
        self._q_labels.append(q_lbl)

        def enter(_e=None):
            W.set_bg_recursive(row, T.c("card"))
            q_lbl.configure(fg=T.c("accent"))

        def leave(_e=None):
            W.set_bg_recursive(row, base)
            q_lbl.configure(fg=T.c("text"))

        W.bind_hover(row, on_enter=enter, on_leave=leave)
        W.bind_click_recursive(hit_area, lambda q=item["question"]: self._pick(q))
        W.set_cursor_recursive(hit_area)

    # ---------- 动作 ----------

    def _pick(self, question: str):
        self.close()
        self.on_pick(question)

    def _copy(self, text: str):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def _clear(self):
        from tkinter import messagebox

        try:
            ok = messagebox.askyesno(
                "清空历史", "确定清空全部查询历史?此操作不可撤销。", parent=self.win
            )
        except Exception:
            ok = True
        if not ok:
            return
        self.history.clear()
        self._render_rows()
        if self.on_clear:
            self.on_clear()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close()
