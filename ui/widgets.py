from __future__ import annotations

import tkinter as tk

from .theme import (
    ACCENT,
    ACCENT_DARK,
    ACCENT_HOVER,
    BG,
    BORDER,
    DANGER_BG,
    DANGER_BG_ACTIVE,
    DANGER_FG,
    DANGER_FG_ACTIVE,
    DARK_FG,
    FG,
    FONT,
    FONT_BOLD,
    INPUT,
    MUTED,
    PANEL,
    PANEL2,
    PANEL3,
    SELECT_FG,
)


def flat_button(parent, text, command=None, accent=False, danger=False,
                bg=None, fg=None, font=FONT, padx=14, pady=6, width=None,
                anchor="center"):
    if accent:
        bg = ACCENT
        fg = DARK_FG
        active_bg = ACCENT_HOVER
        active_fg = DARK_FG
        font = FONT_BOLD
    elif danger:
        bg = bg or DANGER_BG
        fg = fg or DANGER_FG
        active_bg = DANGER_BG_ACTIVE
        active_fg = DANGER_FG_ACTIVE
    else:
        bg = bg or PANEL2
        fg = fg or FG
        active_bg = PANEL3
        active_fg = FG
    button = tk.Button(
        parent,
        text=text,
        command=command,
        bd=0,
        relief="flat",
        highlightthickness=0,
        bg=bg,
        fg=fg,
        activebackground=active_bg,
        activeforeground=active_fg,
        disabledforeground=MUTED,
        font=font,
        padx=padx,
        pady=pady,
        cursor="hand2",
        anchor=anchor,
    )
    if width:
        button.configure(width=width)
    return button


def link_button(parent, text, command, bg=PANEL):
    return tk.Button(parent, text=text, command=command, bd=0, relief="flat",
                     highlightthickness=0, bg=bg, fg=ACCENT,
                     activebackground=bg, activeforeground=ACCENT_HOVER,
                     font=FONT, cursor="hand2", anchor="w", padx=0, pady=0)


def center_window(win: tk.Toplevel, parent, width: int, height: int):
    win.update_idletasks()
    try:
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
    except Exception:
        px = py = 0
        pw, ph = width, height
    x = max(0, px + (pw - width) // 2)
    y = max(0, py + (ph - height) // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")


def card(parent, padding=14):
    import tkinter.ttk as ttk
    return ttk.Frame(parent, style="Card.TFrame", padding=padding)


def label(parent, text, style="Card.TLabel", **kwargs):
    import tkinter.ttk as ttk
    return ttk.Label(parent, text=text, style=style, **kwargs)


def text_area(parent, height=8, font=None):
    frame = tk.Frame(parent, bg=INPUT, highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=BORDER)
    text = tk.Text(frame, height=height, wrap="word", bd=0,
                   bg=INPUT, fg=FG, insertbackground=ACCENT,
                   selectbackground=ACCENT_DARK, selectforeground=SELECT_FG,
                   font=font or ("Courier New", 9), padx=8, pady=6,
                   state="disabled")
    scroll = tk.Scrollbar(frame, command=text.yview, bd=0, relief="flat",
                          bg=PANEL2, troughcolor=PANEL, activebackground=BORDER,
                          width=10)
    text.configure(yscrollcommand=scroll.set)
    text.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    return frame, text


def append_text(text_widget: tk.Text, message: str):
    text_widget.configure(state="normal")
    text_widget.insert("end", message.rstrip("\n") + "\n")
    text_widget.see("end")
    text_widget.configure(state="disabled")


def clear_text(text_widget: tk.Text):
    text_widget.configure(state="normal")
    text_widget.delete("1.0", "end")
    text_widget.configure(state="disabled")


def busy_text(button, text: str):
    try:
        button.configure(text=text, state="disabled")
    except Exception:
        pass


def idle_text(button, text: str):
    try:
        button.configure(text=text, state="normal")
    except Exception:
        pass
