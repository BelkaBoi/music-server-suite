"""Capture control-panel screenshots for the README.

Boots the real panel against a mocked status layer (so screenshots are
deterministic and show a healthy, good-looking state), grabs each pill and the
full window via PIL ImageGrab, and writes PNGs to docs/screenshots/.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-center"))

import server_control as sc  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

MOCK = {
    "navidrome": {"running": True, "local": True, "local_code": "200", "autostart": True},
    "tunnel": {"service": "active", "bridge": "active", "public": True,
               "public_code": "200", "autostart": True},
    "spotify": {"saved": 1075, "matched": 1049, "missing": 26,
                "last_sync": "2026-09-19T18:00:00+00:00"},
    "acquisition": {
        "running": False, "next_sync": "in 42 minutes", "last_run": "18 minutes ago",
        "missing_at_end": 26,
        "library": {"files": 1064, "lrc": 755, "gib": 6.88},
    },
    "automation": {"hourly_sync": True, "daily_ai_dj": True,
                   "public_health": True, "navidrome_watchdog": True},
    "capabilities": {"tasks": True, "tunnel": True, "platform": "Windows"},
}


def main() -> None:
    # Patch the network/system probes so the shot is deterministic and healthy.
    sc.status_snapshot = lambda: json.loads(json.dumps(MOCK))
    sc.http_probe = lambda url, timeout=5: (True, "200")

    app = sc.ControlCenter()
    # force two-column layout and grow the window to fit all cards (no scrolling)
    app.geometry("1400x9999")
    app.update_idletasks()
    content_h = app._canvas.bbox("all")[3] + 80   # + padding below Tools card
    pass  # position set in the combined geometry call below
    app.geometry(f"1400x{min(int(content_h) + 60, app.winfo_screenheight() - 40)}+0+0")  # size + position, no bleed
    app.update_idletasks()
    # scroll to top so the shot starts at the header
    app._canvas.yview_moveto(0)

    def shoot_full():
        app.update_idletasks()
        app.after(600, lambda: (grab(app, OUT / "control-panel.png"), app.destroy()))

    def dump_state():
        state = {k: v.get() for k, v in app.status_vars.items()}
        (OUT / "panel-state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
        print("state:", json.dumps(state)[:400])

    app.after(2400, dump_state)
    app.after(2500, shoot_full)
    app.mainloop()
    print("saved:", list(OUT.glob("*.png")))


def grab(app, path):
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(app.winfo_id())
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        app.attributes("-topmost", True)
        app.update()
        import tkinter as tk
        # content rect: client area starts below the title bar; crop it off
        x = app.winfo_rootx()
        y = app.winfo_rooty()
        w = app.winfo_width()
        h = app.winfo_height()
        from PIL import ImageGrab
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        app.attributes("-topmost", False)
        image.save(path)
        print("saved", path.name, f"{w}x{h}")
    except Exception as exc:
        print("grab failed:", exc)


if __name__ == "__main__":
    main()
