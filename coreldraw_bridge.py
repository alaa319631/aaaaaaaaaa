"""
coreldraw_bridge.py - CorelDRAW COM Automation Engine
Interfaces with CorelDRAW via pywin32 / win32com for full design automation.

Requires:
  pip install pywin32
  CorelDRAW 2021 / 2022 / 2023 / 2024 installed on the same machine.
"""

import os
import math
import json
import time
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("CDR")

# ── Safe COM import (graceful on non-Windows machines) ────────────────────────
try:
    import win32com.client as wc
    import pythoncom
    COM_AVAILABLE = True
except ImportError:
    COM_AVAILABLE = False
    log.warning("pywin32 not available – CorelDRAW automation disabled (demo mode).")


# ── Unit conversion constants ─────────────────────────────────────────────────
# CorelDRAW internal unit = 1/100 of a millimetre (0.01 mm)
MM_TO_CDR  = 100.0      # multiply mm  → CorelDRAW units
PT_TO_CDR  = 3.527778   # 1 pt = 0.353 mm → 35.28 CDR units (rough)
CDR_UNIT   = 0          # cdrMillimeter constant value in CorelDRAW enum

# CorelDRAW shape constants
CDR_RECTANGLE = 3
CDR_ELLIPSE   = 4

# Alignment constants
CDR_ALIGN_CENTER = 2
CDR_ALIGN_LEFT   = 1
CDR_ALIGN_RIGHT  = 3

# Export filter types (CorelDRAW cdrFilter enum values)
EXPORT_FILTERS = {
    "cdr": None,   # native save
    "pdf": 494,    # cdrPDF
    "dxf": 489,    # cdrDXF
    "svg": 464,    # cdrSVG
    "png": 497,    # cdrPNG
    "jpg": 49,     # cdrJPEG
}


class CorelDrawBridge:
    """
    High-level interface to CorelDRAW.

    Usage:
        bridge = CorelDrawBridge()
        bridge.connect()
        doc_path = bridge.create_design_from_order(order_dict, template_dict)
        bridge.disconnect()
    """

    def __init__(self):
        self.app = None
        self._connected = False
        self._last_check_time = 0
        self._check_interval = 1.0  # Check connection every 1 second

    # ── Connection Management ─────────────────────────────────────────────────

    def _validate_connection(self) -> bool:
        """
        Check if COM object is still valid and connected.
        Returns True if still connected, False otherwise.
        """
        if not self.app:
            log.debug("App object is None")
            return False
        
        try:
            # Try to access a simple property to verify connection
            _ = self.app.Version
            log.debug(f"Connection validated. CorelDRAW version: {self.app.Version}")
            return True
        except Exception as e:
            log.warning(f"Connection validation failed: {type(e).__name__}: {e}")
            self.app = None
            self._connected = False
            return False

    def connect(self, visible: bool = True) -> bool:
        """Launch or attach to a running CorelDRAW instance."""
        if not COM_AVAILABLE:
            log.warning("COM not available – running in demo mode.")
            return False
        try:
            pythoncom.CoInitialize()
            
            # Try to get existing instance first
            try:
                self.app = wc.GetActiveObject("CorelDRAW.Application")
                log.info("Attached to existing CorelDRAW instance.")
                # Validate it's actually connected
                if not self._validate_connection():
                    log.warning("Existing instance validation failed, launching new one")
                    self.app = wc.Dispatch("CorelDRAW.Application")
            except Exception as e:
                log.debug(f"No active instance found: {e}. Launching new one.")
                self.app = wc.Dispatch("CorelDRAW.Application")
                log.info("Launched new CorelDRAW instance.")

            # Validate new/existing connection
            if not self._validate_connection():
                log.error("Failed to validate CorelDRAW connection")
                self.app = None
                return False

            self.app.Visible = visible
            self._connected = True
            log.info(f"✅ Successfully connected to CorelDRAW (version {self.app.Version})")
            return True
            
        except Exception as exc:
            log.error(f"Failed to connect to CorelDRAW: {exc}", exc_info=True)
            self.app = None
            self._connected = False
            return False

    def disconnect(self):
        """Release COM object."""
        self.app = None
        self._connected = False
        if COM_AVAILABLE:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        """Check if still connected (with lazy validation)."""
        if not self._connected or not self.app:
            return False
        
        # Occasionally validate the connection
        current_time = time.time()
        if current_time - self._last_check_time > self._check_interval:
            self._last_check_time = current_time
            if not self._validate_connection():
                return False
        
        return True

    # ── Core Design Creation ──────────────────────────────────────────────────

    def create_design_from_order(
        self,
        order: dict,
        template: dict,
        output_dir: Optional[Path] = None,
        export_format: str = "cdr",
    ) -> Optional[str]:
        """
        Full pipeline: new doc → draw shape → insert text → format → export.
        Returns the exported file path, or None on failure.
        """
        log.info(f"create_design_from_order called: connected={self.connected}, app={self.app}")
        
        if not self.connected:
            log.error("Not connected to CorelDRAW.")
            return None

        try:
            log.info(f"Creating document: {order.get('width_mm')}x{order.get('height_mm')} mm")
            doc  = self._new_document(order["width_mm"], order["height_mm"])
            log.info(f"Document created successfully")
            page = doc.ActivePage
            layer = page.ActiveLayer

            # Draw the main shape
            shape = self._draw_shape(layer, order, template)

            # Insert text objects
            text_objects = self._insert_text_objects(layer, order, template)

            # Group shape + text for easy manipulation
            all_shapes = [shape] + text_objects
            group = self._group_shapes(doc, all_shapes)

            # Center the group on the page
            self._center_on_page(group, order["width_mm"], order["height_mm"])

            # Export / save
            out_path = self._export_document(
                doc, order, output_dir or Path.cwd(), export_format
            )
            return out_path

        except Exception as exc:
            log.error(f"Design creation failed: {exc}", exc_info=True)
            return None

    # ── Document Setup ────────────────────────────────────────────────────────

    def _new_document(self, width_mm: float, height_mm: float):
        """Create a new CorelDRAW document sized to the order dimensions."""
        try:
            doc = self.app.Documents.Add(
                width_mm, height_mm,
                1,        # pages
                96,       # resolution
                0,        # cdrMillimeter
                True      # primaryColorModel = True (CMYK)
            )
            doc.ActivePage.SizeWidth  = width_mm
            doc.ActivePage.SizeHeight = height_mm
            log.info(f"Document created: {width_mm}×{height_mm}mm")
            return doc
        except Exception as e:
            log.error(f"Failed to create document: {e}", exc_info=True)
            raise

    # ── Shape Drawing ─────────────────────────────────────────────────────────

    def _draw_shape(self, layer, order: dict, template: dict):
        """Draw the primary bounding shape based on template settings."""
        w  = order["width_mm"]
        h  = order["height_mm"]
        x  = 0.0
        y  = 0.0
        st = template.get("shape_type", "rectangle")
        extra = json.loads(template.get("extra_props", "{}"))

        try:
            if st == "circle" or st == "ellipse":
                shape = layer.CreateEllipse2(x, y, w, h)
            elif st == "rounded":
                shape = layer.CreateRectangle2(x, y, w, h)
                r = extra.get("corner_radius", 3)
                shape.SetRectangleRoundness(r, r, r, r)
            else:
                shape = layer.CreateRectangle2(x, y, w, h)

            # Fill
            fr = template.get("fill_color_r", 255)
            fg = template.get("fill_color_g", 255)
            fb = template.get("fill_color_b", 255)
            shape.Fill.ApplyUniformFill(
                self._make_color(fr, fg, fb)
            )

            # Outline
            br = template.get("border_color_r", 0)
            bg = template.get("border_color_g", 0)
            bb = template.get("border_color_b", 0)
            bw = template.get("border_width_mm", 0.5)
            outline = shape.Outline
            outline.SetProperties(
                bw,
                0, 0,
                self._make_color(br, bg, bb),
                False, False, False, False, False, False
            )

            # Optional inner circle for badge style
            if extra.get("inner_circle") and st == "circle":
                margin = template.get("margin_mm", 5)
                ic = layer.CreateEllipse2(
                    margin, margin, w - 2*margin, h - 2*margin
                )
                ic.Fill.ApplyNoFill()
                ic.Outline.SetProperties(
                    bw * 0.5, 0, 0,
                    self._make_color(br, bg, bb),
                    False, False, False, False, False, False
                )

            log.info(f"Shape created: {st} {w}×{h}mm")
            return shape
        except Exception as e:
            log.error(f"Failed to draw shape: {e}", exc_info=True)
            raise

    # ── Text Insertion ────────────────────────────────────────────────────────

    def _insert_text_objects(self, layer, order: dict, template: dict) -> list:
        """Create one artistic text object per non-empty text line."""
        lines = [
            order.get("text_line1", ""),
            order.get("text_line2", ""),
            order.get("text_line3", ""),
            order.get("text_line4", ""),
        ]
        lines = [l for l in lines if l and str(l).strip()]

        font      = template.get("font_name", "Arial")
        font_size = float(template.get("font_size_pt", 12))
        margin    = float(template.get("margin_mm", 5))
        align_str = template.get("text_align", "center")
        align_map = {"left": CDR_ALIGN_LEFT, "center": CDR_ALIGN_CENTER,
                     "right": CDR_ALIGN_RIGHT}
        align = align_map.get(align_str, CDR_ALIGN_CENTER)

        w = order["width_mm"]
        h = order["height_mm"]
        avail_h = h - 2 * margin
        line_h  = avail_h / max(len(lines), 1)
        fr      = template.get("border_color_r", 0)
        fg_c    = template.get("border_color_g", 0)
        fb      = template.get("border_color_b", 0)

        text_objects = []
        try:
            for i, line in enumerate(lines):
                # Y coordinate from bottom (CorelDRAW origin = bottom-left)
                y_pos = (h - margin) - (i + 0.5) * line_h

                txt = layer.CreateArtisticText(
                    margin, y_pos,     # x, y
                    0,                 # angle
                    0,                 # skew
                    False,             # right-to-left
                    line               # text
                )
                # Font & size
                range_ = txt.Text.Story.TextRange
                range_.Font = font
                range_.Size = font_size

                # Color (match border/text color from template)
                range_.Fill.ApplyUniformFill(self._make_color(fr, fg_c, fb))

                # Alignment
                txt.Text.Story.TextRange.Alignment = align

                # Fit width within available area
                txt.SetSize(w - 2 * margin, line_h * 0.85)

                text_objects.append(txt)

            log.info(f"Created {len(text_objects)} text objects")
            return text_objects
        except Exception as e:
            log.error(f"Failed to insert text objects: {e}", exc_info=True)
            raise

    # ── Grouping & Layout ─────────────────────────────────────────────────────

    def _group_shapes(self, doc, shapes: list):
        """Select all shapes and group them."""
        sel = doc.ActivePage.Shapes
        sel.DeselectAll()
        for s in shapes:
            s.AddToSelection()
        return doc.ActivePage.Selection.Group()

    def _center_on_page(self, shape, page_w: float, page_h: float):
        """Move shape so its centre aligns with the page centre."""
        cx = page_w / 2.0
        cy = page_h / 2.0
        shape.CenterX = cx
        shape.CenterY = cy

    # ── Nesting / Layout Optimisation ─────────────────────────────────────────

    def create_nested_layout(
        self,
        orders: list,
        template: dict,
        sheet_w_mm: float = 297.0,
        sheet_h_mm: float = 210.0,
        gap_mm: float = 2.0,
        output_dir: Optional[Path] = None,
        export_format: str = "pdf",
    ) -> Optional[str]:
        """
        Arrange multiple order designs on one sheet (simple bin-pack algorithm).
        Returns the exported file path.
        """
        if not self.connected:
            log.error("Not connected to CorelDRAW for nested layout")
            return None
        try:
            doc  = self._new_document(sheet_w_mm, sheet_h_mm)
            page = doc.ActivePage
            layer = page.ActiveLayer

            x_cursor = gap_mm
            y_cursor = gap_mm
            row_max_h = 0.0

            for order in orders:
                ow = float(order["width_mm"])
                oh = float(order["height_mm"])

                # Wrap to next row if needed
                if x_cursor + ow + gap_mm > sheet_w_mm:
                    x_cursor  = gap_mm
                    y_cursor += row_max_h + gap_mm
                    row_max_h = 0.0

                if y_cursor + oh + gap_mm > sheet_h_mm:
                    log.warning("Sheet full – remaining orders skipped.")
                    break

                # Draw shape at cursor position
                st = template.get("shape_type", "rectangle")
                if st in ("circle", "ellipse"):
                    s = layer.CreateEllipse2(x_cursor, y_cursor, ow, oh)
                else:
                    s = layer.CreateRectangle2(x_cursor, y_cursor, ow, oh)

                fr = template.get("fill_color_r", 255)
                fg = template.get("fill_color_g", 255)
                fb = template.get("fill_color_b", 255)
                s.Fill.ApplyUniformFill(self._make_color(fr, fg, fb))
                bw = template.get("border_width_mm", 0.5)
                s.Outline.SetProperties(bw, 0, 0, self._make_color(0,0,0),
                                        False, False, False, False, False, False)

                # Text label
                label = order.get("text_line1", order.get("order_number", ""))
                if label:
                    font = template.get("font_name", "Arial")
                    fsize = min(float(template.get("font_size_pt", 10)),
                                oh * 2.0)
                    t = layer.CreateArtisticText(
                        x_cursor + 1, y_cursor + oh / 2, 0, 0, False, str(label)
                    )
                    t.Text.Story.TextRange.Font = font
                    t.Text.Story.TextRange.Size = fsize

                x_cursor += ow + gap_mm
                row_max_h = max(row_max_h, oh)

            out_path = self._export_document(
                doc,
                {"order_number": "NESTED_LAYOUT",
                 "customer_name": "batch",
                 "order_type": "batch"},
                output_dir or Path.cwd(),
                export_format,
            )
            return out_path
        except Exception as exc:
            log.error(f"Nested layout failed: {exc}", exc_info=True)
            return None

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_document(
        self,
        doc,
        order: dict,
        output_dir: Path,
        fmt: str,
    ) -> str:
        """Save or export the document and return the output path."""
        try:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_name = (
                f"{order.get('order_number','order')}_"
                f"{order.get('order_type','design').replace(' ','_')}"
            )
            fmt = fmt.lower().strip(".")

            if fmt == "cdr":
                out_path = str(output_dir / f"{safe_name}.cdr")
                doc.SaveAs(out_path)
                log.info(f"Saved CDR: {out_path}")
            else:
                filter_id = EXPORT_FILTERS.get(fmt)
                if filter_id is None:
                    log.warning(f"Unknown format {fmt!r}; defaulting to PDF.")
                    fmt = "pdf"
                    filter_id = EXPORT_FILTERS["pdf"]
                out_path = str(output_dir / f"{safe_name}.{fmt}")
                export_obj = doc.ExportEx(out_path, filter_id, 0)
                if hasattr(export_obj, "Finish"):
                    export_obj.Finish()
                log.info(f"Exported {fmt.upper()}: {out_path}")

            return out_path
        except Exception as e:
            log.error(f"Export failed: {e}", exc_info=True)
            raise

    # ── Color Helper ──────────────────────────────────────────────────────────

    def _make_color(self, r: int, g: int, b: int):
        """Create a CorelDRAW color object from RGB values."""
        color = wc.Dispatch("CorelDRAW.Color")
        color.RGBAssign(r, g, b)
        return color

    # ── Template Application ──────────────────────────────────────────────────

    def apply_template_to_document(self, doc, template: dict):
        """Apply template-level document settings (future expansion)."""
        pass


# ── Demo mode helper ──────────────────────────────────────────────────────────

class DemoCorelDrawBridge:
    """
    Fake bridge used when CorelDRAW / pywin32 is not available.
    Logs what WOULD happen so the UI can still be developed/tested.
    """

    connected = True   # always "connected" in demo mode

    def connect(self, visible=True) -> bool:
        log.info("[DEMO] CorelDRAW bridge connected (simulation).")
        return True

    def disconnect(self):
        log.info("[DEMO] Disconnected.")

    def create_design_from_order(self, order, template, output_dir=None, export_format="cdr"):
        path = Path(output_dir or ".") / f"{order.get('order_number','demo')}.{export_format}"
        log.info(f"[DEMO] Would create design → {path}")
        log.info(f"       Shape : {template.get('shape_type','rectangle')} "
                 f"{order.get('width_mm')} × {order.get('height_mm')} mm")
        log.info(f"       Text  : {order.get('text_line1')} | {order.get('text_line2')}")
        log.info(f"       Font  : {template.get('font_name')} {template.get('font_size_pt')}pt")
        # Simulate a delay so progress bar makes sense
        time.sleep(1.2)
        return str(path)

    def create_nested_layout(self, orders, template, sheet_w_mm=297, sheet_h_mm=210,
                             gap_mm=2, output_dir=None, export_format="pdf"):
        path = Path(output_dir or ".") / f"nested_layout.{export_format}"
        log.info(f"[DEMO] Would nest {len(orders)} designs on {sheet_w_mm}×{sheet_h_mm}mm sheet → {path}")
        time.sleep(1.5)
        return str(path)


def get_bridge() -> "CorelDrawBridge | DemoCorelDrawBridge":
    """Factory: returns real bridge if COM available, else demo."""
    if COM_AVAILABLE:
        return CorelDrawBridge()
    return DemoCorelDrawBridge()
