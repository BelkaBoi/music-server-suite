from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import platform as _platform
import shlex

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
DEFAULT_BASE = Path.home() / ("Navidrome" if IS_WINDOWS else "navidrome")
DEFAULT_SYNC = Path.home() / "ultimate-music-sync"
BASE = Path(os.environ.get("MUSICSERVER_BASE", DEFAULT_BASE))
PUBLIC_URL = os.environ.get("MUSICSERVER_PUBLIC_URL", "https://music.example.com").rstrip("/")
TOOLS = BASE / "tools"
SYNC = Path(os.environ.get("UMS_PROJECT", DEFAULT_SYNC))
CAP_TASKS = IS_WINDOWS  # schtasks-based automation toggles / task triggers
CAP_TUNNEL = IS_WINDOWS or IS_LINUX  # systemctl via WSL or native
NAVIDROME_BIN = BASE / ("navidrome.exe" if IS_WINDOWS else "navidrome")
NAVIDROME_PROCESS = "navidrome.exe" if IS_WINDOWS else "navidrome"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

AUTOMATION_TASKS = {
    "hourly_sync": r"\Music Library\Hourly Spotify Sync",
    "daily_ai_dj": r"\Navidrome\DailyAI-DJ",
    "public_health": r"\Cloudflared\PublicHealth",
    "navidrome_watchdog": r"\Navidrome\SilentWatchdog",
}

SYNC_VENV_PYTHON = SYNC / r".venv\Scripts\python.exe"
ACQ_LOCK = SYNC / r".real-state\acquire.lock"
ACQ_REPORT = SYNC / r".real-state\auto-acquire-report.json"
SYNC_REPORT = SYNC / r".real-state\hourly-sync-report.json"
SYNC_LOG = SYNC / r".real-state\hourly-sync.log"
ACQ_LOG = SYNC / r".real-state\auto-acquire.log"
ACQ_LOCK_MAX_AGE_S = 6 * 3600

if IS_WINDOWS:
    STARTUP_DIR = Path.home() / r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
    STARTUP_ITEMS = {
        "navidrome": STARTUP_DIR / "Navidrome Silent Startup.cmd",
        "cloudflared": STARTUP_DIR / "Cloudflared WSL Keepalive.cmd",
    }
elif IS_MAC:
    STARTUP_DIR = Path.home() / "Library" / "LaunchAgents"
    STARTUP_ITEMS = {
        "navidrome": STARTUP_DIR / "music.navidrome.plist",
        "cloudflared": STARTUP_DIR / "music.cloudflared.plist",
    }
else:
    STARTUP_DIR = Path.home() / ".config" / "autostart"
    STARTUP_ITEMS = {
        "navidrome": STARTUP_DIR / "music-navidrome.desktop",
        "cloudflared": STARTUP_DIR / "music-cloudflared.desktop",
    }

_WATCHDOG_VBS = BASE / "silent-watchdog.vbs"
_TUNNEL_KEEPALIVE_VBS = BASE / "silent-cloudflared-keepalive.vbs"
STARTUP_CONTENT = {
    "navidrome": f'@echo off\r\nwscript.exe "{_WATCHDOG_VBS}"\r\n',
    "cloudflared": f'@echo off\r\nwscript.exe "{_TUNNEL_KEEPALIVE_VBS}"\r\n',
}

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>music.{key}</string>
  <key>ProgramArguments</key><array>
    <string>{program}</string>{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><{keep_alive}/>
</dict></plist>
"""

def _plist_for(key: str, program: str, args: list[str], keep_alive: bool) -> str:
    arg_xml = "".join(f"\n    <string>{a}</string>" for a in args)
    return PLIST_TEMPLATE.format(key=key, program=program, args=arg_xml,
                                 keep_alive=str(keep_alive).lower())

LAUNCHD_PLIST = {
    "navidrome": _plist_for("navidrome", str(NAVIDROME_BIN),
                            ["--configfile", str(BASE / "navidrome.toml")], keep_alive=True),
    "cloudflared": _plist_for("cloudflared", "/usr/local/bin/cloudflared",
                              ["tunnel", "--config", str(BASE / "cloudflared.yml")], keep_alive=True),
}

DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name=Music {name}
Exec={exec_line}
X-GNOME-Autostart-enabled=true
"""

def _desktop_for(exec_line: str) -> str:
    return DESKTOP_TEMPLATE.format(name=exec_line.split()[0], exec_line=exec_line)

XDG_AUTOSTART = {
    "navidrome": _desktop_for(f'"{NAVIDROME_BIN}" --configfile "{BASE / "navidrome.toml"}"'),
    "cloudflared": _desktop_for("cloudflared tunnel --config " + shlex.quote(str(BASE / "cloudflared.yml"))),
}

ACTION_BUTTON_LABELS = {
    "Start", "Stop", "Open Local", "Restart", "Open Remote",
    "Sync Now", "Acquire Now", "Open Missing List", "Refresh",
    "Music Folder", "Sync Reports", "Server Folder", "Run AI DJ",
    "Open Sync Log", "Open Acquire Log",
}


def run_hidden(command: list[str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    kwargs: dict = dict(
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(command, **kwargs)


def spawn_hidden(command: list[str], cwd: str | None = None) -> None:
    """Launch a fully detached, windowless child on every platform."""
    kwargs: dict = dict(
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW | DETACHED_PROCESS
    else:
        # macOS/Linux: fully detach from the parent's terminal/session (no window)
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def http_ok(code: str) -> bool:
    return code.strip() == "200"


def http_probe(url: str, timeout: int = 5) -> tuple[bool, str]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "MusicServerControl/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = str(response.status)
            return http_ok(code), code
    except urllib.error.HTTPError as exc:
        return False, str(exc.code)
    except Exception:
        return False, "000"


def process_running(image: str) -> bool:
    if IS_WINDOWS:
        result = run_hidden(["tasklist.exe", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"])
        return image.casefold() in result.stdout.casefold()
    result = run_hidden(["pgrep", "-x", image])
    return result.returncode == 0


TUNNEL_UNIT = "cloudflared-music-new.service"
BRIDGE_UNIT = "navidrome-host-forward.service"


def wsl_service_state(name: str) -> str:
    if IS_WINDOWS:
        result = run_hidden(["wsl.exe", "-d", "Ubuntu", "-u", "root", "--", "systemctl", "is-active", name])
        return result.stdout.strip() or "inactive"
    if IS_LINUX:
        result = run_hidden(["systemctl", "is-active", name])
        return result.stdout.strip() or "inactive"
    return "unsupported"


def wsl_action(action: str, service: str) -> None:
    # systemctl restart can remain blocked while cloudflared reconnects;
    # queue it and return so the GUI never stays on "Working…".
    if IS_WINDOWS:
        base = ["wsl.exe", "-d", "Ubuntu", "-u", "root", "--", "systemctl"]
    elif IS_LINUX:
        base = ["systemctl"]
    else:
        raise RuntimeError("Tunnel control requires systemd (Windows/WSL or native Linux).")
    action_args = ["restart", "--no-block", service] if action == "restart" else [action, service]
    result = run_hidden([*base, *action_args], timeout=20)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"systemctl {action} failed")


def task_enabled(task_name: str) -> bool:
    if not CAP_TASKS:
        return False
    result = run_hidden(["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST", "/V"])
    return result.returncode == 0 and "Scheduled Task State:" in result.stdout and "Enabled" in result.stdout


def set_task_enabled(task_name: str, enabled: bool) -> None:
    if not CAP_TASKS:
        raise RuntimeError("Scheduled-task control is Windows-only.")
    run_hidden(["schtasks.exe", "/Change", "/TN", task_name, "/ENABLE" if enabled else "/DISABLE"])


def run_task(task_name: str) -> None:
    if not CAP_TASKS:
        raise RuntimeError("Scheduled-task control is Windows-only.")
    run_hidden(["schtasks.exe", "/Run", "/TN", task_name])


def acquisition_running() -> bool:
    """True while an acquire/auto_acquire run holds a fresh lock."""
    if not ACQ_LOCK.exists():
        return False
    try:
        return (time.time() - ACQ_LOCK.stat().st_mtime) < ACQ_LOCK_MAX_AGE_S
    except OSError:
        return False


def next_sync_run() -> str:
    if IS_WINDOWS:
        result = run_hidden([
            "schtasks.exe", "/Query", "/TN", AUTOMATION_TASKS["hourly_sync"],
            "/FO", "LIST", "/V",
        ])
        for line in result.stdout.splitlines():
            if "Next Run Time:" in line:
                return line.split(":", 1)[1].strip() or "n/a"
        return "n/a"
    if IS_LINUX:
        result = run_hidden(["systemctl", "list-timers", "music-sync.timer", "--no-pager"], timeout=20)
        for line in result.stdout.splitlines():
            if "music-sync.timer" in line:
                return line.split()[1] if len(line.split()) > 1 else "scheduled"
        return "n/a"
    return "n/a (launchd)"


def last_run_summary() -> str:
    """Human summary of the newest sync/acquire artifact."""
    try:
        if ACQ_REPORT.exists() and SYNC_REPORT.exists():
            newest = max(ACQ_REPORT.stat().st_mtime, SYNC_REPORT.stat().st_mtime)
        elif ACQ_REPORT.exists():
            newest = ACQ_REPORT.stat().st_mtime
        elif SYNC_REPORT.exists():
            newest = SYNC_REPORT.stat().st_mtime
        else:
            return "no runs yet"
    except OSError:
        return "no runs yet"
    age = time.time() - newest
    if age < 90:
        return "just now"
    if age < 3600:
        return f"{int(age // 60)} min ago"
    if age < 86400:
        return f"{int(age // 3600)}h ago"
    return f"{int(age // 86400)}d ago"


def startup_enabled(name: str) -> bool:
    return STARTUP_ITEMS[name].exists()


def set_startup(name: str, enabled: bool) -> None:
    path = STARTUP_ITEMS[name]
    if enabled:
        if IS_WINDOWS:
            path.write_text(STARTUP_CONTENT[name], encoding="utf-8")
        elif IS_MAC:
            path.write_text(LAUNCHD_PLIST[name], encoding="utf-8")
        else:
            path.write_text(XDG_AUTOSTART[name], encoding="utf-8")
    elif path.exists():
        path.unlink()


def start_navidrome() -> None:
    if not process_running(NAVIDROME_PROCESS):
        spawn_hidden([str(NAVIDROME_BIN), "--configfile", str(BASE / "navidrome.toml")])


def stop_navidrome() -> None:
    if IS_WINDOWS:
        run_hidden(["taskkill.exe", "/IM", NAVIDROME_PROCESS, "/F"])
    else:
        run_hidden(["pkill", "-x", NAVIDROME_PROCESS])


LIBRARY_ROOT = Path(os.environ.get("MUSIC_LIBRARY", Path.home() / "Music" / "Spotify Liked"))
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".alac"}


def library_stats() -> dict:
    files = lrc = 0
    size = 0
    for path in LIBRARY_ROOT.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in AUDIO_EXT:
            files += 1
            try:
                size += path.stat().st_size
            except OSError:
                pass
        elif suffix == ".lrc":
            lrc += 1
    return {"files": files, "lrc": lrc, "gib": round(size / 1024**3, 2)}


def status_snapshot() -> dict:
    local_ok, local_code = http_probe("http://127.0.0.1:4533/ping")
    public_ok, public_code = http_probe(f"{PUBLIC_URL}/ping", timeout=12)
    report = {}
    if SYNC_REPORT.exists():
        try:
            report = json.loads(SYNC_REPORT.read_text(encoding="utf-8"))
        except Exception:
            report = {}
    acq_report = {}
    if ACQ_REPORT.exists():
        try:
            acq_report = json.loads(ACQ_REPORT.read_text(encoding="utf-8"))
        except Exception:
            acq_report = {}
    return {
        "navidrome": {
            "running": process_running("navidrome.exe"),
            "local": local_ok,
            "local_code": local_code,
            "autostart": startup_enabled("navidrome"),
        },
        "tunnel": {
            "service": wsl_service_state("cloudflared-music-new.service"),
            "bridge": wsl_service_state("navidrome-host-forward.service"),
            "public": public_ok,
            "public_code": public_code,
            "autostart": startup_enabled("cloudflared"),
        },
        "spotify": {
            "saved": report.get("spotify_saved", "—"),
            "matched": report.get("matched", "—"),
            "missing": report.get("missing", "—"),
            "last_sync": report.get("finished_at", "Never"),
        },
        "acquisition": {
            "running": acquisition_running(),
            "next_sync": next_sync_run(),
            "last_run": last_run_summary(),
            "missing_at_end": acq_report.get("missing_at_end"),
            "library": library_stats(),
        },
        "automation": {key: task_enabled(value) for key, value in AUTOMATION_TASKS.items()},
        "capabilities": {
            "tasks": CAP_TASKS,
            "tunnel": CAP_TUNNEL,
            "platform": _platform.system(),
        },
    }


def start_acquisition() -> None:
    if acquisition_running():
        return
    if not SYNC_VENV_PYTHON.exists():
        raise RuntimeError(f"missing {SYNC_VENV_PYTHON}")
    spawn_hidden([
        str(SYNC_VENV_PYTHON), "-m", "ultimate_music_sync.auto_acquire",
    ], cwd=str(SYNC))


class ControlCenter(tk.Tk):
    TWO_COL_MIN_WIDTH = 1080  # window width (px) at which cards sit side by side

    def __init__(self) -> None:
        super().__init__()
        self.title("Spotify Music Server Control")
        self.minsize(340, 380)
        self.configure(bg="#10131a")
        self.status_vars: dict[str, tk.StringVar] = {}
        self.status_colors: dict[str, tk.Label] = {}
        self.task_vars: dict[str, tk.BooleanVar] = {}
        self.startup_vars: dict[str, tk.BooleanVar] = {}
        self._cards: dict[str, ttk.Frame] = {}
        self._flows: list[ttk.Frame] = []
        self._build_style()
        self._build_ui()
        self._apply_initial_geometry()
        self.bind("<Configure>", self._on_configure)
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Button-4>", self._on_mousewheel)  # linux scroll-up
        self.bind("<Button-5>", self._on_mousewheel)  # linux scroll-down
        self.after(250, self.refresh)
        self.after(15000, self._periodic_refresh)

    def _apply_initial_geometry(self) -> None:
        """Fit the window to the work area; clamp to it on small screens."""
        self.update_idletasks()
        scale = max(1.0, self.winfo_fpixels("1i") / 96.0)
        width = min(880, int(self.winfo_screenwidth() - 40))
        height = min(720, int(self.winfo_screenheight() - 120))
        self.geometry(f"{int(width * scale)}x{int(height * scale)}")

    def _on_configure(self, event) -> None:
        if event.widget is not self:
            return
        if event.width == getattr(self, "_last_width", None):
            return  # height-only or no-op resize; nothing to re-wrap
        self._last_width = event.width
        if getattr(self, "_reflow_job", None):
            self.after_cancel(self._reflow_job)
        self._reflow_job = self.after(120, self._apply_relayout)

    def _apply_relayout(self) -> None:
        """Run the (heavier) relayout once, after resizing settles."""
        self._reflow_job = None
        self._reflow_columns()
        self._reflow_flows()
        if hasattr(self, "_inner_window"):
            self._canvas.itemconfigure(self._inner_window, width=self.winfo_width() - 24)
        self._sync_scrollregion()

    def _reflow_columns(self) -> None:
        """Reflow the card grid between two-column and single-column layout."""
        wide = self.winfo_width() >= self.TWO_COL_MIN_WIDTH
        if getattr(self, "_wide_state", None) == wide:
            return
        self._wide_state = wide
        slots = [
            ("nav", 0, 0), ("tunnel", 0, 1),
            ("spotify", 1, 0), ("acquire", 1, 1),
        ]
        for key, row, col in slots:
            card = self._cards.get(key)
            if card is None:
                continue
            if wide:
                card.grid_configure(row=row, column=col, columnspan=1)
            else:
                card.grid_configure(row=row, column=0, columnspan=1)
        for key, row, wide_kwargs, narrow_row in (
            ("auto", 2, {"row": 2, "column": 0, "columnspan": 2}, 2),
            ("tools", 3, {"row": 3, "column": 0, "columnspan": 2}, 3),
        ):
            card = self._cards.get(key)
            if card is None:
                continue
            if wide:
                card.grid_configure(**wide_kwargs)
            else:
                card.grid_configure(row=narrow_row, column=0, columnspan=1)
        self._sync_scrollregion()

    def _flow_row(self, parent, pady=(10, 0)) -> ttk.Frame:
        """Button row that re-wraps into multiple rows when the window is narrow."""
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill="x", pady=pady)
        self._flows.append(frame)
        return frame

    def _reflow_flows(self) -> None:
        """Greedy-wrap each flow row's buttons; retry narrower until it fits."""
        window = self.winfo_width()
        for attempt_avail in (window - 110, window // 2 - 80, 200):
            self._flow_pass(max(180, attempt_avail))
            if self._inner.winfo_reqwidth() <= max(240, window - 24):
                break

    def _flow_pass(self, avail: int) -> None:
        for frame in getattr(self, "_flows", []):
            children = list(frame.winfo_children())
            if not children:
                continue
            for child in children:
                child.grid_forget()
            row = col = x = 0
            for child in children:
                width = child.winfo_reqwidth()
                if x > 0 and x + width > avail:
                    row += 1
                    col = 0
                    x = 0
                child.grid(row=row, column=col, sticky="w", padx=(0, 6), pady=(4, 0))
                col += 1
                x += width + 6
        self._sync_scrollregion()

    def _sync_scrollregion(self) -> None:
        if hasattr(self, "_canvas") and self._canvas.winfo_exists():
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_mousewheel(self, event=None) -> None:
        delta = 0
        if event is not None and getattr(event, "num", None) not in (4, 5):
            delta = -int(event.delta / 120 * 3) if event.delta else 0
        elif event is not None:
            delta = -3 if event.num == 4 else 3
        if delta:
            self._canvas.yview_scroll(delta, "units")

    def _periodic_refresh(self) -> None:
        if self.winfo_exists():
            self.refresh()
            self.after(15000, self._periodic_refresh)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#10131a")
        style.configure("Card.TFrame", background="#171c26")
        style.configure("TLabel", background="#10131a", foreground="#eef2ff", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22), foreground="#ffffff")
        style.configure("Sub.TLabel", foreground="#9ca8bd")
        style.configure("Card.TLabel", background="#171c26", foreground="#eef2ff")
        style.configure("Heading.TLabel", background="#171c26", font=("Segoe UI Semibold", 13), foreground="#ffffff")
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(12, 8), foreground="#111827", background="#e5e7eb")
        style.map("TButton", foreground=[("disabled", "#6b7280"), ("pressed", "#111827"), ("active", "#111827")], background=[("disabled", "#d1d5db"), ("pressed", "#cbd5e1"), ("active", "#f3f4f6")])
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(12, 8), foreground="#ffffff", background="#2563eb")
        style.map("Accent.TButton", foreground=[("disabled", "#dbeafe"), ("pressed", "#ffffff"), ("active", "#ffffff")], background=[("disabled", "#64748b"), ("pressed", "#1d4ed8"), ("active", "#3b82f6")])
        style.configure("TCheckbutton", background="#171c26", foreground="#eef2ff", font=("Segoe UI", 10))

    def _button(self, parent, text: str, command, accent: bool = False) -> tk.Button:
        """Use a native Tk button with explicit colors so labels cannot disappear."""
        if accent:
            colors = {
                "bg": "#2563eb", "fg": "#ffffff",
                "activebackground": "#3b82f6", "activeforeground": "#ffffff",
            }
        else:
            colors = {
                "bg": "#e5e7eb", "fg": "#111827",
                "activebackground": "#f3f4f6", "activeforeground": "#111827",
            }
        return tk.Button(
            parent, text=text, command=command,
            font=("Segoe UI", 10, "bold"),
            padx=12, pady=7, relief="raised", borderwidth=1,
            highlightthickness=0, anchor="center", **colors,
        )

    def _card(self, parent, key: str, title: str, row: int, column: int, columnspan: int = 1) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=16)
        frame.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=7, pady=7)
        ttk.Label(frame, text=title, style="Heading.TLabel").pack(anchor="w", pady=(0, 10))
        if columnspan == 1:
            self._cards[key] = frame
        return frame

    def _status_line(self, frame, key: str, label: str) -> None:
        line = ttk.Frame(frame, style="Card.TFrame")
        line.pack(fill="x", pady=3)
        ttk.Label(line, text=label, style="Card.TLabel").pack(side="left")
        var = tk.StringVar(value="Checking…")
        pill = tk.Label(line, textvariable=var, font=("Segoe UI", 10, "bold"),
                        bg="#171c26", fg="#eef2ff", padx=10, pady=2)
        pill.pack(side="right")
        self.status_vars[key] = var
        self.status_colors[key] = pill

    def set_status_color(self, key: str, kind: str) -> None:
        """kind: ok | warn | bad | neutral"""
        palette = {
            "ok": ("#123c22", "#4ade80"),
            "warn": ("#3f2f0a", "#fbbf24"),
            "bad": ("#451717", "#f87171"),
            "neutral": ("#171c26", "#eef2ff"),
            "busy": ("#0f2a46", "#60a5fa"),
        }
        bg, fg = palette.get(kind, palette["neutral"])
        pill = self.status_colors.get(key)
        if pill is not None:
            pill.configure(bg=bg, fg=fg)

    def _build_ui(self) -> None:
        # Scrollable shell: canvas + vertical scrollbar + inner content frame.
        shell = ttk.Frame(self, style="TFrame")
        shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, bg="#10131a", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._canvas = canvas
        inner = ttk.Frame(canvas, style="TFrame", padding=18)
        self._inner = inner
        self._inner_window = canvas.create_window((0, 0), window=inner, anchor="nw", tags="inner")
        inner.columnconfigure(0, weight=1)
        # Header owns its rows; cards never reflow into this space.
        header = ttk.Frame(inner, style="TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Spotify Music Server", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Navidrome · Spotify sync · Cloudflare", style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 12))
        tk.Button(header, text="Refresh", command=self.refresh, font=("Segoe UI", 10, "bold"), padx=12, pady=7, bg="#e5e7eb", fg="#111827", activebackground="#f3f4f6", activeforeground="#111827", relief="raised", borderwidth=1, highlightthickness=0).grid(row=0, column=1, rowspan=2, sticky="e")
        cards_area = ttk.Frame(inner, style="TFrame")
        cards_area.grid(row=1, column=0, sticky="nsew")
        cards_area.columnconfigure(0, weight=1)
        cards_area.columnconfigure(1, weight=1)

        nav = self._card(cards_area, "nav", "Navidrome", 0, 0)
        self._status_line(nav, "nav", "Server")
        self._status_line(nav, "local", "Local endpoint")
        actions = self._flow_row(nav)
        self._button(actions, "Start", lambda: self.action(start_navidrome))
        self._button(actions, "Stop", lambda: self.action(stop_navidrome))
        self._button(actions, "Open Local", lambda: webbrowser.open("http://127.0.0.1:4533/app/"))

        tunnel = self._card(cards_area, "tunnel", "Remote Access", 0, 1)
        self._status_line(tunnel, "tunnel", "Tunnel service")
        self._status_line(tunnel, "public", "Public endpoint")
        actions = self._flow_row(tunnel)
        self._button(actions, "Restart", lambda: self.action(wsl_action, "restart", TUNNEL_UNIT))
        self._button(actions, "Open Remote", lambda: webbrowser.open(f"{PUBLIC_URL}/app/"))

        spotify = self._card(cards_area, "spotify", "Spotify Library", 1, 0)
        self._status_line(spotify, "saved", "Liked songs")
        self._status_line(spotify, "matched", "Available offline")
        self._status_line(spotify, "missing", "Missing files")
        self._status_line(spotify, "last_sync", "Last sync")
        actions = self._flow_row(spotify)
        self._button(actions, "Sync Now", lambda: self.action(run_task, AUTOMATION_TASKS["hourly_sync"], watch=True), accent=True)
        self._button(actions, "Open Missing List", lambda: os.startfile(SYNC / ".real-state" / "spotify-missing-tracks.csv"))

        acquire = self._card(cards_area, "acquire", "Acquisition", 1, 1)
        self._status_line(acquire, "acq_state", "Status")
        self._status_line(acquire, "acq_library", "Library")
        self._status_line(acquire, "acq_lrc", "Lyrics (.lrc)")
        self._status_line(acquire, "acq_next", "Next sync")
        self._status_line(acquire, "acq_last", "Last run")
        actions = self._flow_row(acquire)
        self._button(actions, "Acquire Now", lambda: self.action(start_acquisition, watch=True), accent=True)
        self._button(actions, "Open Sync Log", lambda: os.startfile(SYNC_LOG))
        self._button(actions, "Open Acquire Log", lambda: os.startfile(ACQ_LOG))

        auto = self._card(cards_area, "auto", "Autostart & Automation", 2, 0, 2)
        grid = ttk.Frame(auto, style="Card.TFrame"); grid.pack(fill="x")
        self._flows.append(grid)
        for index, (key, label) in enumerate([("navidrome", "Navidrome at login"), ("cloudflared", "Tunnel at login")]):
            var = tk.BooleanVar()
            self.startup_vars[key] = var
            ttk.Checkbutton(grid, text=label, variable=var, command=lambda k=key, v=var: self.action(set_startup, k, v.get())).grid(row=index // 2, column=index % 2, sticky="w", pady=3)
        labels = {
            "hourly_sync": "Hourly Spotify sync",
            "daily_ai_dj": "Daily AI DJ playlist",
            "public_health": "Tunnel health monitor",
            "navidrome_watchdog": "Navidrome health monitor",
        }
        offset = 2
        for index, (key, label) in enumerate(labels.items()):
            var = tk.BooleanVar()
            self.task_vars[key] = var
            ttk.Checkbutton(grid, text=label, variable=var, command=lambda k=key, v=var: self.action(set_task_enabled, AUTOMATION_TASKS[k], v.get())).grid(row=(offset + index) // 2, column=(offset + index) % 2, sticky="w", pady=3)

        files = self._card(cards_area, "tools", "Tools", 3, 0, 2)
        tools = self._flow_row(files, pady=(0, 0))
        for text, command in [
            ("Music Folder", lambda: os.startfile(LIBRARY_ROOT)),
            ("Sync Reports", lambda: os.startfile(SYNC / ".real-state")),
            ("Server Folder", lambda: os.startfile(BASE)),
            ("Run AI DJ", lambda: self.action(run_task, AUTOMATION_TASKS["daily_ai_dj"])),
        ]:
            self._button(tools, text, command)

        self.message = tk.StringVar(value="Ready")
        ttk.Label(inner, textvariable=self.message, style="Sub.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self._wide_state = None
        self._apply_relayout()

    def action(self, func, *args, watch: bool = False) -> None:
        self.message.set("Working…")
        def worker():
            try:
                func(*args)
                message = "Done"
            except subprocess.TimeoutExpired:
                message = "Timed out; check status and logs"
            except Exception as exc:
                message = f"Error: {exc}"
            self.after(0, lambda: (self.message.set(message), self.refresh()))
            if watch:
                self._schedule_watchdog()
        threading.Thread(target=worker, daemon=True).start()

    def _schedule_watchdog(self, checks: int = 20, interval_ms: int = 15000) -> None:
        """Autorefresh while a long-running action works (lock file flips to Idle)."""
        def poll(remaining: int = checks):
            if not self.winfo_exists():
                return
            self.refresh()
            if remaining > 0 and acquisition_running():
                self.after(interval_ms, lambda: poll(remaining - 1))
        self.after(interval_ms, poll)

    def refresh(self) -> None:
        self.message.set("Refreshing…")
        def worker():
            try:
                s = status_snapshot()
                def apply():
                    nav = s["navidrome"]; tun = s["tunnel"]; spot = s["spotify"]; acq = s["acquisition"]
                    self.status_vars["nav"].set("Running" if nav["running"] else "Stopped")
                    self.set_status_color("nav", "ok" if nav["running"] else "bad")
                    self.status_vars["local"].set(f"{'Online' if nav['local'] else 'Offline'} · {nav['local_code']}")
                    self.set_status_color("local", "ok" if nav["local"] else "bad")
                    service = tun["service"]
                    self.status_vars["tunnel"].set(f"{service.capitalize()} · bridge {tun['bridge']}")
                    self.set_status_color("tunnel", "ok" if service == "active" else "warn" if service == "activating" else "bad")
                    self.status_vars["public"].set(f"{'Online' if tun['public'] else 'Offline'} · {tun['public_code']}")
                    self.set_status_color("public", "ok" if tun["public"] else "bad")
                    try:
                        saved, matched, missing = int(spot["saved"]), int(spot["matched"]), int(spot["missing"])
                        coverage = matched / saved if saved else 0
                        self.status_vars["saved"].set(str(saved))
                        self.set_status_color("saved", "neutral")
                        self.status_vars["matched"].set(f"{matched} · {coverage:.0%}")
                        self.set_status_color("matched", "ok" if coverage >= 0.95 else "warn" if coverage >= 0.85 else "bad")
                        self.status_vars["missing"].set(str(missing))
                        self.set_status_color("missing", "ok" if missing <= 30 else "warn" if missing <= 80 else "bad")
                    except (TypeError, ValueError):
                        for key in ("saved", "matched", "missing"):
                            self.status_vars[key].set(str(spot[key]))
                            self.set_status_color(key, "neutral")
                    stamp = str(spot["last_sync"]).replace("T", " ")[:19]
                    self.status_vars["last_sync"].set(stamp if stamp != "Never" else "Never")
                    self.set_status_color("last_sync", "neutral")
                    if acq["running"]:
                        self.status_vars["acq_state"].set("Acquiring…")
                        self.set_status_color("acq_state", "busy")
                    else:
                        left = acq.get("missing_at_end")
                        self.status_vars["acq_state"].set("Idle" if left is not None else "No data yet")
                        self.set_status_color("acq_state", "neutral" if left is not None else "warn")
                    lib = acq.get("library") or {}
                    self.status_vars["acq_library"].set(f"{lib.get('files', '—')} files · {lib.get('gib', '—')} GiB")
                    self.set_status_color("acq_library", "neutral")
                    files = lib.get("files") or 0
                    lrc = lib.get("lrc") or 0
                    self.status_vars["acq_lrc"].set(f"{lrc} · {lrc / files:.0%}" if files else "—")
                    self.set_status_color("acq_lrc", "ok" if files and lrc / files >= 0.6 else "neutral")
                    self.status_vars["acq_next"].set(str(acq.get("next_sync", "n/a")))
                    self.set_status_color("acq_next", "neutral")
                    self.status_vars["acq_last"].set(str(acq.get("last_run", "n/a")))
                    self.set_status_color("acq_last", "neutral")
                    for key, var in self.startup_vars.items(): var.set(s["navidrome" if key == "navidrome" else "tunnel"]["autostart"])
                    for key, var in self.task_vars.items(): var.set(s["automation"][key])
                    self.message.set("Ready")
                self.after(0, apply)
            except Exception as exc:
                self.after(0, lambda: self.message.set(f"Refresh failed: {exc}"))
        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text under display scaling
        except Exception:
            pass
    ControlCenter().mainloop()
