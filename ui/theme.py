from __future__ import annotations

from tkinter import ttk

FONT_FAMILY = "Courier New"
FONT = (FONT_FAMILY, 10)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_BOLD = (FONT_FAMILY, 10, "bold")
FONT_BIG = (FONT_FAMILY, 12, "bold")
FONT_TITLE = (FONT_FAMILY, 18, "bold")
FONT_HUGE = (FONT_FAMILY, 26, "bold")

BG = "#eef2f7"
PANEL = "#ffffff"
PANEL2 = "#e3e9f1"
PANEL3 = "#d4dde8"
BORDER = "#c2ccda"
FG = "#1f2937"
MUTED = "#5c6b7f"
ACCENT = "#0f766e"
ACCENT_HOVER = "#0d9488"
ACCENT_DARK = "#115e59"
WARN = "#b45309"
ERR = "#c53030"
BLUE = "#1d4ed8"
DARK_FG = "#ffffff"
INPUT = "#ffffff"
SELECT_FG = "#ffffff"
ACCENT_DISABLED_BG = "#d9ece9"
ACCENT_DISABLED_FG = "#4b7f78"
DANGER_BG = "#fdecec"
DANGER_BG_ACTIVE = "#f9d6d6"
DANGER_FG = "#c53030"
DANGER_FG_ACTIVE = "#9b1c1c"


def apply_theme(root):
    root.configure(bg=BG)
    try:
        root.option_add("*tearOff", False)
    except Exception:
        pass
    root.option_add("*Font", FONT)
    root.option_add("*Background", BG)
    root.option_add("*Foreground", FG)
    root.option_add("*Listbox.background", PANEL2)
    root.option_add("*Listbox.foreground", FG)
    root.option_add("*Listbox.selectBackground", ACCENT_DARK)
    root.option_add("*Listbox.selectForeground", SELECT_FG)
    root.option_add("*Listbox.font", FONT_SMALL)
    root.option_add("*Text.background", INPUT)
    root.option_add("*Text.foreground", FG)
    root.option_add("*Text.insertBackground", ACCENT)
    root.option_add("*Menu.background", PANEL2)
    root.option_add("*Menu.foreground", FG)
    root.option_add("*Menu.activeBackground", ACCENT_DARK)
    root.option_add("*Menu.activeForeground", SELECT_FG)
    root.option_add("*TCombobox*Listbox.background", PANEL2)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DARK)
    root.option_add("*TCombobox*Listbox.selectForeground", SELECT_FG)
    root.option_add("*TCombobox*Listbox.font", FONT)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", background=BG, foreground=FG, font=FONT, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("Bar.TFrame", background=PANEL2)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("Card.TLabel", background=PANEL, foreground=FG, font=FONT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SMALL)
    style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED, font=FONT_SMALL)
    style.configure("CardTitle.TLabel", background=PANEL, foreground=FG, font=FONT_BOLD)
    style.configure("Accent.TLabel", background=BG, foreground=ACCENT, font=FONT_BOLD)
    style.configure("CardAccent.TLabel", background=PANEL, foreground=ACCENT, font=FONT)
    style.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=FONT_SMALL)
    style.configure("Warn.TLabel", background=PANEL, foreground=WARN, font=FONT_SMALL)

    style.configure("TButton", background=PANEL2, foreground=FG, borderwidth=0,
                    focusthickness=0, padding=(12, 6), font=FONT)
    style.map("TButton",
              background=[("active", PANEL3), ("pressed", BORDER), ("disabled", PANEL)],
              foreground=[("disabled", MUTED), ("active", FG)])
    style.configure("Accent.TButton", background=ACCENT, foreground=DARK_FG, font=FONT_BOLD)
    style.map("Accent.TButton",
              background=[("active", ACCENT_HOVER), ("pressed", ACCENT_DARK), ("disabled", ACCENT_DISABLED_BG)],
              foreground=[("disabled", ACCENT_DISABLED_FG)])

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(16, 10, 16, 0))
    style.configure("TNotebook.Tab", background=PANEL2, foreground=MUTED, padding=(22, 8),
                    borderwidth=0, font=FONT)
    style.map("TNotebook.Tab",
              background=[("selected", PANEL), ("active", PANEL3)],
              foreground=[("selected", ACCENT), ("active", FG)],
              font=[("selected", FONT_BOLD)])
    style.configure("TNotebook.Client", background=BG, borderwidth=0)

    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                    rowheight=26, borderwidth=0, font=FONT)
    style.map("Treeview",
              background=[("selected", ACCENT_DARK)],
              foreground=[("selected", SELECT_FG)])
    style.configure("Treeview.Heading", background=PANEL2, foreground=MUTED,
                    relief="flat", font=FONT_BOLD, padding=(8, 6), borderwidth=0)
    style.map("Treeview.Heading", background=[("active", PANEL3)], foreground=[("active", FG)])

    style.configure("TScrollbar", background=PANEL2, troughcolor=BG, bordercolor=BG,
                    arrowcolor=MUTED, borderwidth=0, width=11)
    style.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, borderwidth=0, width=11)
    style.map("Vertical.TScrollbar", background=[("active", BORDER)])
    style.configure("Horizontal.TScrollbar", background=PANEL2, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, borderwidth=0)

    style.configure("TEntry", fieldbackground=PANEL2, foreground=FG, insertcolor=FG,
                    borderwidth=0, padding=6)
    style.map("TEntry", fieldbackground=[("disabled", PANEL)], foreground=[("disabled", MUTED)])
    style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=FG,
                    arrowcolor=MUTED, borderwidth=0, padding=5)
    style.map("TCombobox",
              fieldbackground=[("readonly", PANEL2), ("disabled", PANEL)],
              foreground=[("readonly", FG), ("disabled", MUTED)],
              background=[("readonly", PANEL2)],
              selectbackground=[("readonly", PANEL2)],
              selectforeground=[("readonly", FG)],
              arrowcolor=[("disabled", BORDER)])
    style.configure("TSpinbox", fieldbackground=PANEL2, foreground=FG, arrowcolor=MUTED,
                    borderwidth=0, padding=4, insertcolor=FG)
    style.map("TSpinbox", fieldbackground=[("disabled", PANEL)], foreground=[("disabled", MUTED)],
              arrowcolor=[("disabled", BORDER)])

    style.configure("TCheckbutton", background=BG, foreground=FG, font=FONT,
                    focusthickness=0, indicatorcolor=PANEL2, indicatorrelief="flat")
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", FG)])
    style.configure("Card.TCheckbutton", background=PANEL, foreground=FG, font=FONT,
                    focusthickness=0, indicatorcolor=PANEL2, indicatorrelief="flat")
    style.map("Card.TCheckbutton", background=[("active", PANEL)], foreground=[("active", FG)])

    style.configure("TProgressbar", troughcolor=PANEL2, background=ACCENT,
                    borderwidth=0, thickness=8)
    style.configure("TLabelframe", background=BG, bordercolor=BORDER, borderwidth=1,
                    relief="solid")
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED, font=FONT_BOLD)
    style.configure("Card.TLabelframe", background=PANEL, bordercolor=BORDER,
                    borderwidth=1, relief="solid")
    style.configure("Card.TLabelframe.Label", background=PANEL, foreground=MUTED, font=FONT_BOLD)
    style.configure("TSeparator", background=BORDER)
    style.configure("TRadiobutton", background=PANEL, foreground=FG, font=FONT,
                    indicatorcolor=PANEL2, indicatorrelief="flat")
    style.map("TRadiobutton", background=[("active", PANEL)])
