#!/usr/bin/env python3
"""PixelRAG 知识库桌面客户端(DeepSeek 风格深色聊天界面)。

打开即自动检测/启动检索服务,支持中文或英文查询,返回最相关的论文页面
(标题 + 相似度 + 页面截图),点击标题打开论文 PDF,点击截图看大图。

依赖:仅标准库 tkinter + 已安装的 Pillow,无需额外安装。
"""
import base64
import io
import json
import os
import subprocess
import threading
import time
import tkinter as tk
import urllib.request

import anthropic
from PIL import Image, ImageTk

ROOT = os.path.dirname(os.path.abspath(__file__))
HOST = "localhost"
PORT = 30001
N_DOCS = 5
HEALTH_URL = f"http://{HOST}:{PORT}/health"
SEARCH_URL = f"http://{HOST}:{PORT}/search"

# ---- VLM 生成配置(DeepSeek-V4.1-Flash,原生视觉)----
VLM_MODEL = "deepseek-flash"             # DeepSeek-V4.1-Flash 官方主名;旧名 deepseek-v4-flash 已下线
VLM_BASE_URL = "https://api.deepseek.com/anthropic"
VLM_MAX_IMAGES = 6                        # 最多发几张截图
VLM_MAX_SIDE = 1568                       # 截图长边像素上限(超出即压缩)
VLM_MAX_TOKENS = 2048

# ---- 配色(参考 DeepSeek 深色主题)----
BG = "#1f1f1f"        # 主背景
BAR_BG = "#141414"    # 顶栏/底栏背景
CARD_BG = "#2a2a2a"   # 结果卡片背景
USER_BG = "#4d6bfe"   # 用户气泡(蓝)
USER_FG = "#ffffff"
TEXT_FG = "#e6e6e6"   # 主文字
MUTED_FG = "#9e9e9e"  # 次要文字
ACCENT = "#7a9bff"    # 可点击标题
BORDER = "#3a3a3a"
GREEN = "#4ade80"     # 服务运行中
YELLOW = "#facc15"    # 启动中
RED = "#f87171"       # 失败

FONT = "Microsoft YaHei UI"


# ---------------------------------------------------------------------------
# 服务与检索
# ---------------------------------------------------------------------------

def check_health() -> bool:
    try:
        urllib.request.urlopen(HEALTH_URL, timeout=3)
        return True
    except Exception:
        return False


def _serve_cmd() -> list[str]:
    exe = os.path.join(ROOT, ".venv", "Scripts", "pixelrag.exe")
    return [
        exe, "serve",
        "--index-dir", os.path.join(ROOT, "knowledge_index"),
        "--tiles-dir", os.path.join(ROOT, "knowledge_index", "tiles"),
        "--articles-json", os.path.join(ROOT, "knowledge_index", "articles.json"),
        "--device", "cpu",
        "--port", str(PORT),
    ]


def start_serve_process() -> subprocess.Popen:
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        _serve_cmd(), env=env, creationflags=flags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def search(text: str, n_docs: int = N_DOCS) -> dict:
    payload = json.dumps(
        {"queries": [{"text": text}], "n_docs": n_docs, "include_images": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        SEARCH_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read().decode("utf-8"))


def title_of(url: str) -> str:
    name = os.path.basename((url or "").replace("\\", "/"))
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    return name


def decode_b64_image(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


SYSTEM_PROMPT = (
    "你是一个科研文献视觉问答助手。用户会提供若干张论文页面的截图,并附上一个问题。"
    "请只根据这些截图中的可见内容回答,严格遵守:\n"
    "1. 只能依据截图内容回答,严禁编造截图里没有的数据或事实。\n"
    "2. 若截图信息不足以回答,直接说\"根据现有资料无法回答\",不要猜测。\n"
    "3. 回答要具体、简洁,直接给出结论。\n"
    "4. 每个关键事实后用 [1][2] 等编号标注,编号对应第几张截图(从 1 开始)。\n"
    "5. 若答案来自某张图表,说明依据的是第几张图的哪个部分。\n"
)


def _load_vlm_config() -> dict:
    """读取 VLM 配置:优先环境变量 PIXELRAG_VLM_*,其次 ANTHROPIC_* 环境变量,最后回退 ~/.claude/settings.json。"""
    cfg = {
        "api_key": os.environ.get("PIXELRAG_VLM_API_KEY"),
        "base_url": os.environ.get("PIXELRAG_VLM_BASE_URL"),
        "model": os.environ.get("PIXELRAG_VLM_MODEL"),
    }
    if not cfg["api_key"]:
        cfg["api_key"] = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        cfg["base_url"] = os.environ.get("ANTHROPIC_BASE_URL") or VLM_BASE_URL
    if not cfg["api_key"]:
        try:
            p = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
            env = json.load(open(p, encoding="utf-8")).get("env", {})
            cfg["api_key"] = env.get("ANTHROPIC_AUTH_TOKEN")
            cfg["base_url"] = env.get("ANTHROPIC_BASE_URL") or VLM_BASE_URL
        except Exception:
            pass
    cfg["base_url"] = cfg["base_url"] or VLM_BASE_URL
    cfg["model"] = cfg["model"] or VLM_MODEL
    return cfg


def prepare_images(hits: list, k: int = VLM_MAX_IMAGES) -> list:
    """取 Top-K 相关截图:按相关度降序、按 (article_id, tile_index) 去重、压缩为长边 ≤ VLM_MAX_SIDE 的 JPEG。

    返回 [(b64_jpeg, hit), ...],顺序即引用编号 [1][2]...。
    """
    seen = set()
    out = []
    for h in sorted(hits, key=lambda x: float(x.get("score", 0.0)), reverse=True):
        key = (h.get("article_id"), h.get("tile_index"))
        if key in seen:
            continue
        seen.add(key)
        b64 = h.get("image_base64")
        if not b64:
            continue
        try:
            img = decode_b64_image(b64)
            img.thumbnail((VLM_MAX_SIDE, VLM_MAX_SIDE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64_out = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            continue
        out.append((b64_out, h))
        if len(out) >= k:
            break
    return out


def vlm_stream(images, question: str, cfg: dict):
    """把多张截图按相关度顺序 + 问题发给 VLM,流式 yield 答案文本(过滤 thinking)。"""
    client = anthropic.Anthropic(api_key=cfg["api_key"], base_url=cfg["base_url"])
    content = []
    for i, (b64, _h) in enumerate(images, 1):
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
        content.append({"type": "text", "text": f"[第{i}张截图]"})
    content.append({"type": "text", "text": question})

    with client.messages.stream(
        model=cfg["model"],
        max_tokens=VLM_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        for ev in stream:
            if getattr(ev, "type", None) == "text":
                yield ev.text


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PixelRAG 视觉问答")
        self.root.geometry("880x760")
        self.root.minsize(640, 560)
        self.root.configure(bg=BG)

        self._photos: list[ImageTk.PhotoImage] = []  # 防止图片被 GC
        self._serve_ready = check_health()
        self._busy = False

        self._build_ui()
        self._add_note("欢迎使用 PixelRAG 视觉问答。\n输入问题,回车后检索相关论文页面截图,并由视觉模型生成答案(附引用)。")
        self._set_status()

        if not self._serve_ready:
            threading.Thread(target=self._ensure_serve, daemon=True).start()

    # ---------- UI 构建 ----------

    def _build_ui(self):
        # 顶栏状态
        self.status = tk.Label(
            self.root, text="", bg=BAR_BG, fg=MUTED_FG, anchor="w",
            padx=16, pady=7, font=(FONT, 10),
        )
        self.status.pack(side="top", fill="x")

        # 消息区(Canvas + Frame + 滚动条)
        body = tk.Frame(self.root, bg=BG)
        body.pack(side="top", fill="both", expand=True)

        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        self.scroll = tk.Scrollbar(
            body, orient="vertical", command=self.canvas.yview,
            bg=CARD_BG, troughcolor=BG, activebackground=BORDER,
            borderwidth=0, width=12,
        )
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.frame = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._win, width=e.width),
        )

        # 底部输入栏
        bar = tk.Frame(self.root, bg=BAR_BG)
        bar.pack(side="bottom", fill="x")

        self.input = tk.Text(
            bar, height=3, bg=CARD_BG, fg=TEXT_FG, insertbackground=TEXT_FG,
            relief="flat", font=(FONT, 12), padx=12, pady=10, wrap="word",
        )
        self.input.pack(side="left", fill="x", expand=True, padx=(16, 8), pady=12)
        self.input.bind("<Return>", self._on_enter)
        self.input.insert("1.0", "")
        self.input.focus_set()

        self.send_btn = tk.Button(
            bar, text="发送", command=self._on_send,
            bg=USER_BG, fg=USER_FG, activebackground="#5a7cff",
            activeforeground="#ffffff", relief="flat", font=(FONT, 11, "bold"),
            padx=22, pady=8, cursor="hand2",
        )
        self.send_btn.pack(side="right", padx=(0, 16), pady=12)

    # ---------- 状态 ----------

    def _set_status(self, state: str | None = None):
        if state == "starting":
            self.status.configure(text="●  正在启动检索服务...", fg=YELLOW)
        elif state == "failed":
            self.status.configure(text="●  服务启动失败(见控制台)", fg=RED)
        elif self._serve_ready:
            self.status.configure(text="●  服务运行中  ·  端口 30001", fg=GREEN)
        else:
            self.status.configure(text="●  服务启动中...", fg=YELLOW)

    # ---------- 消息渲染 ----------

    def _add_user(self, text: str):
        row = tk.Frame(self.frame, bg=BG)
        row.pack(fill="x", padx=16, pady=(14, 2))
        lbl = tk.Label(
            row, text=text, bg=USER_BG, fg=USER_FG, justify="left",
            wraplength=520, font=(FONT, 12), padx=14, pady=9,
        )
        lbl.pack(side="right", anchor="e")
        self._scroll_bottom()

    def _add_note(self, text: str):
        lbl = tk.Label(
            self.frame, text=text, bg=BG, fg=MUTED_FG, justify="center",
            font=(FONT, 11), padx=16, pady=10,
        )
        lbl.pack(fill="x", padx=16)
        self._scroll_bottom()

    def _add_error(self, text: str):
        lbl = tk.Label(
            self.frame, text=text, bg="#3a1d1d", fg=RED, justify="left",
            wraplength=700, font=(FONT, 11), padx=14, pady=10,
        )
        lbl.pack(fill="x", padx=16, pady=8)
        self._scroll_bottom()

    def _add_result_card(self, hit: dict, idx: int):
        card = tk.Frame(
            self.frame, bg=CARD_BG, highlightthickness=1,
            highlightbackground=BORDER,
        )
        card.pack(fill="x", padx=16, pady=(0, 14))

        url = hit.get("url", "")
        title = title_of(url)
        ti = hit.get("tile_index", 0)
        score = hit.get("score", 0.0)

        # 标题(点击打开 PDF)
        title_lbl = tk.Label(
            card, text=f"[{idx}]  {title}", bg=CARD_BG, fg=ACCENT,
            font=(FONT, 11, "bold"), justify="left", anchor="w",
            wraplength=760, cursor="hand2", padx=14,
        )
        title_lbl.pack(fill="x", pady=(12, 2))
        title_lbl.bind(
            "<Button-1>",
            lambda e, u=url: self._open_pdf(u),
        )

        # 元信息
        meta = tk.Label(
            card, text=f"页面 tile_{ti:04d}   ·   相似度 {score:.3f}   ·   点击标题打开 PDF",
            bg=CARD_BG, fg=MUTED_FG, font=(FONT, 9), anchor="w", padx=14,
        )
        meta.pack(fill="x", pady=(0, 8))

        # 页面截图缩略图(点击看大图)
        b64 = hit.get("image_base64")
        if b64:
            try:
                img = decode_b64_image(b64)
                img.thumbnail((320, 460), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self._photos.append(photo)
                img_lbl = tk.Label(card, image=photo, bg=CARD_BG, cursor="hand2")
                img_lbl.image = photo
                img_lbl.pack(padx=14, pady=(0, 14))
                img_lbl.bind(
                    "<Button-1>",
                    lambda e, b=b64, t=title: self._open_image(b, t),
                )
            except Exception:
                pass

        self._scroll_bottom()

    def _scroll_bottom(self):
        self.root.after_idle(lambda: self.canvas.yview_moveto(1.0))

    # ---------- 交互 ----------

    def _on_enter(self, event):
        self._on_send()
        return "break"  # 阻止 Enter 插入换行

    def _on_send(self):
        if self._busy:
            return
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._add_user(text)

        if not self._serve_ready:
            self._add_note("检索服务尚未就绪,正在启动,请稍候几秒再试...")
            threading.Thread(target=self._ensure_serve, daemon=True).start()
            return

        self._busy = True
        self._add_note("⏳ 正在检索截图并生成答案,请稍候...")
        threading.Thread(target=self._do_search, args=(text,), daemon=True).start()

    def _do_search(self, text: str):
        try:
            resp = search(text)
            hits = resp["results"][0]["hits"]
        except Exception as e:
            self.root.after(0, lambda err=e: self._show_error(err))
            self.root.after(0, self._clear_busy)
            return
        if not hits:
            self.root.after(0, lambda: self._add_note("没有找到相关结果,换个说法试试。"))
            self.root.after(0, self._clear_busy)
            return
        self._generate(text, hits)  # 仍在后台线程,继续生成答案

    def _show_results(self, hits: list):
        if not hits:
            self._add_note("没有找到相关结果,换个说法试试。")
            return
        for i, h in enumerate(hits, 1):
            self._add_result_card(h, i)

    def _generate(self, text: str, hits: list):
        """准备截图 → 调 VLM 流式生成 → 前端流式显示答案 + 引用卡片。运行于后台线程。"""
        images = prepare_images(hits)
        if not images:
            self.root.after(0, lambda: self._add_error(
                "检索到了结果,但截图加载失败。以下为检索到的相关页面:"
            ))
            self.root.after(0, lambda: self._show_results(hits))
            self.root.after(0, self._clear_busy)
            return

        cfg = _load_vlm_config()
        if not cfg["api_key"]:
            self.root.after(0, lambda: self._add_error(
                "未找到 VLM API key。请设置环境变量 PIXELRAG_VLM_API_KEY,\n"
                "或确保 ~/.claude/settings.json 里有 ANTHROPIC_AUTH_TOKEN。\n"
                "以下为检索到的相关页面:"
            ))
            self.root.after(0, lambda: self._show_results(hits))
            self.root.after(0, self._clear_busy)
            return

        # 在 UI 线程创建助手气泡,拿到可更新的 Label
        self._answer_ready = threading.Event()
        self.root.after(0, self._begin_answer)
        if not self._answer_ready.wait(timeout=5):
            self.root.after(0, self._clear_busy)
            return

        full = ""
        try:
            for chunk in vlm_stream(images, text, cfg):
                full += chunk
                self.root.after(0, lambda t=full: self._set_answer(t))
        except Exception as e:
            self.root.after(0, lambda err=e: self._add_error(f"生成失败:{err}"))
            if full:
                self.root.after(0, lambda: self._set_answer(full + "\n\n[生成中断]"))
        finally:
            self.root.after(0, lambda: self._finish_answer(images))

    def _begin_answer(self):
        row = tk.Frame(self.frame, bg=BG)
        row.pack(fill="x", padx=16, pady=(14, 2))
        lbl = tk.Label(
            row, text="", bg=CARD_BG, fg=TEXT_FG, justify="left", anchor="w",
            wraplength=680, font=(FONT, 12), padx=14, pady=10,
        )
        lbl.pack(side="left", fill="x", expand=True)
        self._answer_label = lbl
        self._answer_ready.set()
        self._scroll_bottom()

    def _set_answer(self, text: str):
        lbl = getattr(self, "_answer_label", None)
        if lbl is not None:
            lbl.configure(text=text)
            self._scroll_bottom()

    def _finish_answer(self, images):
        lbl = getattr(self, "_answer_label", None)
        if lbl is not None and not lbl.cget("text"):
            lbl.configure(text="(VLM 未返回内容,请重试。)")
        if images:
            self._add_note("引用来源:")
            for i, (_b64, hit) in enumerate(images, 1):
                self._add_result_card(hit, i)
        self._clear_busy()

    def _show_error(self, e: Exception):
        self._add_error(
            f"查询失败:{e}\n请确认检索服务已启动、索引文件完整。"
        )

    def _clear_busy(self):
        self._busy = False

    # ---------- 打开图片 / PDF ----------

    def _open_image(self, b64: str, title: str):
        try:
            img = decode_b64_image(b64)
        except Exception:
            return
        win = tk.Toplevel(self.root)
        win.title(title or "页面截图")
        win.configure(bg="#000000")
        max_w = int(win.winfo_screenwidth() * 0.85)
        max_h = int(win.winfo_screenheight() * 0.85)
        img.thumbnail((max_w, max_h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self._photos.append(photo)
        lbl = tk.Label(win, image=photo, bg="#000000")
        lbl.image = photo
        lbl.pack()

    def _open_pdf(self, url: str):
        if url and os.path.exists(url):
            os.startfile(url)
        else:
            self._add_note("找不到该论文 PDF 文件。")

    # ---------- 服务 ----------

    def _ensure_serve(self):
        if check_health():
            self._serve_ready = True
            self.root.after(0, self._set_status)
            return
        self.root.after(0, self._set_status, "starting")
        start_serve_process()
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(2)
            if check_health():
                self._serve_ready = True
                self.root.after(0, self._set_status)
                return
        self.root.after(0, self._set_status, "failed")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
