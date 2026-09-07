#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent 工作状态红绿灯（纯 Win32 版）

为什么是纯 Win32 而不是 Tkinter：
  Tk 窗口在打包 / 显示后会被 Tk 自身重置扩展样式，无论是
  WS_EX_TRANSPARENT 还是 WM_NCHITTEST 子类化钩子都无法稳定实现
  “鼠标穿透到背后程序”。纯 Win32 窗口（自绘 GDI）配合
  WM_NCHITTEST 返回 HTTRANSPARENT 可稳定穿透（已用探针验证）。

功能：
  - 竖排红/黄/绿三灯，常驻屏幕右上角，置顶悬浮
  - 半透明磨砂（仅“可交互”模式启用 DWM 亚克力）
  - 鼠标穿透开关：开启后点击落到背后程序（如浏览器书签）
  - 自动读取 CodeBuddy 会话状态
  - 系统托盘右键菜单控制所有选项
  - 红灯语义：需确认 / 暂停，需要用户操作
"""

import os
import sys
import json
import ctypes
import time
import threading
import queue

import win32api
import win32con
import win32gui

# GDI / User32 的 ctypes 声明
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

APP_NAME = "AgentTrafficLight"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

# 状态定义：(颜色, 标题, 说明)
# 红灯语义：需确认 / 暂停，需要用户操作
STATES = {
    "green":  ("#22c55e", "空闲",    "Agent 处于空闲或正常运行状态"),
    "yellow": ("#eab308", "忙碌",    "Agent 正在执行任务"),
    "red":    ("#ef4444", "需确认",  "需要用户确认操作，或已暂停等待处理"),
}
STATE_ORDER = ["green", "yellow", "red"]

# 窗口尺寸（紧凑细长）
WIN_W = 54
WIN_H = 150

# CodeBuddy 会话数据库
_CANDIDATES = [
    os.path.join(os.environ.get("APPDATA", ""), "CodeBuddy CN", "codebuddy-sessions.vscdb"),
    os.path.join(os.environ.get("APPDATA", ""), "CodeBuddy", "codebuddy-sessions.vscdb"),
]
# 红灯：仅“阻塞在用户”的状态（需确认 / 输入 / 暂停等待处理）才亮红灯。
# 注意：cancelled/aborted/failed/error/timeout 等“任务终止”状态，尤其是
# 用户主动删除未结束任务后留下的 cancelled/aborted/failed，不应亮红灯，否则会“常红”。
_RED_STATES = {"needs_input", "paused", "waiting", "confirm", "blocked", "permission", "intervention"}
# 黄灯：正在执行
_WORK_STATES = {"working", "streaming", "running", "pending", "queued", "busy"}
# 其余状态（completed/failed/error/cancelled/aborted/timeout/deleted/removed/空/未知）一律不亮红灯

try:
    import sqlite3
    _HAS_SQLITE = True
except ImportError:
    _HAS_SQLITE = False


def _codebuddy_sessions_db():
    for p in _CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def read_codebuddy_state():
    db = _codebuddy_sessions_db()
    if not db or not _HAS_SQLITE:
        return None
    try:
        con = sqlite3.connect(db)
        con.text_factory = bytes
        cur = con.cursor()
        cur.execute("SELECT value FROM ItemTable WHERE key LIKE 'session:%'")
        rows = cur.fetchall()
        con.close()
    except Exception:
        return None

    working = red = other = 0
    titles = []
    for (val,) in rows:
        try:
            txt = val.decode("utf-8", errors="replace")
            obj = json.loads(txt)
        except Exception:
            continue
        st = str(obj.get("status", "")).strip().lower()
        title = obj.get("title", "")
        try:
            title = title.encode("latin-1").decode("utf-8")
        except Exception:
            pass
        if st in _WORK_STATES:
            working += 1
            titles.append(title)
        elif st in _RED_STATES:
            red += 1
            titles.append(title)
        else:
            other += 1
    total = working + red + other
    if red > 0:
        return {"state": "red", "detail": f"CodeBuddy: {red} 个会话需确认/暂停",
                "working": working, "red": red, "total": total, "titles": titles}
    if working > 0:
        return {"state": "yellow", "detail": f"CodeBuddy: {working} 个会话进行中",
                "working": working, "red": red, "total": total, "titles": titles}
    return {"state": "green", "detail": "CodeBuddy: 全部会话空闲/完成",
            "working": working, "red": red, "total": total, "titles": titles}


def load_config():
    default = {"startup": False, "always_on_top": True, "state": "green",
               "auto_mode": True, "penetrate": True}
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            default.update(data)
    except Exception:
        pass
    return default


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_startup(enable: bool):
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
    exe = sys.executable
    script = os.path.abspath(__file__)
    if getattr(sys, "frozen", False):
        target = f'"{exe}"'
    else:
        pythonw = exe.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = exe
        target = f'"{pythonw}" "{script}"'
    try:
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, target)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def get_startup() -> bool:
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_QUERY_VALUE)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


# ---------- 颜色工具 ----------
def hex2rgb(h):
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def rgb2bgr(rgb):
    r, g, b = rgb
    return (b << 16) | (g << 8) | r


def blend(h1, h2, t):
    a = hex2rgb(h1)
    b = hex2rgb(h2)
    c = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return "#%02x%02x%02x" % c


# ---------- 图标 ----------
def make_hicon(color):
    size = 32
    r, g, b = hex2rgb(color)
    xor = bytearray()
    and_mask = bytearray()
    for y in range(size):
        row = bytearray()
        yy = size - 1 - y
        for x in range(size):
            cx, cy = x - size / 2 + 0.5, yy - size / 2 + 0.5
            inside = (cx * cx + cy * cy) <= (size / 2 - 2) ** 2
            row += bytes((b, g, r, 0)) if inside else bytes((0, 0, 0, 0))
        while len(row) % 4 != 0:
            row += b"\x00"
        xor += row
        bits = 0
        for x in range(size):
            cx, cy = x - size / 2 + 0.5, yy - size / 2 + 0.5
            inside = (cx * cx + cy * cy) <= (size / 2 - 2) ** 2
            bits = (bits << 1) | (0 if inside else 1)
        bits <<= (32 - size)
        and_mask += struct_pack(">I", bits)
    import struct
    bmi = struct.pack("IiiHHIIiiII", 40, size, size * 2, 1, 32,
                      0, len(xor) + len(and_mask), 0, 0, 0, 0)
    dib = bmi + bytes(xor) + bytes(and_mask)
    icon_dir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(dib), 6 + 16)
    ico = icon_dir + entry + dib
    tmp = os.path.join(CONFIG_DIR, f"icon_{color[1:]}.ico")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(ico)
    return win32gui.LoadImage(0, tmp, win32con.IMAGE_ICON, 0, 0,
                              win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)


def struct_pack(fmt, v):
    import struct
    return struct.pack(fmt, v)


class TrafficLightApp:
    WM_NCHITTEST = 0x0084
    HTTRANSPARENT = -1
    WM_TRAY = win32con.WM_USER + 20
    TRAY_ID = 1

    def __init__(self):
        self.cfg = load_config()
        self.cfg["startup"] = get_startup()
        self.state = self.cfg.get("state", "green")
        if self.state not in STATES:
            self.state = "green"
        self.auto_mode = self.cfg.get("auto_mode", True)
        self.penetrate = self.cfg.get("penetrate", True)
        self.always_on_top = self.cfg.get("always_on_top", True)
        self._stop = False
        self._cb_detail = None
        self._anim_phase = 0.0
        self.hwnd = None
        self._tray_hwnd = None
        self._tray_icon = None
        self._taskbar_created = 0
        self._tray_q = queue.Queue()
        self._wm_handlers = {}

        self._register_class()
        self._create_window()
        self._apply_style()
        self._init_tray()
        if self.auto_mode:
            self._start_poller()
        self._start_animation()

    # ---------- 窗口 ----------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_NCHITTEST:
            # 穿透模式：让点击落到背后窗口
            if self.penetrate:
                return self.HTTRANSPARENT
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
        if msg == win32con.WM_PAINT:
            self._on_paint(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _register_class(self):
        self._wnd_class_name = APP_NAME + "Main"
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = self._wnd_class_name
        wc.lpfnWndProc = self._wnd_proc
        wc.style = win32con.CS_VREDRAW | win32con.CS_HREDRAW
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        wc.hbrBackground = win32con.COLOR_WINDOW + 1
        self._wc_atom = win32gui.RegisterClass(wc)

    def _create_window(self):
        sw = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        x = sw - WIN_W - 16
        y = 16
        self.hwnd = win32gui.CreateWindow(
            self._wc_atom, "Agent 状态",
            win32con.WS_POPUP,
            x, y, WIN_W, WIN_H,
            0, 0, win32api.GetModuleHandle(None), None)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
        win32gui.UpdateWindow(self.hwnd)

    def _apply_style(self):
        hwnd = self.hwnd
        # 扩展样式
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        ex |= win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_LAYERED
        ex |= win32con.WS_EX_TOPMOST if self.always_on_top else 0
        # 穿透模式需要 WS_EX_TRANSPARENT 配合 WM_NCHITTEST
        if self.penetrate:
            ex |= 0x00000020  # WS_EX_TRANSPARENT
        else:
            ex &= ~0x00000020
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)
        # 半透明
        win32gui.SetLayeredWindowAttributes(hwnd, 0, int(0.78 * 255), win32con.LWA_ALPHA)
        # 置顶
        flags = (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE |
                 win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE)
        pos = HWND_TOPMOST if self.always_on_top else HWND_NOTOPMOST
        win32gui.SetWindowPos(hwnd, pos, 0, 0, 0, 0, flags)
        # 亚克力磨砂：仅在“可交互（非穿透）”模式启用
        # （DWM 亚克力会接管命中测试，导致穿透失效，故互斥）
        self._apply_acrylic(enable=not self.penetrate)
        self._invalidate()

    def _apply_acrylic(self, enable=True):
        try:
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_SYSTEMBACKDROP_TYPE = 38
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            backdrop = 3 if enable else 1  # 3=acrylic, 1=none
            dark = 1 if enable else 0
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                self.hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(dark)), ctypes.sizeof(ctypes.c_int))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                self.hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                ctypes.byref(ctypes.c_int(backdrop)), ctypes.sizeof(ctypes.c_int))
            if enable:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    self.hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                    ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

    def _invalidate(self):
        user32.InvalidateRect(self.hwnd, None, False)

    # ---------- 绘制 ----------
    def _on_paint(self, hwnd):
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            self._draw(hdc)
        finally:
            win32gui.EndPaint(hwnd, ps)

    def _draw(self, hdc):
        # 用内存 DC 双缓冲
        memdc = win32gui.CreateCompatibleDC(hdc)
        hbmp = win32gui.CreateCompatibleBitmap(hdc, WIN_W, WIN_H)
        old = win32gui.SelectObject(memdc, hbmp)
        try:
            bg = win32gui.CreateSolidBrush(rgb2bgr(hex2rgb("#15151f")))
            win32gui.FillRect(memdc, (0, 0, WIN_W, WIN_H), bg)
            win32gui.DeleteObject(bg)

            # 状态小字（顶部）
            color = STATES[self.state][0]
            self._draw_text(memdc, STATES[self.state][1], 4, 4, WIN_W - 4, 20,
                            color, bold=True, anchor_right=False)

            # 三灯：红(上) 黄(中) 绿(下)
            cx = WIN_W // 2
            lamp_r = 13
            top = 30
            bottom = WIN_H - 8
            gap = (bottom - top) / 3
            lamp_ys = [int(top + gap * 0.5), int(top + gap * 1.5), int(top + gap * 2.5)]
            order = ["red", "yellow", "green"]
            import math
            for key, cy in zip(order, lamp_ys):
                base = STATES[key][0]
                if key == self.state:
                    fill = base
                    # 呼吸光晕
                    amp = {"green": 6, "yellow": 10, "red": 8}[key]
                    pulse = 0.5 + 0.5 * (1 + math.sin(self._anim_phase)) / 2
                    glow = amp * pulse
                    gr, gg, gb = hex2rgb(blend(base, "#15151f", 0.55))
                    glow_brush = win32gui.CreateSolidBrush(
                        (gb << 16) | (gg << 8) | gr)
                    old_brush = win32gui.SelectObject(memdc, glow_brush)
                    null_pen = win32gui.CreatePen(win32con.PS_NULL, 0, 0)
                    old_pen = win32gui.SelectObject(memdc, null_pen)
                    win32gui.Ellipse(memdc,
                                    int(cx - lamp_r - 3 - glow), int(cy - lamp_r - 3 - glow),
                                    int(cx + lamp_r + 3 + glow), int(cy + lamp_r + 3 + glow))
                    win32gui.SelectObject(memdc, old_brush)
                    win32gui.SelectObject(memdc, old_pen)
                    win32gui.DeleteObject(glow_brush)
                    win32gui.DeleteObject(null_pen)
                else:
                    fill = blend(base, "#15151f", 0.78)
                r, g, b = hex2rgb(fill)
                brush = win32gui.CreateSolidBrush((b << 16) | (g << 8) | r)
                ring = win32gui.CreateSolidBrush(rgb2bgr(hex2rgb("#0c0c14")))
                null_pen = win32gui.CreatePen(win32con.PS_NULL, 0, 0)
                old_brush = win32gui.SelectObject(memdc, brush)
                old_pen = win32gui.SelectObject(memdc, null_pen)
                win32gui.Ellipse(memdc, cx - lamp_r, cy - lamp_r,
                                cx + lamp_r, cy + lamp_r)
                win32gui.SelectObject(memdc, old_brush)
                win32gui.SelectObject(memdc, old_pen)
                win32gui.DeleteObject(brush)
                win32gui.DeleteObject(ring)
                win32gui.DeleteObject(null_pen)
                # 高光
                if key == self.state:
                    hr, hg, hb = (235, 235, 245)
                    hb_ = win32gui.CreateSolidBrush((hb << 16) | (hg << 8) | hr)
                    null_pen = win32gui.CreatePen(win32con.PS_NULL, 0, 0)
                    old_brush = win32gui.SelectObject(memdc, hb_)
                    old_pen = win32gui.SelectObject(memdc, null_pen)
                    win32gui.Ellipse(memdc,
                                    int(cx - lamp_r * 0.45), int(cy - lamp_r * 0.55),
                                    int(cx - lamp_r * 0.1), int(cy - lamp_r * 0.15))
                    win32gui.SelectObject(memdc, old_brush)
                    win32gui.SelectObject(memdc, old_pen)
                    win32gui.DeleteObject(hb_)
                    win32gui.DeleteObject(null_pen)

            # 模式提示（右上角极小字）
            self._draw_text(memdc, "AUTO" if self.auto_mode else "MANUAL",
                            WIN_W - 4, 2, 0, 0, "#5b6080", anchor_right=True, size=7)

            # 贴到屏幕
            win32gui.BitBlt(hdc, 0, 0, WIN_W, WIN_H, memdc, 0, 0, win32con.SRCCOPY)
        finally:
            win32gui.SelectObject(memdc, old)
            win32gui.DeleteObject(hbmp)
            win32gui.DeleteDC(memdc)

    def _draw_text(self, hdc, text, x, y, x2, y2, color_hex, bold=False,
                   size=9, anchor_right=False):
        hfont = win32gui.LOGFONT()
        hfont.lfHeight = size
        hfont.lfWeight = win32con.FW_BOLD if bold else win32con.FW_NORMAL
        hfont.lfFaceName = "Microsoft YaHei UI"
        font = win32gui.CreateFontIndirect(hfont)
        old_font = win32gui.SelectObject(hdc, font)
        r, g, b = hex2rgb(color_hex)
        old_color = win32gui.SetTextColor(hdc, (b << 16) | (g << 8) | r)
        old_bk = win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        try:
            if anchor_right:
                rect = (0, y, x, y + 14)
                fmt = win32con.DT_RIGHT | win32con.DT_TOP
                win32gui.DrawText(hdc, text, len(text), rect, fmt)
            else:
                rect = (x, y, x2 if x2 else (x + WIN_W), y2 if y2 else (y + 16))
                win32gui.DrawText(hdc, text, len(text), rect,
                                  win32con.DT_CENTER | win32con.DT_TOP)
        finally:
            win32gui.SetBkMode(hdc, old_bk)
            win32gui.SetTextColor(hdc, old_color)
            win32gui.SelectObject(hdc, old_font)
            win32gui.DeleteObject(font)

    # ---------- 状态 ----------
    def _apply_state(self):
        if self._tray_icon:
            win32gui.DestroyIcon(self._tray_icon)
        self._tray_icon = make_hicon(STATES[self.state][0])
        self._refresh_tray_icon()
        self._invalidate()

    def _set_state(self, key):
        self.auto_mode = False
        self.cfg["auto_mode"] = False
        self._stop = True
        self.state = key
        self.cfg["state"] = key
        self._cb_detail = None
        save_config(self.cfg)
        self._apply_state()

    def _set_state_auto(self, key, detail):
        if not self.auto_mode:
            return
        changed = (self.state != key) or (self._cb_detail != detail)
        self.state = key
        self._cb_detail = detail
        self.cfg["state"] = key
        if changed:
            save_config(self.cfg)
        self._apply_state()

    def _start_animation(self):
        def loop():
            while not self._stop:
                self._anim_phase += 0.08
                if self.hwnd:
                    try:
                        self._invalidate()
                    except Exception:
                        pass
                time.sleep(0.04)
        threading.Thread(target=loop, daemon=True).start()

    # ---------- 自动读取 ----------
    def _start_poller(self):
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        while not self._stop:
            info = read_codebuddy_state()
            if info is not None:
                self._set_state_auto(info["state"], info["detail"])
            time.sleep(3)

    # ---------- 托盘 ----------
    def _init_tray(self):
        self._tray_class_name = APP_NAME + "Tray"
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = self._tray_class_name
        wc.lpfnWndProc = self._tray_wndproc
        self._tray_atom = win32gui.RegisterClass(wc)
        self._tray_hwnd = win32gui.CreateWindow(
            self._tray_atom, APP_NAME, 0, 0, 0, 0, 0, 0, 0,
            win32api.GetModuleHandle(None), None)
        try:
            self._taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
        except Exception:
            self._taskbar_created = 0
        self._tray_icon = make_hicon(STATES[self.state][0])
        self._refresh_tray_icon()
        # 本环境下真正泵窗口消息的是这个 daemon 线程的 PumpMessages
        # （PyInstaller onefile 的主线程消息循环在此环境不生效），
        # 托盘窗口与主窗口的消息都由此线程分发。
        threading.Thread(target=win32gui.PumpMessages, daemon=True).start()

    def _tray_wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == self.WM_TRAY and lparam == win32con.WM_RBUTTONUP:
                # 必须在托盘窗口所属（即本 PumpMessages）线程内直接弹出菜单，
                # 跨线程调用 TrackPopupMenu + SetForegroundWindow 会导致菜单无法弹出。
                self._show_tray_menu()
            elif msg == self.WM_TRAY and lparam == win32con.WM_LBUTTONDBLCLK:
                pass
            elif self._taskbar_created and msg == self._taskbar_created:
                self._refresh_tray_icon()
        except Exception:
            pass
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _refresh_tray_icon(self):
        nid = (self._tray_hwnd, self.TRAY_ID,
               win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
               self.WM_TRAY, self._tray_icon,
               f"Agent: {STATES[self.state][1]}")
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
        except Exception:
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
            except Exception:
                pass

    # ---------- 托盘菜单（TrackPopupMenu） ----------
    def _show_tray_menu(self):
        # 标记：用于自动化验证右键是否触发菜单
        try:
            with open(os.path.join(CONFIG_DIR, ".menu_opened"), "w") as f:
                f.write("1")
        except Exception:
            pass

        menu = win32gui.CreatePopupMenu()
        for key in STATE_ORDER:
            _, title, _ = STATES[key]
            win32gui.AppendMenu(menu, win32con.MF_STRING, 100 + STATE_ORDER.index(key),
                                f"● {title}")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu,
            win32con.MF_STRING | (win32con.MF_CHECKED if self.auto_mode else 0),
            200, "自动读取 CodeBuddy 状态")
        win32gui.AppendMenu(menu, win32con.MF_STRING, 201, "立即刷新状态")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu,
            win32con.MF_STRING | (win32con.MF_CHECKED if self.always_on_top else 0),
            202, "始终置顶")
        win32gui.AppendMenu(menu,
            win32con.MF_STRING | (win32con.MF_CHECKED if self.penetrate else 0),
            203, "鼠标穿透（点击穿透到背后程序）")
        win32gui.AppendMenu(menu,
            win32con.MF_STRING | (win32con.MF_CHECKED if self.cfg.get("startup", False) else 0),
            204, "开机启动")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, 205, "关于")
        win32gui.AppendMenu(menu, win32con.MF_STRING, 299, "退出")

        pos = win32gui.GetCursorPos()
        # TrackPopupMenu 必须在拥有 tray_hwnd 的同一线程内调用（即本回调线程）。
        # 先把自己设为前台窗口，菜单才能正确捕获鼠标；结束后发 WM_NULL 复位。
        win32gui.SetForegroundWindow(self._tray_hwnd)
        cmd = win32gui.TrackPopupMenu(
            menu,
            win32con.TPM_RETURNCMD | win32con.TPM_NONOTIFY | win32con.TPM_RIGHTALIGN,
            pos[0], pos[1], 0, self._tray_hwnd, None)
        win32gui.PostMessage(self._tray_hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)
        # 清理标记
        try:
            os.remove(os.path.join(CONFIG_DIR, ".menu_opened"))
        except Exception:
            pass
        self._handle_menu_cmd(cmd)

    def _handle_menu_cmd(self, cmd):
        if cmd == 299:
            self.quit()
            return
        if cmd == 205:
            self._about()
            return
        if cmd in (100, 101, 102):
            self._set_state(STATE_ORDER[cmd - 100])
            return
        if cmd == 200:
            self._toggle_auto()
            return
        if cmd == 201:
            self._poll_once()
            return
        if cmd == 202:
            self._toggle_top()
            return
        if cmd == 203:
            self._toggle_penetrate()
            return
        if cmd == 204:
            self._toggle_startup()
            return

    def _poll_once(self):
        info = read_codebuddy_state()
        if info is not None:
            self._set_state_auto(info["state"], info["detail"])

    def _toggle_auto(self):
        val = not self.auto_mode
        self.auto_mode = val
        self.cfg["auto_mode"] = val
        save_config(self.cfg)
        if val:
            self._stop = False
            self._cb_detail = None
            self._start_poller()
            self._poll_once()
        else:
            self._stop = True
            self._cb_detail = None
            self._apply_state()

    def _toggle_top(self):
        self.always_on_top = not self.always_on_top
        self.cfg["always_on_top"] = self.always_on_top
        save_config(self.cfg)
        self._apply_style()

    def _toggle_penetrate(self):
        self.penetrate = not self.penetrate
        self.cfg["penetrate"] = self.penetrate
        save_config(self.cfg)
        self._apply_style()

    def _toggle_startup(self):
        val = not self.cfg.get("startup", False)
        try:
            set_startup(val)
            self.cfg["startup"] = val
            save_config(self.cfg)
        except Exception:
            self.cfg["startup"] = not val
            save_config(self.cfg)

    def _about(self):
        # 纯 Win32 无法直接弹 MessageBox 到无主窗口，使用 win32api.MessageBox
        import win32api
        win32api.MessageBox(
            0,
            f"{APP_NAME}\nAgent 工作状态红绿灯（纯 Win32 版）\n\n"
            "· 红/黄/绿三灯直观展示状态\n"
            "· 红灯 = 需确认 / 暂停，需要用户操作\n"
            "· 黄灯 = 工作中 / 忙碌\n"
            "· 绿灯 = 空闲 / 正常\n"
            "· 半透明悬浮置顶，右键托盘控制\n"
            "· 勾选“鼠标穿透”后，点击落到背后程序\n"
            "· 自动读取 CodeBuddy 会话状态",
            "关于", win32con.MB_OK)

    def quit(self):
        self._stop = True
        try:
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_DELETE, (self._tray_hwnd, self.TRAY_ID))
        except Exception:
            pass
        try:
            if self._tray_icon:
                win32gui.DestroyIcon(self._tray_icon)
        except Exception:
            pass
        try:
            user32.PostQuitMessage(0)
        except Exception:
            pass
        try:
            win32gui.DestroyWindow(self.hwnd)
        except Exception:
            pass
        os._exit(0)

    def run(self):
        win32gui.PumpMessages()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = TrafficLightApp()
    app.run()


if __name__ == "__main__":
    main()
