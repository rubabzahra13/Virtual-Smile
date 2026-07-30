"""Send assessment report emails via Brevo with a dashboard-matching PDF."""

from __future__ import annotations

import base64
import html
import io
import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

CATEGORY_ORDER = (
    "alignment",
    "gum_health",
    "color",
    "restorations",
    "missing_teeth",
)
CATEGORY_LABELS = {
    "alignment": "Alignment",
    "gum_health": "Gum health",
    "color": "Tooth colour",
    "restorations": "Restorations",
    "missing_teeth": "Missing teeth",
}


def email_configured() -> bool:
    return bool(
        (os.getenv("BREVO_API_KEY") or "").strip()
        and (os.getenv("BREVO_FROM_EMAIL") or "").strip()
    )


def _parse_sender(raw: str) -> tuple[str, str]:
    """Parse `Name <email@domain>` into Brevo sender fields."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    match = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", raw)
    if match:
        name = (match.group(1) or "").strip() or "The Global Dentist"
        email = (match.group(2) or "").strip()
        return name, email
    return "The Global Dentist", raw


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _format_concern_label(label: Any) -> str:
    text = str(label or "").replace("_", " ").strip()
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), text)


def _score_band(score: int) -> tuple[str, str]:
    if score >= 90:
        return "Good", "#0d7a4f"
    if score >= 75:
        return "Watch", "#9a6a00"
    return "Attention", "#b42318"


def _normalize_category_scores(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key in CATEGORY_ORDER:
        val = raw.get(key)
        if isinstance(val, (int, float)):
            out[key] = max(0, min(100, int(round(val))))
    return out


def _concern_details(findings: Any) -> list[dict[str, Any]]:
    if not isinstance(findings, dict):
        return []
    visible = findings.get("visible_concerns") or []
    details = findings.get("concern_details") or []
    by_concern: dict[str, dict] = {}
    if isinstance(details, list):
        for row in details:
            if isinstance(row, dict) and row.get("concern"):
                by_concern[str(row["concern"])] = row

    rows: list[dict[str, Any]] = []
    if isinstance(visible, list):
        for item in visible:
            key = str(item).strip()
            if not key:
                continue
            detail = by_concern.get(key, {})
            options = detail.get("treatment_options")
            if isinstance(options, list):
                treatment = ", ".join(str(o).strip() for o in options if str(o).strip())
            elif isinstance(options, str):
                treatment = options.strip()
            else:
                treatment = ""
            rows.append(
                {
                    "label": _format_concern_label(key),
                    "meaning": str(
                        detail.get("likely_cause")
                        or "This was visible in the photo and may need a professional check."
                    ).strip(),
                    "treatment": treatment
                    or "A dental consultation to confirm and plan treatment.",
                }
            )
    return rows


def _roadmap_items(findings: Any) -> list[str]:
    if not isinstance(findings, dict):
        return ["Book a routine dentist visit to confirm this AI screening."]
    items = findings.get("treatment_roadmap")
    if not isinstance(items, list) or not items:
        return ["Book a routine dentist visit to confirm this AI screening."]
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text)
    return out or ["Book a routine dentist visit to confirm this AI screening."]


def build_email_html(*, to_email: str) -> str:
    greeting_name = (to_email or "").split("@", 1)[0].replace(".", " ").strip()
    greeting = f"Hello {greeting_name}," if greeting_name else "Hello,"
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Georgia,serif;color:#1a2a4a;line-height:1.55;max-width:640px;margin:0 auto;padding:24px;">
  <p>{_esc(greeting)}</p>
  <p>
    Thank you for completing your Virtual Smile Assessment with The Global Dentist.
  </p>
  <p>
    We’ve put together a personalised summary of what we could see in your smile photos.
    You’ll find your full report attached as a PDF. Open it anytime to review your score,
    findings, and suggested next steps.
  </p>
  <p>
    If anything in the report raises a question, or you’d like to talk through treatment options,
    we’re happy to help when you visit the clinic.
  </p>
  <p style="font-size:13px;color:#5a6a80;">
    Please remember this is a preliminary photo-based AI screening, not a clinical diagnosis.
    A dentist exam (and X-rays when needed) is the best way to confirm findings and plan care.
  </p>
  <p>Warm regards,<br>The Global Dentist</p>
</body>
</html>
""".strip()


def _pdf_photo_cell(label: str, image_bytes: bytes, max_width: float, max_height: float):
    """Return a labeled ReportLab image cell, or None if the bytes cannot be decoded."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Image as RLImage, Paragraph, Table, TableStyle
    from PIL import Image as PILImage

    try:
        pil = PILImage.open(io.BytesIO(image_bytes))
        pil = pil.convert("RGB")
        # Honour EXIF orientation when present.
        try:
            from PIL import ImageOps
            pil = ImageOps.exif_transpose(pil) or pil
        except Exception:
            pass
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=82, optimize=True)
        buf.seek(0)
        w, h = pil.size
        if w <= 0 or h <= 0:
            return None
        scale = min(max_width / float(w), max_height / float(h), 1.0)
        img = RLImage(buf, width=w * scale, height=h * scale)
    except Exception:
        logger.exception("Could not embed assessment photo in PDF")
        return None

    label_style = ParagraphStyle(
        "PhotoLabel",
        fontName="Helvetica-Bold",
        fontSize=8,
        textColor=HexColor("#5a6a80"),
        alignment=TA_CENTER,
        leading=10,
        spaceAfter=4,
    )
    cell = Table(
        [[Paragraph(_esc(label), label_style)], [img]],
        colWidths=[max_width],
    )
    cell.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return cell


def build_report_pdf_bytes(
    *,
    overall_score: Optional[int],
    category_scores: Any,
    findings: Any,
    images: Optional[list[tuple[str, bytes]]] = None,
) -> bytes:
    """Build a modern, aligned PDF with ReportLab (xhtml2pdf layout is unreliable)."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Flowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    navy = HexColor("#183068")
    teal = HexColor("#009898")
    teal_soft = HexColor("#eef7f7")
    ink_soft = HexColor("#5a6a80")
    line = HexColor("#d9e2ef")
    warm_bg = HexColor("#fffaf8")
    warm_head = HexColor("#ffe8dc")
    warm_ink = HexColor("#9a3412")
    warm_line = HexColor("#f0c7b4")

    class ProgressBar(Flowable):
        def __init__(self, value: int, width: float = 160, height: float = 8):
            super().__init__()
            self.value = max(0, min(100, int(value)))
            self.width = width
            self.height = height

        def wrap(self, availWidth, availHeight):
            return self.width, self.height

        def draw(self):
            self.canv.setFillColor(HexColor("#d9e2ef"))
            self.canv.roundRect(0, 0, self.width, self.height, 3, stroke=0, fill=1)
            filled = max(3.0, self.width * (self.value / 100.0))
            self.canv.setFillColor(teal)
            self.canv.roundRect(0, 0, filled, self.height, 3, stroke=0, fill=1)

    class SectionTitle(Flowable):
        def __init__(self, text: str, width: float):
            super().__init__()
            self.text = text
            self._width = width
            self.height = 26

        def wrap(self, availWidth, availHeight):
            self._width = availWidth
            return self._width, self.height

        def draw(self):
            self.canv.setFillColor(teal)
            self.canv.rect(0, 0, 4, self.height, stroke=0, fill=1)
            self.canv.setFillColor(teal_soft)
            self.canv.rect(4, 0, self._width - 4, self.height, stroke=0, fill=1)
            self.canv.setFillColor(navy)
            self.canv.setFont("Helvetica-Bold", 11)
            self.canv.drawString(14, 8, self.text)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Virtual Smile Assessment",
        author="The Global Dentist",
    )
    content_width = A4[0] - doc.leftMargin - doc.rightMargin

    styles = getSampleStyleSheet()
    brand = ParagraphStyle(
        "Brand", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=9, textColor=teal, spaceAfter=2, leading=12,
    )
    title = ParagraphStyle(
        "Title", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=20, textColor=navy, spaceAfter=0, leading=24,
    )
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontName="Helvetica",
        fontSize=10, textColor=ink_soft, leading=14,
    )
    body_navy = ParagraphStyle(
        "BodyNavy", parent=styles["Normal"], fontName="Helvetica",
        fontSize=10, textColor=navy, leading=14,
    )
    h_score = ParagraphStyle(
        "HScore", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=14, textColor=navy, leading=18, spaceAfter=4,
    )
    small = ParagraphStyle(
        "Small", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8.5, textColor=HexColor("#7a8799"), leading=12,
    )
    label = ParagraphStyle(
        "Label", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8, textColor=HexColor("#7a8799"), leading=10, spaceAfter=2,
    )
    card_title = ParagraphStyle(
        "CardTitle", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=10, textColor=warm_ink, alignment=TA_CENTER, leading=13,
    )
    footer = ParagraphStyle(
        "Footer", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8, textColor=HexColor("#7a8799"), leading=11,
    )

    story: list[Any] = []

    score_val = overall_score if isinstance(overall_score, int) else None
    band_label, band_color_hex = (
        _score_band(score_val) if score_val is not None else ("Pending", "#6a7a90")
    )
    band_bg_hex = {
        "Good": "#e6f6ee",
        "Watch": "#fff4d6",
        "Attention": "#fde8e6",
        "Pending": "#eef1f6",
    }.get(band_label, "#eef1f6")

    status_label_style = ParagraphStyle(
        "StatusLabel",
        parent=small,
        fontName="Helvetica",
        fontSize=8,
        textColor=ink_soft,
        alignment=TA_RIGHT,
        leading=10,
    )
    band_style = ParagraphStyle(
        "Band",
        parent=small,
        alignment=TA_CENTER,
        textColor=HexColor(band_color_hex),
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
    )
    status_tag = Table(
        [[
            Paragraph("Status:", status_label_style),
            Paragraph(
                f'<font color="{band_color_hex}"><b>{band_label.upper()}</b></font>',
                band_style,
            ),
        ]],
        colWidths=[16 * mm, 28 * mm],
    )
    status_tag.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "RIGHT"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 6),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("RIGHTPADDING", (1, 0), (1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (1, 0), (1, 0), HexColor(band_bg_hex)),
        ("BOX", (1, 0), (1, 0), 0.5, HexColor(band_bg_hex)),
    ]))
    header = Table(
        [[
            [Paragraph("THE GLOBAL DENTIST", brand), Paragraph("Virtual Smile Assessment", title)],
            status_tag,
        ]],
        colWidths=[content_width - 48 * mm, 48 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 12))

    score_text = str(score_val) if score_val is not None else "-"
    score_num_style = ParagraphStyle(
        "BigScore",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=28,
        textColor=HexColor("#ffffff"),
        alignment=TA_CENTER,
        leading=32,
    )
    score_sub_style = ParagraphStyle(
        "OutOf",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        textColor=HexColor("#d7f3f3"),
        alignment=TA_CENTER,
        leading=9,
        spaceBefore=2,
    )
    score_box = Table(
        [[
            [Paragraph(score_text, score_num_style), Paragraph("OUT OF 100", score_sub_style)],
            [
                Paragraph("Your Smile Score", h_score),
                Paragraph("Your preliminary visual assessment is ready.", body),
                Spacer(1, 4),
                Paragraph("Not a diagnosis. Clinical confirmation is required.", small),
            ],
        ]],
        colWidths=[40 * mm, content_width - 40 * mm],
        rowHeights=[30 * mm],
    )
    score_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), teal),
        ("BACKGROUND", (1, 0), (1, 0), HexColor("#f3fbfb")),
        ("BOX", (0, 0), (-1, -1), 1, HexColor("#98d4d4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (0, 0), 8),
        ("RIGHTPADDING", (0, 0), (0, 0), 8),
        ("LEFTPADDING", (1, 0), (1, 0), 14),
        ("RIGHTPADDING", (1, 0), (1, 0), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(score_box)
    story.append(Spacer(1, 14))

    photo_items: list[tuple[str, bytes]] = []
    if isinstance(images, list):
        for item in images:
            if (
                isinstance(item, (tuple, list))
                and len(item) >= 2
                and isinstance(item[0], str)
                and isinstance(item[1], (bytes, bytearray))
                and item[1]
            ):
                photo_items.append((item[0], bytes(item[1])))
    if photo_items:
        story.append(SectionTitle("Your uploaded smile", content_width))
        story.append(Spacer(1, 8))
        n = len(photo_items)
        gap = 4 * mm
        cell_w = (content_width - gap * max(0, n - 1)) / n
        max_h = 48 * mm if n == 1 else 42 * mm
        cells: list[Any] = []
        widths: list[float] = []
        for i, (label_txt, raw) in enumerate(photo_items):
            if i:
                cells.append("")
                widths.append(gap)
            cell = _pdf_photo_cell(label_txt, raw, max_width=cell_w - 2, max_height=max_h)
            if cell is None:
                cells.append(Paragraph(_esc(label_txt), body))
            else:
                cells.append(cell)
            widths.append(cell_w)
        story.append(Table([cells], colWidths=widths))
        story.append(Spacer(1, 14))

    cats = _normalize_category_scores(category_scores)
    if not cats and isinstance(findings, dict):
        cats = _normalize_category_scores(findings.get("scores"))
    if cats:
        story.append(SectionTitle("Category breakdown", content_width))
        story.append(Spacer(1, 8))
        rows = []
        for key in CATEGORY_ORDER:
            if key not in cats:
                continue
            value = cats[key]
            label_txt, color_hex = _score_band(value)
            rows.append([
                Paragraph(f"<b>{CATEGORY_LABELS[key]}</b>", body_navy),
                ProgressBar(value, width=48 * mm, height=8),
                Paragraph(f"<b>{value}</b><font color='#7a8799' size='8'>/100</font>", ParagraphStyle("ScoreNum", parent=body_navy, alignment=TA_CENTER)),
                Paragraph(f'<font color="{color_hex}"><b>{label_txt}</b></font>', ParagraphStyle("BandCell", parent=body, alignment=TA_CENTER, textColor=HexColor(color_hex))),
            ])
        cat_table = Table(rows, colWidths=[40 * mm, content_width - 90 * mm, 24 * mm, 26 * mm])
        cat_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, line),
        ]))
        # Stretch bars to the flexible middle column width.
        for row in rows:
            if isinstance(row[1], ProgressBar):
                row[1].width = content_width - 90 * mm
        story.append(cat_table)
        story.append(Spacer(1, 14))

    concerns = _concern_details(findings)
    story.append(SectionTitle("Visual findings", content_width))
    story.append(Spacer(1, 8))
    if concerns:
        story.append(Paragraph("Visible areas to discuss with your dentist.", body))
        story.append(Spacer(1, 6))
        chip_style = ParagraphStyle(
            "Chip",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=warm_ink,
            alignment=TA_CENTER,
            leading=10,
        )
        chips = []
        chip_widths = []
        for c in concerns:
            label_txt = str(c["label"])
            # Cap chip width so long labels can still fit in the content area.
            width = max(24 * mm, min(content_width, 7 * mm + len(label_txt) * 1.55 * mm))
            chip = Table(
                [[Paragraph(_esc(label_txt), chip_style)]],
                colWidths=[width],
            )
            chip.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), warm_head),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            chips.append(chip)
            chip_widths.append(width)
        # Wrap chips onto multiple rows so they never overflow the page.
        gap = 3 * mm
        row_gap = 4 * mm
        rows_flow: list[list[Any]] = []
        rows_widths: list[list[float]] = []
        cur_cells: list[Any] = []
        cur_widths: list[float] = []
        used = 0.0
        for chip, width in zip(chips, chip_widths):
            needed = width if not cur_cells else width + gap
            if cur_cells and used + needed > content_width + 0.5:
                rows_flow.append(cur_cells)
                rows_widths.append(cur_widths)
                cur_cells, cur_widths, used = [], [], 0.0
                needed = width
            if cur_cells:
                cur_cells.append("")
                cur_widths.append(gap)
                used += gap
            cur_cells.append(chip)
            cur_widths.append(width)
            used += width
        if cur_cells:
            rows_flow.append(cur_cells)
            rows_widths.append(cur_widths)
        for i, (cells, widths) in enumerate(zip(rows_flow, rows_widths)):
            if i:
                story.append(Spacer(1, row_gap))
            story.append(Table([cells], colWidths=widths))
        story.append(Spacer(1, 8))
        for c in concerns:
            # Match card side padding so treatment text is not flush to the border.
            inner_width = content_width - 20
            half = inner_width / 2
            inner = Table(
                [[
                    [
                        Paragraph("MEANING", label),
                        Paragraph(_esc(c["meaning"]), body_navy),
                    ],
                    [
                        Paragraph("TREATMENT", label),
                        Paragraph(_esc(c["treatment"]), body_navy),
                    ],
                ]],
                colWidths=[half, half],
            )
            inner.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
                ("LEFTPADDING", (1, 0), (1, 0), 8),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("LINEAFTER", (0, 0), (0, 0), 0.5, HexColor("#f3d5c8")),
            ]))
            card = Table(
                [
                    [Paragraph(_esc(c["label"]), card_title)],
                    [inner],
                ],
                colWidths=[content_width],
            )
            card.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), warm_head),
                ("BACKGROUND", (0, 1), (0, 1), warm_bg),
                ("BOX", (0, 0), (-1, -1), 0.8, warm_line),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (0, 0), 7),
                ("BOTTOMPADDING", (0, 0), (0, 0), 7),
                ("TOPPADDING", (0, 1), (0, 1), 8),
                ("BOTTOMPADDING", (0, 1), (0, 1), 8),
            ]))
            story.append(KeepTogether([card, Spacer(1, 7)]))
    else:
        story.append(Paragraph(
            "Good news: no obvious visible concerns were detected in your uploaded photo(s).",
            body,
        ))
        story.append(Spacer(1, 6))
        story.append(Paragraph('<font color="#0a7a7a"><b>No visible concerns</b></font>', body))

    story.append(Spacer(1, 6))
    road_rows = []
    for idx, item in enumerate(_roadmap_items(findings), start=1):
        road_rows.append([
            Paragraph(f'<font color="#0a7a7a"><b>{idx}</b></font>', ParagraphStyle("Step", parent=body, alignment=TA_CENTER)),
            Paragraph(_esc(item), body_navy),
        ])
    road = Table(road_rows, colWidths=[10 * mm, content_width - 10 * mm])
    road.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, -1), HexColor("#d9f2f2")),
        ("LEFTPADDING", (0, 0), (0, -1), 4),
        ("RIGHTPADDING", (0, 0), (0, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (1, 0), (1, -1), 10),
        ("LINEBELOW", (1, 0), (1, -2), 0.4, line),
    ]))
    story.append(KeepTogether([
        SectionTitle("What to do next", content_width),
        Spacer(1, 8),
        road,
    ]))

    story.append(Spacer(1, 12))
    rule = Table([[""]], colWidths=[content_width], rowHeights=[1])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), line)]))
    story.append(rule)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "This is a preliminary AI assessment based on photographs only. It is not a clinical diagnosis. "
        "A dentist examination (and X-rays when needed) is required to confirm findings and plan treatment.",
        footer,
    ))

    doc.build(story)
    return buffer.getvalue()


def _brevo_send(
    *,
    to_email: str,
    subject: str,
    html_content: str,
    attachments: Optional[list[dict[str, str]]] = None,
    skip_label: str = "email",
) -> bool:
    api_key = (os.getenv("BREVO_API_KEY") or "").strip()
    from_email = (os.getenv("BREVO_FROM_EMAIL") or "").strip()
    if not api_key or not from_email:
        logger.warning("Brevo not configured; skipping %s.", skip_label)
        return False
    if not to_email:
        return False
    sender_name, sender_email = _parse_sender(from_email)
    if not sender_email:
        logger.warning("Brevo sender email is invalid; skipping %s.", skip_label)
        return False

    payload: dict[str, Any] = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content,
    }
    if attachments:
        payload["attachment"] = attachments

    try:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": api_key,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if res.status_code >= 400:
            logger.error("Brevo failed (%s): %s", res.status_code, res.text)
            return False
        return True
    except Exception:
        logger.exception("Brevo request failed")
        return False


def _format_booking_date(day: str) -> str:
    try:
        from datetime import date as date_cls

        d = date_cls.fromisoformat(str(day)[:10])
        return f"{d.strftime('%A')}, {d.day} {d.strftime('%B %Y')}"
    except Exception:
        return str(day)


def _format_booking_time(slot: str) -> str:
    text = str(slot or "").strip()[:5]
    try:
        hour, minute = text.split(":", 1)
        h = int(hour)
        m = int(minute)
        suffix = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d} {suffix}"
    except Exception:
        return text or str(slot)


def build_booking_email_html(
    *,
    name: str,
    email: str,
    phone: str,
    day: str,
    time_slot: str,
    note: str = "",
) -> str:
    greeting_name = (name or "").strip() or (email or "").split("@", 1)[0].replace(".", " ").strip()
    greeting = f"Hello {greeting_name}," if greeting_name else "Hello,"
    date_label = _format_booking_date(day)
    time_label = _format_booking_time(time_slot)
    note_block = ""
    if (note or "").strip():
        note_block = f"""
  <p style="margin:16px 0 0;padding:12px 14px;background:#f4f7fb;border:1px solid #d9e2ef;border-radius:6px;">
    <strong style="color:#183068;">Your note:</strong><br>
    {_esc(note.strip())}
  </p>
"""
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Georgia,serif;color:#1a2a4a;line-height:1.55;max-width:640px;margin:0 auto;padding:24px;">
  <p>{_esc(greeting)}</p>
  <p>
    Your appointment at The Global Dentist is confirmed. Here are the details:
  </p>
  <table style="width:100%;border-collapse:collapse;margin:18px 0;font-size:15px;">
    <tr>
      <td style="padding:10px 0;border-bottom:1px solid #d9e2ef;color:#5a6a80;width:120px;">Date</td>
      <td style="padding:10px 0;border-bottom:1px solid #d9e2ef;color:#183068;font-weight:bold;">{_esc(date_label)}</td>
    </tr>
    <tr>
      <td style="padding:10px 0;border-bottom:1px solid #d9e2ef;color:#5a6a80;">Time</td>
      <td style="padding:10px 0;border-bottom:1px solid #d9e2ef;color:#183068;font-weight:bold;">{_esc(time_label)}</td>
    </tr>
    <tr>
      <td style="padding:10px 0;border-bottom:1px solid #d9e2ef;color:#5a6a80;">Name</td>
      <td style="padding:10px 0;border-bottom:1px solid #d9e2ef;color:#183068;">{_esc(name or "-")}</td>
    </tr>
    <tr>
      <td style="padding:10px 0;border-bottom:1px solid #d9e2ef;color:#5a6a80;">Email</td>
      <td style="padding:10px 0;border-bottom:1px solid #d9e2ef;color:#183068;">{_esc(email or "-")}</td>
    </tr>
    <tr>
      <td style="padding:10px 0;color:#5a6a80;">Phone</td>
      <td style="padding:10px 0;color:#183068;">{_esc(phone or "-")}</td>
    </tr>
  </table>
  {note_block}
  <p>
    If you need to change or cancel this appointment, please contact the clinic.
  </p>
  <p style="font-size:13px;color:#5a6a80;">The Global Dentist</p>
</body>
</html>
""".strip()


def send_booking_email(
    *,
    to_email: str,
    name: str,
    phone: str,
    day: str,
    time_slot: str,
    note: str = "",
) -> bool:
    html_content = build_booking_email_html(
        name=name,
        email=to_email,
        phone=phone,
        day=day,
        time_slot=time_slot,
        note=note,
    )
    return _brevo_send(
        to_email=to_email,
        subject="Your appointment is confirmed - The Global Dentist",
        html_content=html_content,
        skip_label="booking email",
    )


def send_assessment_email(
    *,
    to_email: str,
    overall_score: Optional[int],
    findings: Any,
    report_text: str,
    category_scores: Any = None,
    images: Optional[list[tuple[str, bytes]]] = None,
) -> bool:
    # report_text kept for API compatibility / future use; PDF mirrors dashboard findings UI.
    _ = report_text

    email_html = build_email_html(to_email=to_email)
    try:
        pdf_bytes = build_report_pdf_bytes(
            overall_score=overall_score,
            category_scores=category_scores,
            findings=findings,
            images=images,
        )
    except Exception:
        logger.exception("Assessment PDF generation failed")
        return False

    attachment_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return _brevo_send(
        to_email=to_email,
        subject="Your Virtual Smile Assessment - The Global Dentist",
        html_content=email_html,
        attachments=[
            {
                "content": attachment_b64,
                "name": "virtual-smile-assessment-report.pdf",
            }
        ],
        skip_label="assessment email",
    )
