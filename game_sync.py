#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
游戏窗口同步器 - Game Window Synchronizer
适用于《明日之后》PC 版的多窗口同步管理。
采用前台置顶轮询发送模式（Foreground Polling）。
搭配 KeymouseGo 使用：
  1. 在 KeymouseGo 中录制好脚本
  2. 启动本工具，GUI 窗口自动激活卡密
  3. 在 KeymouseGo 中播放脚本 → 所有窗口同步执行
"""

import json, os, sys, time, threading, ctypes
from ctypes import wintypes
from collections import OrderedDict

import keyboard, mouse
import win32gui, win32con, win32process, win32api
import psutil
import license_client

import tkinter as tk
from tkinter import ttk

import urllib.request
import ssl

if sys.platform == "win32":
    import io
    if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr is not None and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# When running without a console (e.g. PyInstaller console=False),
# redirect to os.devnull so print() and flush() do not crash.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import logging, datetime
if getattr(sys, "frozen", False):
    _log_dir = os.path.dirname(sys.executable)
else:
    _log_dir = os.path.dirname(os.path.abspath(__file__))
_log_path = os.path.join(_log_dir, "sync_debug.log")
logging.basicConfig(filename=_log_path, level=logging.DEBUG,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger("sync")
_log.info("=== GameSync started ===")

DEFAULT_CONFIG = {
    "process_name": "LifeAfter.exe",
    "window_title_pattern": "",
    "sync_mode": "foreground_polling",
    "poll_interval_ms": 12,
    "activate_delay_ms": 8,
    "key_hold_ms": 20,
    "return_focus": True,
    "return_focus_delay_ms": 30,
    "sync_hotkey": "ctrl+shift+s",
    "quit_hotkey": "ctrl+shift+q",
    "layout_hotkey": "ctrl+shift+l",
    "refresh_windows_hotkey": "ctrl+shift+r",
    "sync_enabled_keys": "all",
    "sync_mouse": False,
    "sync_mouse_clicks": False,
    "sync_mouse_movement": False,
}

_VK_MAP = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10,
    "ctrl": 0x11, "alt": 0x12, "pause": 0x13, "caps lock": 0x14,
    "esc": 0x1B, "space": 0x20, "page up": 0x21, "page down": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "print screen": 0x2C, "insert": 0x2D,
    "delete": 0x2E,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
    "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
    "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59,
    "z": 0x5A,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "num lock": 0x90, "scroll lock": 0x91,
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC,
    ".": 0xBE, "/": 0xBF, "`": 0xC0,
}

def _name_to_vk(name):
    if not name:
        return 0
    low = name.lower().strip()
    if low in _VK_MAP:
        return _VK_MAP[low]
    if len(low) == 1 and low.isalpha():
        return ord(low.upper())
    try:
        sc = keyboard.key_to_scan_codes(name)
        if sc:
            return win32api.MapVirtualKey(sc[0], 1)
    except Exception:
        pass
    return 0

KEYMOUSEGO_URLS = [
    "https://github.com/taojy123/KeymouseGo/releases/download/v5_2_1/KeymouseGo_v5_2_1-win.exe",
    "https://github.com/taojy123/KeymouseGo/releases/download/v5.2.1/KeymouseGo_v5_2_1-win.exe",
]
KEYMOUSEGO_FILENAME = "KeymouseGo_v5_2_1-win.exe"

# How long the app may run while the license server is unreachable, measured from
# the last successful ONLINE verification. Bounds offline abuse / domain-blocking.
_OFFLINE_GRACE_SEC = 3600  # 1 hour

def _app_dir():
    """Get the application directory (works both as script and PyInstaller exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def download_keymousego(status_cb=None, progress_cb=None):
    dest = os.path.join(_app_dir(), KEYMOUSEGO_FILENAME)
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        if status_cb:
            status_cb("KeymouseGo ready")
        return dest
    if status_cb:
        status_cb("Downloading KeymouseGo (~57MB)...")
    ctx = ssl.create_default_context()
    for url in KEYMOUSEGO_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                loaded = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        loaded += len(chunk)
                        if progress_cb and total > 0:
                            progress_cb(loaded, total)
            if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
                if status_cb:
                    status_cb("KeymouseGo download complete")
                return dest
        except Exception:
            if status_cb:
                status_cb("Download failed, trying mirror...")
            continue
    if status_cb:
        status_cb("KeymouseGo download failed")
    return None


class LicenseActivationGUI:

    def __init__(self):
        self._result = False
        self._activated_key = None
        self._root = None

    def show(self):
        _log.info("show() called, base_dir=%s", _app_dir())
        self._root = tk.Tk()
        self._root.title("Game Sync - Activate")
        self._root.resizable(False, False)
        self._root.configure(bg="#f0f0f0")
        w, h = 440, 350
        x = (self._root.winfo_screenwidth() - w) // 2
        y = (self._root.winfo_screenheight() - h) // 2
        self._root.geometry(f"{w}x{h}+{x}+{y}")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._root.after(100, self._start_flow)
        self._root.mainloop()
        return self._result, self._activated_key

    def _on_close(self):
        self._result = False
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None


    def _build_ui(self):
        r = self._root
        tk.Label(r, text="Game Window Synchronizer",
                 font=("Microsoft YaHei", 17, "bold"),
                 bg="#f0f0f0", fg="#333").pack(pady=(22, 3))
        tk.Label(r, text="License Activation",
                 font=("Microsoft YaHei", 9),
                 bg="#f0f0f0", fg="#999").pack()

        sf = tk.Frame(r, bg="#f0f0f0")
        sf.pack(pady=(18, 8), fill=tk.X, padx=36)

        self._status_lbl = tk.Label(sf, text="Preparing...",
                                    font=("Microsoft YaHei", 9),
                                    bg="#f0f0f0", fg="#555",
                                    anchor=tk.W, justify=tk.LEFT,
                                    wraplength=360)
        self._status_lbl.pack(fill=tk.X)

        self._progress = ttk.Progressbar(sf, mode="determinate", length=368)
        self._progress.pack(fill=tk.X, pady=(6, 0))

        self._key_frame = tk.Frame(r, bg="#f0f0f0")
        tk.Label(self._key_frame, text="License Key",
                 font=("Microsoft YaHei", 10),
                 bg="#f0f0f0", fg="#555").pack(anchor=tk.W, pady=(0, 4))

        self._key_entry = tk.Entry(self._key_frame, font=("Consolas", 14),
                                   justify=tk.CENTER,
                                   relief=tk.SOLID, bd=1, width=26)
        self._key_entry.pack(fill=tk.X, ipady=3)
        self._key_entry.bind("<Return>", lambda e: self._activate())

        self._key_frame.pack(pady=(4, 6), fill=tk.X, padx=36)
        self._key_frame.pack_forget()

        self._btn_frame = tk.Frame(r, bg="#f0f0f0")
        self._act_btn = tk.Button(self._btn_frame, text="Activate",
                                  font=("Microsoft YaHei", 11, "bold"),
                                  bg="#4a90d9", fg="white",
                                  activebackground="#357abd",
                                  activeforeground="white",
                                  relief=tk.FLAT, padx=40, pady=6,
                                  cursor="hand2", command=self._activate)
        self._act_btn.pack()
        self._btn_frame.pack(pady=(0, 6))
        self._btn_frame.pack_forget()

        self._hint_lbl = tk.Label(r, text="",
                                  font=("Microsoft YaHei", 8),
                                  bg="#f0f0f0", fg="#aaa")
        self._hint_lbl.pack()

        tk.Label(r, text="Contact admin to purchase a license key",
                 font=("Microsoft YaHei", 8),
                 bg="#f0f0f0", fg="#bbb").pack(side=tk.BOTTOM, pady=(0, 14))

    def _start_flow(self):
        self._root.after(50, self._on_ready)

    def _download_kmg(self):
        def prog(done, total):
            pct = int(done / total * 100)
            self._root.after(0, lambda: self._progress.config(value=pct))
        def stat(msg):
            self._root.after(0, lambda: self._status_lbl.config(text=msg))
        download_keymousego(status_cb=stat, progress_cb=prog)
        self._root.after(0, self._on_ready)

    def _on_ready(self):
        try:
            _log.info("_on_ready() called")
            self._progress.pack_forget()
            saved = license_client.load_saved_key()
            _log.info("saved_key present: %s", bool(saved))
            if saved:
                self._status_lbl.config(text="Verifying saved key...")
                self._root.update()
                _log.info("Starting verify thread for saved key")
                def _verify_thread():
                    try:
                        hwid = license_client.get_hwid()
                    except Exception:
                        hwid = None
                    result = license_client.verify_key(saved, hwid)
                    _log.info("verify result: valid=%s status=%s", result.get("valid"), result.get("status"))
                    if result.get("valid") and not result.get("error"):
                        license_client.mark_online_ok()
                        self._activated_key = saved
                        self._result = True
                        self._root.after(0, lambda: self._status_lbl.config(text='Key valid! Starting...', fg='#2a2'))
                        self._root.after(800, self._on_success)
                    elif result.get("status") == "revoked":
                        self._root.after(0, lambda: self._status_lbl.config(text='Key revoked by admin', fg='#c33'))
                        self._root.after(0, lambda: self._hint_lbl.config(text='Contact admin for a new key'))
                        self._root.after(0, self._show_input)
                    elif result.get("status") == "expired":
                        self._root.after(0, lambda: self._status_lbl.config(text='Key expired', fg='#c33'))
                        self._root.after(0, lambda: self._hint_lbl.config(text='Contact admin to renew'))
                        self._root.after(0, self._show_input)
                    elif result.get("status") == "device_mismatch":
                        self._root.after(0, lambda: self._status_lbl.config(text='Key bound to another device', fg='#c33'))
                        self._root.after(0, lambda: self._hint_lbl.config(text='Contact admin to unbind'))
                        self._root.after(0, self._show_input)
                    elif result.get("_offline"):
                        if license_client.offline_grace_ok(_OFFLINE_GRACE_SEC):
                            self._activated_key = saved
                            self._result = True
                            self._root.after(0, lambda: self._status_lbl.config(text="Offline mode - starting...", fg="#2a2"))
                            self._root.after(800, self._on_success)
                        else:
                            self._root.after(0, lambda: self._status_lbl.config(text='Network required to verify license', fg='#c33'))
                            self._root.after(0, lambda: self._hint_lbl.config(text='Connect to the internet and retry'))
                            self._root.after(0, self._show_input)
                    else:
                        self._root.after(0, self._show_input)
                threading.Thread(target=_verify_thread, daemon=True).start()
                return
            _log.info("No saved key, showing input")
            self._show_input()
        except Exception as e:
            _log.error("_on_ready failed: %s", e, exc_info=True)
            self._show_input()

    def _show_input(self):
        self._status_lbl.config(text="Enter license key to activate")
        self._key_frame.pack(pady=(4, 6), fill=tk.X, padx=36)
        self._btn_frame.pack(pady=(0, 6))
        self._key_entry.delete(0, tk.END)
        self._key_entry.focus_set()

    def _activate(self):
        key = self._key_entry.get().strip().upper()
        if not key or len(key) < 8:
            self._status_lbl.config(text="Please enter a valid key (min 8 chars)", fg="#c33")
            return
        self._act_btn.config(state=tk.DISABLED, text="Verifying...")
        self._status_lbl.config(text="Starting...", fg="#555")
        self._progress.pack(fill=tk.X, pady=(6, 0))
        self._root.update()

        def _thread():
            # Step 1: Download KeymouseGo in background (instant if already exists)
            def _dl_stat(msg):
                self._root.after(0, lambda: self._status_lbl.config(text=msg))
            def _dl_prog(done, total):
                if total > 0:
                    pct = int(done / total * 100)
                    self._root.after(0, lambda: self._progress.config(value=pct))
            download_keymousego(status_cb=_dl_stat, progress_cb=_dl_prog)
            self._root.after(0, lambda: self._progress.pack_forget())

            # Step 2: Verify and activate license
            self._root.after(0, lambda: self._status_lbl.config(text="Connecting to server...", fg="#555"))
            hwid = license_client.get_hwid()
            v = license_client.verify_key(key, hwid)
            if v.get("error"):
                self._root.after(0, lambda: self._on_result(
                    False, "Error: " + str(v.get("error", "verification failed"))))
                return
            if not v.get("valid"):
                self._root.after(0, lambda: self._on_result(
                    False, "Invalid: " + str(v.get("message", "invalid key"))))
                return
            a = license_client.activate_key(key, hwid)
            if a.get("success"):
                license_client.save_key(key)
                license_client.mark_online_ok()
                self._root.after(0, lambda: self._on_result(
                    True, "Activated! Starting..."))
            else:
                self._root.after(0, lambda: self._on_result(
                    False, "Failed: " + str(a.get("message", "activation failed"))))

        threading.Thread(target=_thread, daemon=True).start()

    def _on_result(self, success, msg):
        self._act_btn.config(state=tk.NORMAL, text="Activate")
        if success:
            self._status_lbl.config(text=msg, fg="#2a2")
            self._activated_key = True
            self._result = True
            self._hint_lbl.config(text="")
            self._root.after(800, self._on_success)
        else:
            self._status_lbl.config(text=msg, fg="#c33")
            self._key_entry.delete(0, tk.END)
            self._key_entry.focus_set()

    def _on_success(self):
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None


class LicenseGuard:

    CHECK_INTERVAL_SEC = 60  # re-check every 60s so a remote revoke/expiry kills the app within ~1 min

    def __init__(self, active_key):
        self._active_key = active_key
        self._hwid = None
        self._valid = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(timeout=self.CHECK_INTERVAL_SEC):
            if not self._active_key:
                continue
            if self._hwid is None:
                try:
                    self._hwid = license_client.get_hwid()
                except Exception:
                    self._hwid = ""
            try:
                result = license_client.verify_key(self._active_key, self._hwid)
            except Exception:
                continue
            if result.get("valid"):
                license_client.mark_online_ok()
                continue
            # Server unreachable: tolerate only within the bounded offline grace window.
            if result.get("_offline"):
                if license_client.offline_grace_ok(_OFFLINE_GRACE_SEC):
                    continue
            # Definitive invalid (revoked / expired / device mismatch) OR offline past grace.
            self._valid = False
            self._alert(result)
            self._stop.set()
            return

    def _alert(self, result):
        status = result.get("status", "")
        if status == "revoked":
            ctypes.windll.user32.MessageBoxW(
                0, "This license key has been revoked by admin.\n\nThe program will now exit.",
                "Key Revoked", 0x00000010)
        elif status == "expired":
            ctypes.windll.user32.MessageBoxW(
                0, "This license key has expired.\n\nThe program will now exit.",
                "Key Expired", 0x00000010)
        elif status == "device_mismatch":
            ctypes.windll.user32.MessageBoxW(
                0, "This license key is bound to a different device.\n\nThe program will now exit.",
                "Device Mismatch", 0x00000010)
        else:
            ctypes.windll.user32.MessageBoxW(
                0, "License verification failed.\n\nThe program will now exit.",
                "Verification Failed", 0x00000010)

    def is_valid(self):
        return self._valid

    def shutdown(self):
        self._stop.set()


class WindowManager:

    def __init__(self, config):
        self.config = config
        self.windows = []

    def find_windows(self):
        process_name = self.config['process_name'].lower()
        title_pattern = self.config.get("window_title_pattern", "").lower()
        found = []
        def _enum(hwnd, _ctx):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                proc = psutil.Process(pid)
                if proc.name().lower() == process_name:
                    title = win32gui.GetWindowText(hwnd)
                    if not title_pattern or title_pattern in title.lower():
                        found.append({"hwnd": hwnd, "title": title, "pid": pid})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            return True
        win32gui.EnumWindows(_enum, None)
        found.sort(key=lambda w: w["hwnd"])
        self.windows = found
        return found

    def bring_to_foreground(self, hwnd):
        if not win32gui.IsWindow(hwnd):
            return False
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        fg = win32gui.GetForegroundWindow()
        cur_tid = win32api.GetCurrentThreadId()
        if fg:
            fg_tid = win32process.GetWindowThreadProcessId(fg)[0]
            if cur_tid != fg_tid:
                try:
                    win32process.AttachThreadInput(cur_tid, fg_tid, True)
                except Exception:
                    pass
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
        if fg and cur_tid != win32process.GetWindowThreadProcessId(fg)[0]:
            try:
                win32process.AttachThreadInput(cur_tid,
                    win32process.GetWindowThreadProcessId(fg)[0], False)
            except Exception:
                pass
        settle_ms = self.config.get("activate_delay_ms", 10)
        time.sleep(settle_ms / 1000.0)
        return True

    def layout_windows(self, rows=None, cols=None):
        if not self.windows:
            self.find_windows()
        if not self.windows:
            print("  [layout] No windows found.")
            return
        n = len(self.windows)
        if rows is None or cols is None:
            cols = int(n ** 0.5 + 0.5)
            if cols == 0:
                cols = 1
            rows = (n + cols - 1) // cols
        mon_info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))
        work = mon_info.get("Work", (0, 0, 1920, 1040))
        mx, my, mw, mh = work[0], work[1], work[2] - work[0], work[3] - work[1]
        cell_w = mw // cols
        cell_h = mh // rows
        for i, w in enumerate(self.windows):
            hwnd = w["hwnd"]
            r = i // cols
            c = i % cols
            x = mx + c * cell_w
            y = my + r * cell_h
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            style &= ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME |
                       win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX)
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOP,
                                  x, y, cell_w, cell_h,
                                  win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW)
        print(f"  [layout] {n} windows tiled in {rows}x{cols} grid, "
              f"cell {cell_w}x{cell_h}")

    def send_key_post_message(self, hwnd, vk_code, scan_code, event_type):
        if event_type == "down":
            msg = win32con.WM_KEYDOWN
            lparam = (scan_code << 16) | 0x00000001
        else:
            msg = win32con.WM_KEYUP
            lparam = (scan_code << 16) | 0xC0000001
        win32gui.PostMessage(hwnd, msg, vk_code, lparam)

    def find_window_by_class(self, class_name):
        found = []
        def _enum(hwnd, _ctx):
            if win32gui.GetClassName(hwnd) == class_name:
                title = win32gui.GetWindowText(hwnd)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                found.append({"hwnd": hwnd, "title": title, "pid": pid})
            return True
        win32gui.EnumWindows(_enum, None)
        return found


class SyncEngine:

    def __init__(self, config_path=None, license_guard=None):
        if config_path is None:
            config_path = os.path.join(_app_dir(), "config.json")
        self.config_path = config_path
        self.config = self._load_config()
        self.wm = WindowManager(self.config)
        self.sync_active = False
        self.sending = False
        self._lock = threading.Lock()
        self._saved_fg = None
        self._held_keys = set()
        self._running = True
        self._license_guard = license_guard
        self._ui_sync_label = None
        self._ui_status_dot = None
        self._ui_win_label = None
        self._ui_root = None

    def _load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        return dict(DEFAULT_CONFIG)

    def toggle_sync(self):
        self.sync_active = not self.sync_active
        if self.sync_active:
            self.wm.find_windows()
            n = len(self.wm.windows)
            if n == 0:
                print()
                print("  [!] No game windows found.")
                print(f"      Looking for process: {self.config['process_name']}")
                print("      Tip: open the game first, then press Ctrl+Shift+R to re-scan.")
                print()
                sys.stdout.flush()
                self.sync_active = False
                self._update_ui()
                return
            print()
            print(f"  >>> SYNC ON  ({n} windows) <<<")
            for w in self.wm.windows:
                print(f"      HWND={w['hwnd']:08X}  PID={w['pid']}  {w['title']!r}")
        else:
            self._held_keys.clear()
            print()
            print("  <<< SYNC OFF >>>")
        sys.stdout.flush()
        self._update_ui()

    def refresh_windows(self):
        self.wm.find_windows()
        n = len(self.wm.windows)
        print()
        print(f"  Refreshed: {n} window(s) found.")
        for w in self.wm.windows:
            print(f"      HWND={w['hwnd']:08X}  PID={w['pid']}  {w['title']!r}")
        sys.stdout.flush()
        self._update_ui()

    def apply_layout(self):
        print()
        print("  Applying window layout...")
        sys.stdout.flush()
        self.wm.layout_windows()
        self._update_ui()


    def _update_ui(self, extra_msg=None):
        if not self._ui_root or not self._ui_sync_label:
            return
        def _upd():
            if self.sync_active:
                n = len(self.wm.windows)
                self._ui_sync_label.config(text=f"Sync: ON  ({n} windows)", fg="#a6e3a1")
                self._ui_status_dot.config(fg="#a6e3a1")
            else:
                self._ui_sync_label.config(text="Sync: OFF", fg="#585b70")
                self._ui_status_dot.config(fg="#f38ba8")
            self.wm.find_windows()
            n = len(self.wm.windows)
            self._ui_win_label.config(text=f"Windows: {n} found")
        self._ui_root.after(0, _upd)
    def _should_sync_key(self, event_name):
        enabled = self.config["sync_enabled_keys"]
        if enabled == "all":
            return True
        if isinstance(enabled, list):
            return event_name.lower() in [k.lower() for k in enabled]
        return True

    def _forward_key(self, event):
        if not self.sync_active:
            return
        windows = self.wm.windows
        if not windows:
            return
        mode = self.config.get("sync_mode", "foreground_polling")
        key_name = event.name
        scan_code = event.scan_code
        vk_code = _name_to_vk(key_name)
        hold_ms = self.config.get("key_hold_ms", 20) / 1000.0
        poll_ms = self.config.get("poll_interval_ms", 12) / 1000.0
        try:
            current_fg = win32gui.GetForegroundWindow()
        except Exception:
            current_fg = None
        self._saved_fg = current_fg
        for w in windows:
            hwnd = w["hwnd"]
            if not win32gui.IsWindow(hwnd):
                continue
            if hwnd == current_fg:
                continue
            try:
                if mode == "foreground_polling":
                    if not self.wm.bring_to_foreground(hwnd):
                        continue
                    keyboard.press(scan_code)
                    if hold_ms > 0:
                        time.sleep(hold_ms)
                    keyboard.release(scan_code)
                elif mode == "post_message":
                    self.wm.send_key_post_message(hwnd, vk_code, scan_code, "down")
                    if hold_ms > 0:
                        time.sleep(hold_ms)
                    self.wm.send_key_post_message(hwnd, vk_code, scan_code, "up")
            except Exception:
                pass
            if poll_ms > 0:
                time.sleep(poll_ms)
        if (self.config.get("return_focus", True)
                and self._saved_fg
                and win32gui.IsWindow(self._saved_fg)
                and self._saved_fg != win32gui.GetForegroundWindow()):
            return_delay = self.config.get("return_focus_delay_ms", 30) / 1000.0
            time.sleep(return_delay)
            try:
                self.wm.bring_to_foreground(self._saved_fg)
            except Exception:
                pass

    def _on_key_event(self, event):
        if not self._running:
            return
        if self.sending:
            return
        if event.event_type != keyboard.KEY_DOWN:
            return
        key_name = event.name
        if not key_name or not self._should_sync_key(key_name):
            return
        if event.name in self._held_keys:
            return
        self._held_keys.add(event.name)
        if not self.sync_active:
            return
        self.sending = True
        try:
            self._forward_key(event)
        finally:
            self.sending = False

    def _on_key_release(self, event):
        if event.name in self._held_keys:
            self._held_keys.discard(event.name)

    def _on_mouse_event(self, event):
        if not self.sync_active or self.sending:
            return
        if not self.config.get("sync_mouse", False):
            return
        if not isinstance(event, mouse.ButtonEvent):
            return
        if not self.config.get("sync_mouse_clicks", False):
            return
        self.sending = True
        try:
            windows = self.wm.windows
            current_fg = win32gui.GetForegroundWindow()
            for w in windows:
                hwnd = w["hwnd"]
                if hwnd == current_fg:
                    continue
                self.wm.bring_to_foreground(hwnd)
                if event.event_type == "down":
                    mouse.click(event.button)
                time.sleep(0.005)
            if self.config.get("return_focus", True):
                self.wm.bring_to_foreground(current_fg)
        finally:
            self.sending = False

    def run(self):
        self._print_banner()

        if self._license_guard and not self._license_guard.is_valid():
            print("  License invalid. Exiting.")
            sys.stdout.flush()
            return

        keyboard.add_hotkey(self.config["sync_hotkey"],
                           self.toggle_sync, suppress=False)
        keyboard.add_hotkey(self.config["refresh_windows_hotkey"],
                           self.refresh_windows, suppress=False)
        keyboard.add_hotkey(self.config["layout_hotkey"],
                           self.apply_layout, suppress=False)
        print(f"  Sync toggle :  {self.config['sync_hotkey']}")
        print(f"  Re-scan     :  {self.config['refresh_windows_hotkey']}")
        print(f"  Tile layout :  {self.config['layout_hotkey']}")
        print(f"  Quit        :  {self.config['quit_hotkey']}")
        print()
        mode_label = ("Foreground Polling" if self.config["sync_mode"] ==
                      "foreground_polling" else "PostMessage (background)")
        print("  Mode:  ", mode_label)
        print("  Target:", self.config['process_name'])
        print()
        keyboard.hook(self._on_key_event, suppress=False)
        keyboard.on_release(self._on_key_release)
        if self.config.get("sync_mouse", False):
            mouse.hook(self._on_mouse_event)
            print("  Mouse sync: ENABLED")
        print("  Ready. Hooks active. Press Ctrl+Shift+S to start syncing.")
        print()
        sys.stdout.flush()
        quit_event = threading.Event()
        def _do_quit():
            quit_event.set()
        keyboard.add_hotkey(self.config["quit_hotkey"], _do_quit, suppress=False)

        _last_guard_tick = time.time()
        _GUARD_TICK_SEC = 5

        try:
            while self._running and not quit_event.is_set():
                quit_event.wait(timeout=0.25)

                if self._license_guard:
                    now = time.time()
                    if now - _last_guard_tick > _GUARD_TICK_SEC:
                        _last_guard_tick = now
                        if not self._license_guard.is_valid():
                            print()
                            print("  [!] License revoked. Exiting...")
                            sys.stdout.flush()
                            self._running = False
                            quit_event.set()
        except KeyboardInterrupt:
            pass
        print()
        print("Shutting down...")
        sys.stdout.flush()
        self._running = False
        keyboard.unhook_all()
        try:
            mouse.unhook_all()
        except Exception:
            pass
        print("Goodbye.")

    def _print_banner(self):
        print()
        print("=" * 62)
        print("    Game Window Synchronizer  v1.2")
        print("    LifeAfter PC multi-window sync")
        print("    Foreground Polling Mode")
        print("=" * 62)
        print()
        sys.stdout.flush()


if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    print("Starting Game Window Synchronizer...", flush=True)
    _log.info("main() entry")

    gui = LicenseActivationGUI()
    success, active_key = gui.show()

    if not success or not active_key:
        print("Activation cancelled. Exiting.", flush=True)
        sys.exit(1)

    saved_key = license_client.load_saved_key()
    guard = LicenseGuard(saved_key) if saved_key else None

    # Launch KeymouseGo before engine starts
    kmg_path = os.path.join(_app_dir(), KEYMOUSEGO_FILENAME)
    if os.path.exists(kmg_path):
        try:
            import subprocess as _sp
            _sp.Popen([kmg_path], shell=False, creationflags=0x08000000)
            print("  [KeymouseGo] Launched.", flush=True)
        except Exception as _e:
            print(f"  [warn] KeymouseGo launch failed: {_e}", flush=True)
    else:
        print("  [warn] KeymouseGo not found. Download it from:", flush=True)
        print("        https://github.com/taojy123/KeymouseGo/releases", flush=True)

    # Create status window that stays visible
    status_root = tk.Tk()
    status_root.title("GameSync - Running")
    status_root.resizable(False, False)
    status_root.configure(bg="#1e1e2e")
    sw, sh = 360, 360
    sx = (status_root.winfo_screenwidth() - sw) // 2
    sy = (status_root.winfo_screenheight() - sh) // 2
    status_root.geometry(f"{sw}x{sh}+{sx}+{sy}")
    status_root.protocol("WM_DELETE_WINDOW", lambda: _stop_engine())

    tk.Label(status_root, text="GameSync", font=("Microsoft YaHei", 20, "bold"),
             bg="#1e1e2e", fg="#cdd6f4").pack(pady=(24, 2))
    tk.Label(status_root, text="Multi-window Sync Engine", font=("Microsoft YaHei", 9),
             bg="#1e1e2e", fg="#a6adc8").pack()

    # Separator
    tk.Frame(status_root, height=1, bg="#45475a").pack(fill=tk.X, padx=30, pady=(14, 10))

    # Status indicator
    status_frame = tk.Frame(status_root, bg="#1e1e2e")
    status_frame.pack(pady=(4, 2))
    status_dot = tk.Label(status_frame, text="●", font=("", 14),
                          bg="#1e1e2e", fg="#f38ba8")
    status_dot.pack(side=tk.LEFT, padx=(0, 6))
    status_text = tk.Label(status_frame, text="Engine Running", font=("Microsoft YaHei", 12, "bold"),
                           bg="#1e1e2e", fg="#cdd6f4")
    status_text.pack(side=tk.LEFT)

    # Sync status
    sync_frame = tk.Frame(status_root, bg="#1e1e2e")
    sync_frame.pack(pady=(2, 8))
    sync_label = tk.Label(sync_frame, text="Sync: OFF", font=("Microsoft YaHei", 10),
                          bg="#1e1e2e", fg="#585b70")
    sync_label.pack()

    # Hotkeys
    hk_frame = tk.Frame(status_root, bg="#313244", highlightbackground="#45475a", highlightthickness=1)
    hk_frame.pack(fill=tk.X, padx=24, pady=(6, 10), ipady=10)
    hotkeys = [
        ("Ctrl+Shift+S", "Toggle Sync"),
        ("Ctrl+Shift+Q", "Quit"),
        ("Ctrl+Shift+L", "Tile Windows"),
        ("Ctrl+Shift+R", "Rescan"),
    ]
    for hk, desc in hotkeys:
        row = tk.Frame(hk_frame, bg="#313244")
        row.pack(fill=tk.X, padx=16, pady=2)
        tk.Label(row, text=hk, font=("Consolas", 10, "bold"),
                 bg="#313244", fg="#89b4fa", width=14, anchor=tk.W).pack(side=tk.LEFT)
        tk.Label(row, text=desc, font=("Microsoft YaHei", 10),
                 bg="#313244", fg="#a6adc8").pack(side=tk.LEFT)

    # Window count
    win_label = tk.Label(status_root, text="Windows: 0 found", font=("Microsoft YaHei", 9),
                         bg="#1e1e2e", fg="#585b70")
    win_label.pack(pady=(4, 12))

    # Quit button
    quit_btn = tk.Button(status_root, text="Quit", font=("Microsoft YaHei", 10, "bold"),
                         bg="#f38ba8", fg="#1e1e2e", activebackground="#eba0ac",
                         relief=tk.FLAT, padx=30, pady=4, cursor="hand2",
                         command=lambda: _stop_engine())
    quit_btn.pack(pady=(0, 20))

    engine = SyncEngine(license_guard=guard)
    engine._ui_sync_label = sync_label
    engine._ui_status_dot = status_dot
    engine._ui_win_label = win_label
    engine._ui_root = status_root
    engine._running = True
    engine_thread = threading.Thread(target=engine.run, daemon=True)
    engine_thread.start()

    def _stop_engine():
        engine._running = False
        if guard:
            guard.shutdown()
        try:
            status_root.destroy()
        except Exception:
            pass

    # Periodic status updates
    def _update_status():
        if not engine._running:
            return
        # Check sync state
        if engine.sync_active:
            sync_label.config(text="Sync: ON  (" + str(len(engine.wm.windows)) + " windows)", fg="#a6e3a1")
            status_dot.config(fg="#a6e3a1")
        else:
            sync_label.config(text="Sync: OFF", fg="#585b70")
            status_dot.config(fg="#f38ba8")
        # Update window count
        engine.wm.find_windows()
        n = len(engine.wm.windows)
        win_label.config(text=f"Windows: {n} found")
        status_root.after(1500, _update_status)

    status_root.after(1000, _update_status)
    status_root.mainloop()

