from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PYMCL_DATA_DIR") or CODE_DIR)


def _setup_logging():
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_dir / "launcher.log"),
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> int:
    _setup_logging()
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        from ui.app import LauncherApp
        app = LauncherApp()
    except Exception as e:
        logging.exception("failed to start launcher")
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("PyMC Launcher", f"启动失败：\n{e}")
            root.destroy()
        except Exception:
            pass
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
