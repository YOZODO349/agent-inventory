# -*- coding: utf-8 -*-
"""卡片控件 —— 白底卡片，左侧一道彩色竖条作点缀。

一个 tkinter Canvas 小部件：纯白卡片，左缘一窄条彩色竖带（按应用主色），
**无液体、无晃动**，静止不动。

设计约束：
  * 只用 tkinter 原生绘图，不依赖 Pillow。
  * 颜色全部由外部传入，便于各栏目取不同主色。
  * **绝不画液体**，也**绝不做任何动画**。
"""
import tkinter as tk

# 默认色板（可被外部覆盖）
BASE_SHADOW  = "#e8eaee"   # 卡片外框


def _mix(c1, c2, t):
    """在两条 #rrggbb 之间取插值，t=0 得 c1，t=1 得 c2。"""
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    m = [int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)]
    return "#%02x%02x%02x" % tuple(m)


def darken(c, t=0.25):
    return _mix(c, "#000000", t)


def lighten(c, t=0.45):
    return _mix(c, "#ffffff", t)


class GlassCard(tk.Canvas):
    """一张白底卡片，左缘一道主色竖条；整块可点击。"""

    ACCENT_W = 6.0                    # 左缘竖条宽度（像素）

    def __init__(self, container, liquid="#7fb2e8", size=(300, 118),
                 fill_ratio=0.58, **kw):
        cw, ch = int(size[0]), int(size[1])
        kw.setdefault("bg", "#ffffff")
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightbackground", BASE_SHADOW)
        kw.setdefault("bd", 0)
        kw["width"] = cw
        kw["height"] = ch
        super().__init__(container, **kw)
        self.liquid = liquid          # 主色（仍沿用旧名，外部无需改）
        self.fill_ratio = fill_ratio  # 保留形参，已无用
        self._cw = cw
        self._ch = ch
        self._hover = False

    # ---------- 几何 ----------
    def _geom(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            w = self._cw
        if h <= 1:
            h = self._ch
        top = 6.0
        bot = h - 9.0
        return top, bot, w, h

    # ---------- 绘制 ----------
    def redraw(self, wave_t=0.0):
        """重画卡片。wave_t 参数保留兼容，已不再使用（无动画）。"""
        self.delete("shape")
        top, bot, w, h = self._geom()
        col = self.liquid

        # 左缘一道主色竖条（唯一的彩色元素，圆角感靠首尾缩短模拟）
        ax = self.ACCENT_W
        self.create_rectangle(0, top + 4, ax, bot - 4,
                              fill=col, outline="", tags="shape")

        # 竖向条顶端稍亮、底端稍浓，做出极轻的立体感
        self.create_rectangle(0, top + 4, ax, top + 4 + (bot - top) * 0.06,
                              fill=lighten(col, 0.30), outline="", tags="shape")
        self.create_rectangle(0, bot - 4 - (bot - top) * 0.06, ax, bot - 4,
                              fill=darken(col, 0.14), outline="", tags="shape")

        self.tag_raise("win")

    def redraw_all(self, wave_t=0.0):
        """删掉图形与内容窗口，重画图形，再按原坐标补回内容。"""
        self.redraw(wave_t)
        keep = list(getattr(self, "_content", []))
        self.delete("win")
        self._content = []
        for (widget, x, y, anchor, width, height, _old) in keep:
            self.add_content(widget, x, y, anchor, width, height)

    # ---------- 内容（窗口组件，随重绘自动归位） ----------
    def clear_content(self):
        self.delete("win")
        self._content = []

    def add_content(self, widget, x, y, anchor="nw", width=None, height=None):
        """把一个 Label/Frame 摆进卡片；存下坐标，重绘时自动补回。"""
        if not hasattr(self, "_content"):
            self._content = []
        item = self.create_window(x, y, window=widget, anchor=anchor, tags="win")
        if width is not None:
            self.itemconfigure(item, width=width)
        if height is not None:
            self.itemconfigure(item, height=height)
        self._content.append((widget, x, y, anchor, width, height, item))
        return item

    # ---------- 事件（只有悬停与点击，无动画） ----------
    def _hover_on(self, _e=None):
        self._hover = True
        self.configure(highlightbackground=darken(self.liquid, 0.20),
                       highlightthickness=2)

    def _hover_off(self, _e=None):
        self._hover = False
        self.configure(highlightbackground=BASE_SHADOW, highlightthickness=1)

    def _say_click(self, _e=None):
        cb = getattr(self, "_click_cb", None)
        if cb is not None:
            cb()

    def bind_click(self, callback):
        """整卡可点。

        要紧之处：卡里的文字/图标都是用 create_window 摆进来的**独立控件**，
        各有各的事件域，canvas 上的绑定管不到它们 —— 若只在 canvas 上绑，
        使用者点在文字上便毫无反应。故须**递归给每个内容控件也补上**。
        另外 `<Configure>` 会在卡刚摆好时自动触发一次，届时内容尚未摆入，
        故 bind 之后还要再刷一遍（`rebind_contents()`）。
        """
        self._click_cb = callback
        self.bind("<Enter>", self._hover_on)
        self.bind("<Leave>", self._hover_off)
        self.bind("<Button-1>", self._say_click)
        self.bind("<Configure>", self._on_config)
        try:
            self.configure(cursor="hand2")
        except Exception:
            pass
        self.rebind_contents()

    def bind_hover_only(self):
        """只做悬停反馈，**不接管点击**（用于另有详情窗的自然点击路径）。"""
        self._click_cb = None
        bound = getattr(self, "_bound", None)
        if bound is None:
            bound = self._bound = set()
        stack = [self]
        while stack:
            w = stack.pop()
            if str(w) in bound:
                continue
            bound.add(str(w))
            try:
                w.bind("<Enter>", self._hover_on)
                w.bind("<Leave>", self._hover_off)
                w.configure(cursor="hand2")
            except Exception:
                pass
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass

    def rebind_contents(self):
        """把悬停与点击补绑到所有内容控件（含其子控件）上。

        两处要紧：
          1. 用 `add="+"` **累加**会出事 —— Tk 的 bindtags 链会让同一事件
             依次经过控件、类、顶层、all 四站，遂触发四回。故此处**不累加**，
             同签只留最新一枚；且用 `_bound` 记下已绑者，不重复动手。
          2. 悬停只在**卡片本身**变描边，子控件上的进出一律转发到卡上处理，
             免得鼠标从文字边上滑过时描边闪回原色。
        """
        if not getattr(self, "_click_cb", None):
            return
        bound = getattr(self, "_bound", None)
        if bound is None:
            bound = self._bound = set()
        for (widget, *_rest) in list(getattr(self, "_content", [])):
            stack = [widget]
            while stack:
                w = stack.pop()
                if str(w) in bound:
                    continue
                bound.add(str(w))
                try:
                    w.bind("<Enter>", self._hover_on)
                    w.bind("<Leave>", self._hover_off)
                    w.bind("<Button-1>", self._say_click)
                    w.configure(cursor="hand2")
                except Exception:
                    pass
                try:
                    stack.extend(w.winfo_children())
                except Exception:
                    pass

    def _on_config(self, _e=None):
        """窗口尺寸变时重画；已绑的事件不必重绑（`_bound` 记着）。"""
        self.redraw_all(0.0)
