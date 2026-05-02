"""
gui_app.py - Main Application Window
Professional Tkinter GUI for the Order Management & CorelDRAW Design System.
"""

import os
import sys
import time
import queue
import logging
import threading
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3

# Local modules
import database as db
from coreldraw_bridge import get_bridge, COM_AVAILABLE

log = logging.getLogger("App")

# Check CorelDRAW automation availability
if not COM_AVAILABLE:
    log.warning("=" * 60)
    log.warning("pywin32 NOT INSTALLED - CorelDRAW automation disabled!")
    log.warning("Export will fail. Install with: pip install pywin32")
    log.warning("=" * 60)

# ── Theme constants ───────────────────────────────────────────────────────────
COLORS = {
    "bg":          "#1A1D2E",
    "surface":     "#252840",
    "surface2":    "#2E3155",
    "accent":      "#6C8EFF",
    "accent2":     "#FF6B8A",
    "accent3":     "#50E3A4",
    "text":        "#E8EAF6",
    "text_muted":  "#8890B5",
    "border":      "#3D4170",
    "success":     "#4CAF82",
    "warning":     "#F5A623",
    "danger":      "#FF5252",
    "pending":     "#F5A623",
    "exported":    "#4CAF82",
    "cancelled":   "#FF5252",
}

FONTS = {
    "header":  ("Segoe UI", 16, "bold"),
    "subhead": ("Segoe UI", 12, "bold"),
    "body":    ("Segoe UI", 10),
    "small":   ("Segoe UI", 9),
    "mono":    ("Consolas",  9),
    "title":   ("Segoe UI", 22, "bold"),
}

STATUS_OPTIONS   = ["pending", "in_progress", "exported", "cancelled"]
ORDER_TYPES      = ["Nameplate", "Label", "Badge", "Sign", "Sticker",
                    "Engraving", "Banner", "Custom"]
EXPORT_FORMATS   = ["cdr", "pdf", "dxf", "svg", "png"]


# ── Utility helpers ────────��──────────────────────────────────────────────────

def badge_color(status: str) -> str:
    return COLORS.get(status, COLORS["text_muted"])


def styled_entry(parent, textvariable=None, width=20, **kw):
    e = tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=COLORS["surface2"],
        fg=COLORS["text"],
        insertbackground=COLORS["accent"],
        relief="flat",
        font=FONTS["body"],
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
        **kw
    )
    return e


def styled_label(parent, text="", font=None, color=None, **kw):
    return tk.Label(
        parent,
        text=text,
        bg=COLORS["bg"],
        fg=color or COLORS["text"],
        font=font or FONTS["body"],
        **kw
    )


def styled_button(parent, text="", command=None, accent=False,
                  danger=False, small=False, **kw):
    bg = (COLORS["danger"] if danger else
          COLORS["accent"] if accent else COLORS["surface2"])
    fg = COLORS["text"]
    font = FONTS["small"] if small else FONTS["body"]
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        font=font,
        relief="flat",
        cursor="hand2",
        padx=10, pady=5,
        activebackground=COLORS["accent2"],
        activeforeground=COLORS["text"],
        **kw
    )
    return btn


# ── Scrollable frame ──────────────────────────────────────────────────────────

class ScrollFrame(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=COLORS["bg"], **kw)
        self.canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical",
                                       command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=COLORS["bg"])

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self._win = self.canvas.create_window((0, 0), window=self.inner,
                                              anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_inner_configure(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def _on_mousewheel(self, e):
        self.canvas.yview_scroll(int(-1*(e.delta/120)), "units")


# ── Order Form Dialog ─────────────────────────────────────────────────────────

class OrderFormDialog(tk.Toplevel):
    def __init__(self, parent, order_data: dict = None, templates: list = None):
        super().__init__(parent)
        self.result = None
        self.templates = templates or []
        self.edit_mode = order_data is not None
        self._order_data = order_data or {}

        self.title("Edit Order" if self.edit_mode else "New Order")
        self.configure(bg=COLORS["bg"])
        self.resizable(False, False)
        self.grab_set()

        self._build_ui()
        self._populate(order_data)
        self.wait_window()

    def _build_ui(self):
        pad = dict(padx=14, pady=5)
        # Header
        header = tk.Frame(self, bg=COLORS["surface"], padx=20, pady=14)
        header.pack(fill="x")
        icon = "✏️" if self.edit_mode else "➕"
        tk.Label(header, text=f"{icon}  {'Edit' if self.edit_mode else 'New'} Order",
                 bg=COLORS["surface"], fg=COLORS["accent"],
                 font=FONTS["subhead"]).pack(anchor="w")

        body = tk.Frame(self, bg=COLORS["bg"], padx=20, pady=10)
        body.pack(fill="both")

        # ── Customer
        self._section(body, "👤 Customer Info")
        self.v_name  = tk.StringVar()
        self.v_phone = tk.StringVar()
        self._row(body, "Name *",  styled_entry(body, self.v_name,  28))
        self._row(body, "Phone",   styled_entry(body, self.v_phone, 28))

        # ── Order details
        self._section(body, "📋 Order Details")
        self.v_type = tk.StringVar(value=ORDER_TYPES[0])
        self.v_qty  = tk.StringVar(value="1")
        combo = ttk.Combobox(body, textvariable=self.v_type,
                             values=ORDER_TYPES, state="readonly", width=22)
        self._row(body, "Order Type *", combo)
        self._row(body, "Quantity",     styled_entry(body, self.v_qty, 8))

        # ── Dimensions
        self._section(body, "📐 Dimensions (mm)")
        dim_frame = tk.Frame(body, bg=COLORS["bg"])
        self.v_w = tk.StringVar(value="100")
        self.v_h = tk.StringVar(value="50")
        tk.Label(dim_frame, text="Width", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(side="left")
        styled_entry(dim_frame, self.v_w, 8).pack(side="left", padx=(4,12))
        tk.Label(dim_frame, text="Height", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(side="left")
        styled_entry(dim_frame, self.v_h, 8).pack(side="left", padx=4)
        dim_frame.pack(anchor="w", **pad)

        # ── Text lines
        self._section(body, "✏️ Text Content")
        self.v_t1 = tk.StringVar()
        self.v_t2 = tk.StringVar()
        self.v_t3 = tk.StringVar()
        self.v_t4 = tk.StringVar()
        self._row(body, "Line 1 *", styled_entry(body, self.v_t1, 32))
        self._row(body, "Line 2",   styled_entry(body, self.v_t2, 32))
        self._row(body, "Line 3",   styled_entry(body, self.v_t3, 32))
        self._row(body, "Line 4",   styled_entry(body, self.v_t4, 32))

        # ── Template
        self._section(body, "🎨 Template")
        tpl_names = [t["name"] if isinstance(t, dict) else t.get("name","default")
                     for t in self.templates]
        self.v_template = tk.StringVar(value=tpl_names[0] if tpl_names else "default")
        ttk.Combobox(body, textvariable=self.v_template,
                     values=tpl_names, state="readonly", width=22
                     ).pack(anchor="w", **pad)

        # ── Notes
        self._section(body, "📝 Notes")
        self.notes_text = tk.Text(body, bg=COLORS["surface2"],
                                  fg=COLORS["text"], width=34, height=3,
                                  relief="flat", font=FONTS["body"],
                                  insertbackground=COLORS["accent"])
        self.notes_text.pack(anchor="w", **pad)

        # ── Status (edit only)
        if self.edit_mode:
            self._section(body, "🔖 Status")
            self.v_status = tk.StringVar(value="pending")
            ttk.Combobox(body, textvariable=self.v_status,
                         values=STATUS_OPTIONS, state="readonly", width=18
                         ).pack(anchor="w", **pad)

        # ── Buttons
        btn_frame = tk.Frame(self, bg=COLORS["bg"], padx=20, pady=12)
        btn_frame.pack(fill="x")
        styled_button(btn_frame, "💾 Save", command=self._save,
                      accent=True).pack(side="right", padx=4)
        styled_button(btn_frame, "Cancel",  command=self.destroy
                      ).pack(side="right", padx=4)

    def _section(self, parent, title):
        tk.Label(parent, text=title, bg=COLORS["bg"],
                 fg=COLORS["accent"], font=FONTS["small"]
                 ).pack(anchor="w", padx=14, pady=(10,2))

    def _row(self, parent, label, widget):
        row = tk.Frame(parent, bg=COLORS["bg"])
        tk.Label(row, text=label, bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"],
                 width=10, anchor="w").pack(side="left")
        widget.pack(side="left", padx=6)
        row.pack(anchor="w", padx=14, pady=3)

    def _populate(self, data):
        if not data:
            return
        self.v_name.set(data.get("customer_name", ""))
        self.v_phone.set(data.get("phone", ""))
        self.v_type.set(data.get("order_type", ORDER_TYPES[0]))
        self.v_qty.set(str(data.get("quantity", 1)))
        self.v_w.set(str(data.get("width_mm", 100)))
        self.v_h.set(str(data.get("height_mm", 50)))
        self.v_t1.set(data.get("text_line1", ""))
        self.v_t2.set(data.get("text_line2", ""))
        self.v_t3.set(data.get("text_line3", ""))
        self.v_t4.set(data.get("text_line4", ""))
        self.v_template.set(data.get("template_name", "default"))
        if data.get("notes"):
            self.notes_text.insert("1.0", data["notes"])
        if self.edit_mode and hasattr(self, "v_status"):
            self.v_status.set(data.get("status", "pending"))

    def _save(self):
        name = self.v_name.get().strip()
        if not name:
            messagebox.showwarning("Required", "Customer name is required.", parent=self)
            return
        try:
            w = float(self.v_w.get())
            h = float(self.v_h.get())
            qty = int(self.v_qty.get())
            assert w > 0 and h > 0 and qty > 0
        except Exception:
            messagebox.showwarning("Invalid", "Width, height, qty must be positive numbers.", parent=self)
            return

        self.result = {
            "customer_name":  name,
            "phone":          self.v_phone.get().strip(),
            "order_type":     self.v_type.get(),
            "width_mm":       w,
            "height_mm":      h,
            "quantity":       qty,
            "text_line1":     self.v_t1.get().strip(),
            "text_line2":     self.v_t2.get().strip(),
            "text_line3":     self.v_t3.get().strip(),
            "text_line4":     self.v_t4.get().strip(),
            "template_name":  self.v_template.get(),
            "notes":          self.notes_text.get("1.0", "end").strip(),
            "status":         (self.v_status.get() if hasattr(self, "v_status")
                               else self._order_data.get("status", "pending")),
        }
        self.destroy()


# ── Export Dialog ─────────────────────────────────────────────────────────────

class ExportDialog(tk.Toplevel):
    def __init__(self, parent, orders: list, templates: list):
        super().__init__(parent)
        self.result = None
        self.orders = orders
        self.templates = templates

        self.title("Export / Generate Designs")
        self.configure(bg=COLORS["bg"])
        self.resizable(False, False)
        self.grab_set()
        self._build()
        self.wait_window()

    def _build(self):
        pad = dict(padx=16, pady=6)
        header = tk.Frame(self, bg=COLORS["surface"], padx=20, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="🖨️  Export Designs to CorelDRAW",
                 bg=COLORS["surface"], fg=COLORS["accent"],
                 font=FONTS["subhead"]).pack(anchor="w")
        tk.Label(header, text=f"{len(self.orders)} order(s) selected",
                 bg=COLORS["surface"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(anchor="w")

        body = tk.Frame(self, bg=COLORS["bg"], padx=20, pady=12)
        body.pack(fill="both")

        # Mode
        self.v_mode = tk.StringVar(value="individual")
        tk.Label(body, text="Layout Mode", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(anchor="w")
        modes = [("Individual files per order",  "individual"),
                 ("Nested layout on single sheet","nested")]
        for txt, val in modes:
            tk.Radiobutton(body, text=txt, variable=self.v_mode, value=val,
                           bg=COLORS["bg"], fg=COLORS["text"],
                           selectcolor=COLORS["surface2"],
                           font=FONTS["body"], activebackground=COLORS["bg"]
                           ).pack(anchor="w", padx=10)

        # Sheet size (for nested)
        sf = tk.Frame(body, bg=COLORS["bg"])
        self.v_sw = tk.StringVar(value="297")
        self.v_sh = tk.StringVar(value="210")
        tk.Label(sf, text="Sheet W×H (mm):", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(side="left")
        styled_entry(sf, self.v_sw, 6).pack(side="left", padx=4)
        tk.Label(sf, text="×", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(side="left")
        styled_entry(sf, self.v_sh, 6).pack(side="left", padx=4)
        sf.pack(anchor="w", **pad)

        # Format
        self.v_fmt = tk.StringVar(value="cdr")
        tk.Label(body, text="Export Format", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(anchor="w")
        fmt_row = tk.Frame(body, bg=COLORS["bg"])
        for fmt in EXPORT_FORMATS:
            tk.Radiobutton(fmt_row, text=fmt.upper(), variable=self.v_fmt, value=fmt,
                           bg=COLORS["bg"], fg=COLORS["text"],
                           selectcolor=COLORS["surface2"],
                           font=FONTS["small"], activebackground=COLORS["bg"]
                           ).pack(side="left", padx=6)
        fmt_row.pack(anchor="w", **pad)

        # Output dir
        self.v_outdir = tk.StringVar(value=str(Path.home() / "CorelDRAW_Output"))
        dir_frame = tk.Frame(body, bg=COLORS["bg"])
        styled_entry(dir_frame, self.v_outdir, 28).pack(side="left")
        styled_button(dir_frame, "📂", command=self._browse, small=True
                      ).pack(side="left", padx=4)
        tk.Label(body, text="Output Folder", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(anchor="w")
        dir_frame.pack(anchor="w", **pad)

        # Open CorelDRAW visible?
        self.v_visible = tk.BooleanVar(value=True)
        tk.Checkbutton(body, text="Open CorelDRAW visibly",
                       variable=self.v_visible,
                       bg=COLORS["bg"], fg=COLORS["text"],
                       selectcolor=COLORS["surface2"],
                       font=FONTS["body"], activebackground=COLORS["bg"]
                       ).pack(anchor="w", **pad)

        btn_f = tk.Frame(self, bg=COLORS["bg"], padx=20, pady=12)
        btn_f.pack(fill="x")
        styled_button(btn_f, "🚀 Generate", command=self._go, accent=True
                      ).pack(side="right", padx=4)
        styled_button(btn_f, "Cancel", command=self.destroy
                      ).pack(side="right", padx=4)

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.v_outdir.set(d)

    def _go(self):
        self.result = {
            "mode":     self.v_mode.get(),
            "fmt":      self.v_fmt.get(),
            "out_dir":  Path(self.v_outdir.get()),
            "visible":  self.v_visible.get(),
            "sheet_w":  float(self.v_sw.get() or 297),
            "sheet_h":  float(self.v_sh.get() or 210),
        }
        self.destroy()


# ── Statistics Panel ──────────────────────────────────────────────────────────

class StatCard(tk.Frame):
    def __init__(self, parent, label, value, color, **kw):
        super().__init__(parent, bg=COLORS["surface"],
                         padx=18, pady=12, **kw)
        tk.Label(self, text=str(value), bg=COLORS["surface"],
                 fg=color, font=("Segoe UI", 26, "bold")).pack(anchor="w")
        tk.Label(self, text=label, bg=COLORS["surface"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(anchor="w")
        # left accent bar
        bar = tk.Frame(self, bg=color, width=4)
        bar.place(x=0, y=0, relheight=1)


# ── Main Application Window ───────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Order Manager  ⬥  CorelDRAW Automation")
        self.configure(bg=COLORS["bg"])
        self.minsize(1100, 680)
        self.geometry("1200x750")

        # State
        self._bridge = None
        self._bridge_lock = threading.Lock()  # NEW: thread-safe bridge access
        self._q: queue.Queue = queue.Queue()
        self._selected_ids: list = []
        self._templates: list = []

        # Init DB
        db.initialize_database()
        self._reload_templates()

        # Build UI
        self._build_header()
        self._build_body()
        self._build_status_bar()

        # Initial load
        self._refresh_stats()
        self._refresh_orders()

        # Poll background queue
        self.after(200, self._poll_queue)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self, bg=COLORS["surface"], padx=20, pady=14)
        hdr.pack(fill="x")

        tk.Label(hdr, text="⬥ CorelDRAW Order System",
                 bg=COLORS["surface"], fg=COLORS["accent"],
                 font=FONTS["title"]).pack(side="left")

        self._cdr_status_lbl = tk.Label(
            hdr, text="○  CorelDRAW: Disconnected",
            bg=COLORS["surface"], fg=COLORS["warning"],
            font=FONTS["small"]
        )
        self._cdr_status_lbl.pack(side="right", padx=12)

        styled_button(hdr, "Connect CDR", command=self._connect_cdr,
                      small=True).pack(side="right", padx=6)

    # ── Body layout ───────────────────────────────────────────────────────────

    def _build_body(self):
        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=0, pady=0)

        # Left sidebar (stats + actions)
        self._sidebar = tk.Frame(body, bg=COLORS["surface"], width=220)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        self._build_sidebar()

        # Main content area
        main = tk.Frame(body, bg=COLORS["bg"])
        main.pack(side="left", fill="both", expand=True, padx=0)
        self._build_toolbar(main)
        self._build_table(main)

    def _build_sidebar(self):
        s = self._sidebar
        tk.Label(s, text="DASHBOARD", bg=COLORS["surface"],
                 fg=COLORS["text_muted"], font=FONTS["small"],
                 padx=16, pady=12).pack(anchor="w")

        self._stat_total    = self._stat_card(s, "Total Orders",    "0", COLORS["accent"])
        self._stat_today    = self._stat_card(s, "Today",           "0", COLORS["accent3"])
        self._stat_pending  = self._stat_card(s, "Pending",         "0", COLORS["warning"])
        self._stat_exported = self._stat_card(s, "Exported",        "0", COLORS["success"])

        sep = tk.Frame(s, bg=COLORS["border"], height=1)
        sep.pack(fill="x", padx=10, pady=14)

        tk.Label(s, text="QUICK ACTIONS", bg=COLORS["surface"],
                 fg=COLORS["text_muted"], font=FONTS["small"],
                 padx=16, pady=4).pack(anchor="w")

        actions = [
            ("➕  New Order",      self._new_order),
            ("🖨️  Export Selected", self._export_selected),
            ("🗑️  Delete Selected", self._delete_selected),
            ("♻️  Refresh",         self._refresh_orders),
        ]
        for txt, cmd in actions:
            btn = tk.Button(s, text=txt, command=cmd,
                            bg=COLORS["surface"], fg=COLORS["text"],
                            font=FONTS["body"], relief="flat",
                            cursor="hand2", padx=16, pady=8, anchor="w",
                            activebackground=COLORS["surface2"],
                            activeforeground=COLORS["accent"])
            btn.pack(fill="x")

        sep2 = tk.Frame(s, bg=COLORS["border"], height=1)
        sep2.pack(fill="x", padx=10, pady=14)

        tk.Label(s, text="FILTER BY STATUS", bg=COLORS["surface"],
                 fg=COLORS["text_muted"], font=FONTS["small"],
                 padx=16, pady=4).pack(anchor="w")

        self.v_status_filter = tk.StringVar(value="all")
        for val, label in [("all","All"), ("pending","Pending"),
                           ("exported","Exported"), ("cancelled","Cancelled")]:
            tk.Radiobutton(
                s, text=label, variable=self.v_status_filter, value=val,
                command=self._refresh_orders,
                bg=COLORS["surface"], fg=COLORS["text"],
                selectcolor=COLORS["surface2"],
                font=FONTS["body"], activebackground=COLORS["surface"]
            ).pack(anchor="w", padx=16)

    def _stat_card(self, parent, label, value, color) -> tk.Label:
        card = tk.Frame(parent, bg=COLORS["surface"], padx=16, pady=10)
        card.pack(fill="x", padx=10, pady=3)
        num_lbl = tk.Label(card, text=value, bg=COLORS["surface"],
                           fg=color, font=("Segoe UI", 20, "bold"))
        num_lbl.pack(anchor="w")
        tk.Label(card, text=label, bg=COLORS["surface"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).pack(anchor="w")
        return num_lbl

    def _build_toolbar(self, parent):
        bar = tk.Frame(parent, bg=COLORS["surface"], padx=10, pady=8)
        bar.pack(fill="x")

        # Search
        tk.Label(bar, text="🔍", bg=COLORS["surface"],
                 fg=COLORS["text_muted"], font=FONTS["body"]).pack(side="left")
        self.v_search = tk.StringVar()
        self.v_search.trace("w", lambda *_: self._refresh_orders())
        search_e = styled_entry(bar, self.v_search, 26)
        search_e.configure(bg=COLORS["surface2"])
        search_e.pack(side="left", padx=6)

        styled_button(bar, "➕ New",    command=self._new_order,
                      accent=True, small=True).pack(side="right", padx=4)
        styled_button(bar, "🖨️ Export", command=self._export_selected,
                      small=True).pack(side="right", padx=4)
        styled_button(bar, "✏️ Edit",   command=self._edit_selected,
                      small=True).pack(side="right", padx=4)

    def _build_table(self, parent):
        cols = ("✓", "ID", "Order #", "Customer", "Type",
                "W×H (mm)", "Text", "Status", "Date")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Custom.Treeview",
                         background=COLORS["surface"],
                         foreground=COLORS["text"],
                         fieldbackground=COLORS["surface"],
                         rowheight=30,
                         borderwidth=0,
                         font=FONTS["body"])
        style.configure("Custom.Treeview.Heading",
                         background=COLORS["surface2"],
                         foreground=COLORS["accent"],
                         borderwidth=0,
                         font=FONTS["small"])
        style.map("Custom.Treeview",
                  background=[("selected", COLORS["surface2"])],
                  foreground=[("selected", COLORS["accent"])])

        frame = tk.Frame(parent, bg=COLORS["bg"])
        frame.pack(fill="both", expand=True, padx=6, pady=6)

        self.tree = ttk.Treeview(frame, columns=cols, show="headings",
                                 style="Custom.Treeview", selectmode="extended")
        col_widths = [30, 50, 130, 130, 90, 100, 200, 90, 110]
        for col, w in zip(cols, col_widths):
            self.tree.heading(col, text=col, anchor="w")
            self.tree.column(col, width=w, anchor="w")

        vsb = ttk.Scrollbar(frame, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right",  fill="y")
        self.tree.pack(fill="both", expand=True)

        self.tree.bind("<Double-1>", lambda _: self._edit_selected())
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_status_bar(self):
        bar = tk.Frame(self, bg=COLORS["surface2"], padx=12, pady=4)
        bar.pack(fill="x", side="bottom")
        self._status_lbl = tk.Label(bar, text="Ready",
                                    bg=COLORS["surface2"],
                                    fg=COLORS["text_muted"],
                                    font=FONTS["small"])
        self._status_lbl.pack(side="left")

        self._progress = ttk.Progressbar(bar, mode="indeterminate",
                                         length=150)
        self._progress.pack(side="right", padx=8)

    # ── Data Operations ───────────────────────────────────────────────────────

    def _refresh_orders(self, *_):
        q = self.v_search.get().strip()
        st = self.v_status_filter.get()
        rows = db.search_orders(q, st)
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            dims = f"{r['width_mm']}×{r['height_mm']}"
            date = (r["created_at"] or "")[:10]
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                "", r["id"], r["order_number"],
                r["customer_name"] or "—",
                r["order_type"],
                dims,
                (r["text_line1"] or "")[:35],
                r["status"],
                date,
            ))
            self.tree.item(str(r["id"]), tags=(r["status"],))

        # Tag colours for status
        self.tree.tag_configure("exported",  foreground=COLORS["success"])
        self.tree.tag_configure("pending",   foreground=COLORS["warning"])
        self.tree.tag_configure("cancelled", foreground=COLORS["danger"])
        self._refresh_stats()

    def _refresh_stats(self):
        stats = db.get_stats()
        self._stat_total.config(text=str(stats["total"]))
        self._stat_today.config(text=str(stats["today"]))
        self._stat_pending.config(text=str(stats["pending"]))
        self._stat_exported.config(text=str(stats["exported"]))

    def _reload_templates(self):
        self._templates = [dict(t) for t in db.get_all_templates()]

    def _on_select(self, _):
        self._selected_ids = [int(i) for i in self.tree.selection()]

    # ── CRUD Actions ──────────────────────────────────────────────────────────

    def _new_order(self):
        dlg = OrderFormDialog(self, templates=self._templates)
        if not dlg.result:
            return
        data = dlg.result
        cust_id = db.get_or_create_customer(
            data["customer_name"], data.get("phone",""), ""
        )
        data["customer_id"] = cust_id
        data.setdefault("status", "pending")
        db.create_order(data)
        self._refresh_orders()
        self._set_status(f"Order created: {data.get('order_number','')}")

    def _edit_selected(self):
        if not self._selected_ids:
            messagebox.showinfo("Select", "Please select an order to edit.")
            return
        oid = self._selected_ids[0]
        row = db.get_order(oid)
        if not row:
            return
        dlg = OrderFormDialog(self, dict(row), self._templates)
        if not dlg.result:
            return
        data = dlg.result
        cust_id = db.get_or_create_customer(
            data["customer_name"], data.get("phone",""), ""
        )
        data["customer_id"] = cust_id
        db.update_order(oid, data)
        self._refresh_orders()
        self._set_status(f"Order #{oid} updated.")

    def _delete_selected(self):
        if not self._selected_ids:
            return
        if not messagebox.askyesno("Delete",
                f"Delete {len(self._selected_ids)} order(s)?"):
            return
        for oid in self._selected_ids:
            db.delete_order(oid)
        self._refresh_orders()
        self._set_status(f"Deleted {len(self._selected_ids)} order(s).")

    # ── CorelDRAW Connection ──────────────────────────────────────────────────

    def _connect_cdr(self):
        self._set_status("Connecting to CorelDRAW …")
        self._progress.start(12)

        def worker():
            try:
                bridge = get_bridge()
                log.info(f"Got bridge: {type(bridge).__name__}")
                ok = bridge.connect(visible=True)
                log.info(f"Bridge.connect() returned: {ok}")
                log.info(f"Bridge.connected property: {getattr(bridge, 'connected', 'N/A')}")
                self._q.put(("cdr_connected", bridge, ok))
            except Exception as e:
                log.error(f"Connection error: {e}", exc_info=True)
                self._q.put(("cdr_error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_selected(self):
        # Capture selection BEFORE any dialog opens
        ids = list(self._selected_ids)
        if not ids:
            ids = [int(i) for i in self.tree.selection()]
        if not ids:
            messagebox.showinfo("Select", "Please select at least one order to export.")
            return

        # Build orders list
        orders = []
        for oid in ids:
            row = db.get_order(oid)
            if row is None:
                log.warning(f"Order id={oid} not found in DB – skipping.")
                continue
            orders.append(dict(row))
        if not orders:
            messagebox.showerror("Error", "Could not load the selected orders from database.")
            return

        dlg = ExportDialog(self, orders, self._templates)
        if not dlg.result:
            return
        opts = dlg.result

        # Ensure output directory exists
        out_dir = Path(opts["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)

        self._set_status(f"Exporting {len(orders)} design(s) …")
        self._progress.start(10)

        # Prepare bridge and options snapshot for worker thread
        with self._bridge_lock:
            existing_bridge = self._bridge
        
        visible = opts["visible"]

        def worker():
            results  = []
            errors   = []

            try:
                # Get or create bridge in worker thread
                with self._bridge_lock:
                    if existing_bridge and existing_bridge.connected:
                        bridge = existing_bridge
                        log.info("Using existing connected bridge")
                    else:
                        log.info("Creating new bridge in worker thread")
                        bridge = get_bridge()
                        if not bridge.connect(visible=visible):
                            log.warning("Bridge connection failed – checking connected state")
                            if not bridge.connected:
                                log.warning("Real bridge not available – switching to demo mode")
                                from coreldraw_bridge import DemoCorelDrawBridge
                                bridge = DemoCorelDrawBridge()
                                bridge.connect()

                log.info(f"Using bridge in worker: {type(bridge).__name__}, connected: {bridge.connected}")

                if opts["mode"] == "nested":
                    tpl_name = orders[0].get("template_name", "default")
                    tpl_row  = db.get_template(tpl_name)
                    tpl      = dict(tpl_row) if tpl_row else {}
                    path = bridge.create_nested_layout(
                        orders, tpl,
                        sheet_w_mm=opts["sheet_w"],
                        sheet_h_mm=opts["sheet_h"],
                        output_dir=out_dir,
                        export_format=opts["fmt"],
                    )
                    if path:
                        results.append(path)
                    else:
                        errors.append("Nested layout returned no output.")

                else:
                    for order in orders:
                        try:
                            tpl_name = order.get("template_name", "default") or "default"
                            tpl_row  = db.get_template(tpl_name)
                            tpl      = dict(tpl_row) if tpl_row else {}

                            log.info(f"Generating: {order.get('order_number')} "
                                     f"({order.get('width_mm')}×{order.get('height_mm')} mm) "
                                     f"template={tpl_name}")
                            log.info(f"  Bridge: {type(bridge).__name__}, connected: {bridge.connected}")

                            path = bridge.create_design_from_order(
                                order, tpl,
                                output_dir=out_dir,
                                export_format=opts["fmt"],
                            )

                            if path:
                                log.info(f"  ✅ Saved → {path}")
                                try:
                                    db.log_export(order["id"], opts["fmt"], str(path))
                                except Exception as db_exc:
                                    log.warning(f"  DB log failed: {db_exc}")
                                results.append(str(path))
                            else:
                                msg = (f"Order {order.get('order_number')} returned no path. "
                                       f"Check bridge connection and CorelDRAW availability.")
                                log.error(f"  ❌ {msg}")
                                errors.append(msg)

                        except Exception as order_exc:
                            err = f"Order {order.get('order_number','?')}: {order_exc}"
                            log.error(f"  ❌ {err}", exc_info=True)
                            errors.append(err)

                self._q.put(("export_done", results, errors))

            except Exception as fatal:
                log.error(f"Fatal export error: {fatal}", exc_info=True)
                self._q.put(("export_error", str(fatal)))

        threading.Thread(target=worker, daemon=True).start()

    # ── Background queue polling ──────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                msg = self._q.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        self.after(200, self._poll_queue)

    def _handle_message(self, msg):
        kind = msg[0]
        if kind == "cdr_connected":
            _, bridge, ok = msg
            self._progress.stop()
            with self._bridge_lock:
                self._bridge = bridge
            if ok:
                self._cdr_status_lbl.config(
                    text="●  CorelDRAW: Connected",
                    fg=COLORS["success"]
                )
                self._set_status("CorelDRAW connected successfully.")
            else:
                self._cdr_status_lbl.config(
                    text="○  CorelDRAW: Demo Mode",
                    fg=COLORS["warning"]
                )
                self._set_status("Running in demo mode (CorelDRAW not found).")

        elif kind == "cdr_error":
            _, err = msg
            self._progress.stop()
            self._cdr_status_lbl.config(
                text="⚠️  CorelDRAW: Error",
                fg=COLORS["danger"]
            )
            self._set_status(f"CorelDRAW connection error: {err}")
            messagebox.showerror("Connection Error", f"Failed to connect to CorelDRAW:\n\n{err}")

        elif kind == "export_done":
            _, paths, errors = msg
            self._progress.stop()
            self._refresh_orders()
            if paths:
                self._set_status(f"✅  Exported {len(paths)} file(s).")
                detail = "\n".join(str(p) for p in paths[:8])
                if errors:
                    detail += f"\n\n⚠️ {len(errors)} failed:\n" + "\n".join(errors[:3])
                messagebox.showinfo("Export Complete",
                                    f"Successfully exported {len(paths)} design(s).\n\n"
                                    + detail)
            else:
                self._set_status("❌  Export produced 0 files — check log.")
                err_detail = "\n".join(errors[:5]) if errors else "Unknown error."
                messagebox.showerror(
                    "Export Failed – 0 Designs",
                    "No designs were exported.\n\n"
                    "Possible reasons:\n"
                    "• CorelDRAW not running or not connected\n"
                    "• pywin32 not installed\n"
                    "• Output folder is read-only\n"
                    "• Template not found\n\n"
                    f"Details:\n{err_detail}"
                )

        elif kind == "export_error":
            _, err = msg
            self._progress.stop()
            self._set_status(f"❌  Export error: {err}")
            messagebox.showerror("Export Failed", str(err))

    # ── Status bar ────────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        self._status_lbl.config(text=text)
        log.info(text)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Warn user if CorelDRAW automation unavailable
    if not COM_AVAILABLE:
        messagebox.showwarning(
            "CorelDRAW Automation Unavailable",
            "pywin32 is not installed.\n\n"
            "Export functionality will NOT work.\n\n"
            "To fix this, run in terminal:\n"
            "pip install pywin32"
        )

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
