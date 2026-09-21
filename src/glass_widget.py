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
# 2026-09-20（UI 美化）：原为冷灰 #e8eaee，与应用的暖灰族（#eaeaea / #f7f6f3）
# 不是一家 —— 一冷一暖并排，卡片看着"脏"。此处归到暖灰族，数值取主程序里的 EDGE。
BASE_SHADOW  = "#e6e5e1"   # 卡片外框（暖灰，与主程序 EDGE 一致）


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
    """一张白底卡片，左缘一道主色竖脊；整块可点击。"""

    ACCENT_W = 4.0                    # 左缘竖脊宽度（未经缩放的基准值）

    def __init__(self, container, liquid="#7fb2e8", size=(300, 118),
                 fill_ratio=0.58, **kw):
        cw, ch = int(size[0]), int(size[1])
        kw.setdefault("bg", "#ffffff")
        # 第五十二轮（照 redesign-skill 审）：卡片整块可点 —— 鼠标移上去要变手型，
        # 否则用户不知道它能点（原先只有悬停变色，没有指针提示）
        kw.setdefault("cursor", "hand2")
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
        self._pressed = False
        # 竖脊宽度随屏幕缩放走（卡片本身是缩放后的尺寸，脊却钉死 4px 会显得细弱）
        try:
            self._sc = float(self.tk.call("tk", "scaling")) / (96.0 / 72.0)
        except Exception:
            self._sc = 1.0
        if not (0.5 <= self._sc <= 4.0):
            self._sc = 1.0

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
    def _bar_color(self):
        """竖脊的颜色：静置为本色，悬停略深，按下再深一档（三档就够）。"""
        if self._pressed:
            return darken(self.liquid, 0.30)
        if self._hover:
            return darken(self.liquid, 0.14)
        return self.liquid

    def redraw(self, wave_t=0.0):
        """重画卡片。wave_t 参数保留兼容，已不再使用（无动画）。

        改版前左脊是「亮／主／暗」**三段拼色**（顶端提亮 30%、底端压暗 14%），
        本意是做立体感，实际在大屏上能看出两道横向接缝，像贴纸没贴平。
        今改为一色到底、通高贴边的一道脊 —— 它是这张卡的身份色，不必装作立体。
        """
        self.delete("shape")
        _top, _bot, _w, h = self._geom()
        bw = max(3.0, round(self.ACCENT_W * getattr(self, "_sc", 1.0)))
        self.create_rectangle(0, 0, bw, h, fill=self._bar_color(),
                              outline="", tags="shape")
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
    def _paint_state(self):
        """按当前状态上色。

        改版前悬停是把描边**从 1px 加粗到 2px** —— 加粗会把卡片的内框挤小
        一格，卡里的文字跟着挪一下，鼠标划过时整块内容在抖。今改为**只换颜色、
        不动粗细**：观感一样明确，内容一动不动。
        """
        if self._hover:
            self.configure(highlightbackground=darken(self.liquid, 0.34))
        else:
            self.configure(highlightbackground=BASE_SHADOW)
        try:
            self.redraw(0.0)
        except Exception:
            pass

    def _hover_on(self, _e=None):
        self._hover = True
        self._paint_state()

    def _hover_off(self, _e=None):
        self._hover = False
        self._pressed = False
        self._paint_state()

    def _press_on(self, _e=None):
        self._pressed = True
        try:
            self.redraw(0.0)
        except Exception:
            pass

    def _press_off(self, _e=None):
        """松开鼠标：只收回「按下」这一档，**不动悬停态**（指针多半还在卡上）。"""
        self._pressed = False
        try:
            self.redraw(0.0)
        except Exception:
            pass

    def _say_click(self, _e=None):
        self._pressed = False
        cb = getattr(self, "_click_cb", None)
        if cb is not None:
            cb()

    def bind_click(self, callback):
        """整卡可点。

        要紧之处：卡里的文字/图标都是用 create_window 摆进来的**独立控件**，
        各有各的事件域，canvas 上的绑定管不到它们 —— 若只在 canvas 上绑，
        作者点在文字上便毫无反应。故须**递归给每个内容控件也补上**。
        另外 `<Configure>` 会在卡刚摆好时自动触发一次，届时内容尚未摆入，
        故 bind 之后还要再刷一遍（`rebind_contents()`）。
        """
        self._click_cb = callback
        self.bind("<Enter>", self._hover_on)
        self.bind("<Leave>", self._hover_off)
        self.bind("<ButtonPress-1>", self._press_on)
        self.bind("<ButtonRelease-1>", self._press_off)
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
                w.bind("<ButtonPress-1>", self._press_on)
                w.bind("<ButtonRelease-1>", self._press_off)
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
                    w.bind("<ButtonPress-1>", self._press_on)
                    w.bind("<ButtonRelease-1>", self._press_off)
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
