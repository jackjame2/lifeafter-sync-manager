#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
游戏窗口同步器 - Game Window Synchronizer
适用于《明日之后》PC 版的多窗口同步管理。
采用前台置顶轮询发送模式（Foreground Polling）。
搭配 KeyMouseGo 使用：
  1. 在 KeyMouseGo 中录制好脚本
  2. 启动本工具，按 Ctrl+Shift+S 开启同步
  3. 在 KeyMouseGo 中播放脚本 → 所有窗口同步执行
"""

import json, os, sys, time, threading, ctypes
from ctypes import wintypes
from collections import OrderedDict

import keyboard, mouse
import win32gui, win32con, win32process, win32api
import psutil
import license_client

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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


class WindowManager:
    """Finds, activates, and positions game windows."""

    def __init__(self, config):
        self.config = config
        self.windows = []

    def find_windows(self):
        process_name = self.config["process_name"].lower()
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
    """Core synchronizer: hooks global keyboard, forwards via foreground polling."""

    def __init__(self, config_path=None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(
                os.path.abspath(__file__)), "config.json")
        self.config_path = config_path
        self.config = self._load_config()
        self.wm = WindowManager(self.config)
        self.sync_active = False
        self.sending = False
        self._lock = threading.Lock()
        self._saved_fg = None
        self._held_keys = set()
        self._running = True

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

    def refresh_windows(self):
        self.wm.find_windows()
        n = len(self.wm.windows)
        print()
        print(f"  Refreshed: {n} window(s) found.")
        for w in self.wm.windows:
            print(f"      HWND={w['hwnd']:08X}  PID={w['pid']}  {w['title']!r}")
        sys.stdout.flush()

    def apply_layout(self):
        print()
        print("  Applying window layout...")
        sys.stdout.flush()
        self.wm.layout_windows()

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
        print("  Target:", self.config["process_name"])
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
        try:
            while self._running and not quit_event.is_set():
                quit_event.wait(timeout=0.25)
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
        print("    Game Window Synchronizer  v1.1")
        print("    明日之后 (LifeAfter) PC 多窗口同步工具")
        print("    前台置顶轮询模式 - Foreground Polling Mode")
        print("=" * 62)
        print()
        sys.stdout.flush()


if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    print("Starting Game Window Synchronizer...", flush=True)

    if not license_client.run_license_check():
        sys.exit(1)

    try:
        engine = SyncEngine()
        engine.run()
    except KeyboardInterrupt:
        print()
        print("Interrupted.")
    except Exception as e:
        print()
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        input("Press Enter to exit...")
        sys.exit(1)

