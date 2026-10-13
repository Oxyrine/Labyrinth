"""Deterministic ReportLab invoice PDFs in 3 layouts (tabular_gst, letterhead, tally).

The PDFs carry only what a printed invoice carries: parties, GSTINs, dates, references, line items, tax
split and totals. There is no machine-readable answer key and no internal IDs, so extraction has to read
the visible text.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Literal

import reportlab.rl_config

reportlab.rl_config.invariant = 1

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from contract.models import DocType, Invoice, LineItem, Party

LayoutKind = Literal["tabular_gst", "letterhead", "tally"]
LAYOUTS: tuple[LayoutKind, ...] = ("tabular_gst", "letterhead", "tally")

_DOC_TITLES = {DocType.INVOICE: "TAX INVOICE", DocType.CREDIT_NOTE: "CREDIT NOTE", DocType.DEBIT_NOTE: "DEBIT NOTE"}


def rs(paise: int) -> str:
    """ASCII rupee string with Indian digit grouping and two decimals: `Rs. 9,60,000.00`."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(int(paise)), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{sign}Rs. {s}.{frac:02d}"


def gst_pct(item: LineItem) -> str:
    bps = item.cgst_bps + item.sgst_bps + item.igst_bps
    return f"{bps / 100:g}%"


def _title(invoice: Invoice) -> str:
    return _DOC_TITLES.get(invoice.doc_type, "TAX INVOICE")


def _supply(invoice: Invoice) -> str:
    return "SEZ/EXPORT" if invoice.is_export_or_sez else "REGULAR"


def _fit(c: canvas.Canvas, text: str, font: str, size: float, max_w: float, x: float, y: float) -> None:
    """Draw text on one line, shrinking the font (down to 5pt) so the full text always fits."""
    while size > 5 and stringWidth(text, font, size) > max_w:
        size -= 0.25
    c.setFont(font, size)
    c.drawString(x, y, text)


def _extra_header_lines(invoice: Invoice) -> list[str]:
    lines = [f"Supply Type: {_supply(invoice)}"]
    if invoice.linked_invoice_id:
        lines.append(f"Against Invoice: {invoice.linked_invoice_id}")
    return lines


def _draw_remarks(c: canvas.Canvas, remarks: str | None) -> None:
    if remarks:
        c.setFont("Helvetica", 7)
        c.drawString(36, 150, f"REMARKS: {remarks}")


def _draw_tabular_gst(c: canvas.Canvas, invoice: Invoice, seller: Party, buyer: Party) -> None:
    width, height = A4
    c.setStrokeColor(colors.HexColor("#1e293b"))
    c.setLineWidth(1.0)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(30, height - 75, width - 60, 35, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(42, height - 62, f"{_title(invoice)} (GOODS & SERVICES TAX)")

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(42, height - 95, f"Supplier: {seller.name}")
    c.setFont("Helvetica", 9)
    c.drawString(42, height - 110, f"GSTIN: {invoice.seller_gstin} | State: {invoice.seller_gstin[:2]}")
    c.drawString(42, height - 123, f"Address: {seller.address}, {seller.city} - {seller.pincode}"[:78])

    c.setFont("Helvetica-Bold", 10)
    c.drawString(330, height - 95, f"Invoice No: {invoice.invoice_no}")
    c.setFont("Helvetica", 9)
    c.drawString(330, height - 110, f"Invoice Date: {invoice.invoice_date} | Due Date: {invoice.due_date}")
    c.drawString(330, height - 123, f"PO Ref: {invoice.po_id or 'N/A'} | Contract: {invoice.contract_id or 'N/A'}")
    for i, extra in enumerate(_extra_header_lines(invoice)):
        c.drawString(330, height - 136 - 13 * i, extra)

    c.line(30, height - 160, width - 30, height - 160)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(42, height - 177, f"Recipient (Bill To): {buyer.name}")
    c.setFont("Helvetica", 9)
    c.drawString(
        42, height - 192,
        f"Buyer GSTIN: {invoice.buyer_gstin} | Place of Supply: {invoice.place_of_supply_state} | Terms: {invoice.payment_terms_days} days",
    )
    c.drawString(42, height - 205, f"Address: {buyer.address}, {buyer.city} - {buyer.pincode}"[:78])

    c.line(30, height - 217, width - 30, height - 217)
    y = height - 232
    c.setFont("Helvetica-Bold", 8)
    for x, label in ((34, "#"), (48, "Description"), (238, "HSN/SAC"), (290, "Qty"), (332, "Rate"), (392, "Taxable"), (452, "GST%"), (484, "Tax"), (528, "Total")):
        c.drawString(x, y, label)
    c.line(30, y - 6, width - 30, y - 6)
    y -= 20
    for item in invoice.line_items:
        c.setFont("Helvetica", 7.5)
        c.drawString(34, y, str(item.line_no))
        _fit(c, item.description, "Helvetica", 7.5, 184, 48, y)
        c.setFont("Helvetica", 7.5)
        c.drawString(238, y, item.hsn_sac)
        c.drawString(290, y, f"{item.qty} {item.unit}")
        c.drawString(332, y, rs(item.rate_paise))
        c.drawString(392, y, rs(item.taxable_paise))
        c.drawString(452, y, gst_pct(item))
        c.drawString(484, y, rs(item.tax_paise))
        c.drawString(528, y, rs(item.total_paise))
        y -= 16

    y = min(y - 10, 230)
    c.line(30, y + 6, width - 30, y + 6)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(42, y - 10, f"Subtotal: {rs(invoice.subtotal_paise)}")
    c.drawString(42, y - 24, f"CGST: {rs(invoice.cgst_paise)} | SGST: {rs(invoice.sgst_paise)} | IGST: {rs(invoice.igst_paise)}")
    c.drawString(42, y - 38, f"Total Tax: {rs(invoice.total_tax_paise)}")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(42, y - 54, f"Grand Total: {rs(invoice.grand_total_paise)}")


def _draw_letterhead(c: canvas.Canvas, invoice: Invoice, seller: Party, buyer: Party) -> None:
    width, height = A4
    c.setFillColor(colors.HexColor("#1e3a8a"))
    c.rect(0, height - 65, width, 65, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 35, seller.name.upper())
    _fit(c, f"{seller.address}, {seller.city} - {seller.pincode} | GSTIN: {invoice.seller_gstin}", "Helvetica", 8, width - 80, 40, height - 52)
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(width - 40, height - 35, f"COMMERCIAL {_title(invoice)}")

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, height - 95, "BILLED TO:")
    c.setFont("Helvetica", 9)
    c.drawString(40, height - 110, buyer.name)
    c.drawString(40, height - 124, f"Buyer GSTIN: {invoice.buyer_gstin}")
    c.drawString(40, height - 138, f"{buyer.address}, {buyer.city} - {buyer.pincode}"[:70])
    c.drawString(40, height - 152, f"Place of Supply: {invoice.place_of_supply_state}")

    c.setFont("Helvetica-Bold", 10)
    c.drawString(330, height - 95, "INVOICE METADATA:")
    c.setFont("Helvetica", 9)
    c.drawString(330, height - 110, f"Invoice Number: {invoice.invoice_no}")
    c.drawString(330, height - 124, f"Date: {invoice.invoice_date} | Due: {invoice.due_date}")
    c.drawString(330, height - 138, f"FY: {invoice.financial_year} | Terms: {invoice.payment_terms_days}d")
    c.drawString(330, height - 152, f"PO: {invoice.po_id or '-'} | Contract: {invoice.contract_id or '-'}")
    for i, extra in enumerate(_extra_header_lines(invoice)):
        c.drawString(330, height - 166 - 13 * i, extra)

    y = height - 195
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.line(40, y, width - 40, y)
    y -= 16
    c.setFont("Helvetica-Bold", 8.5)
    for x, label in ((40, "Item & HSN/SAC"), (270, "Qty"), (320, "Unit Rate"), (395, "Taxable"), (465, "GST%"), (500, "Line Total")):
        c.drawString(x, y, label)
    y -= 8
    c.line(40, y, width - 40, y)
    y -= 16
    for item in invoice.line_items:
        _fit(c, f"{item.line_no}. {item.description} [{item.hsn_sac}]", "Helvetica", 8, 226, 40, y)
        c.setFont("Helvetica", 8)
        c.drawString(270, y, f"{item.qty} {item.unit}")
        c.drawString(320, y, rs(item.rate_paise))
        c.drawString(395, y, rs(item.taxable_paise))
        c.drawString(465, y, gst_pct(item))
        c.drawString(500, y, rs(item.total_paise))
        y -= 16

    y = min(y - 10, 235)
    c.line(40, y + 6, width - 40, y + 6)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(40, y - 10, f"Taxable Subtotal: {rs(invoice.subtotal_paise)} | Total GST: {rs(invoice.total_tax_paise)}")
    c.drawString(40, y - 24, f"CGST: {rs(invoice.cgst_paise)} | SGST: {rs(invoice.sgst_paise)} | IGST: {rs(invoice.igst_paise)}")
    c.drawString(40, y - 40, f"Net Payable: {rs(invoice.grand_total_paise)}")


def _draw_tally(c: canvas.Canvas, invoice: Invoice, seller: Party, buyer: Party) -> None:
    width, height = A4
    c.setFont("Courier-Bold", 12)
    c.drawCentredString(width / 2.0, height - 45, f"{_title(invoice)} (TALLY ERP VOUCHER)")
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    c.line(32, height - 125, width - 32, height - 125)

    c.setFont("Courier-Bold", 9)
    c.drawString(40, height - 80, seller.name[:44])
    c.setFont("Courier", 8)
    c.drawString(40, height - 93, f"GSTIN/UIN : {invoice.seller_gstin}")
    c.drawString(40, height - 105, f"Address   : {seller.city} - {seller.pincode}")

    c.setFont("Courier-Bold", 9)
    c.drawString(308, height - 80, f"Voucher No: {invoice.invoice_no}")
    c.setFont("Courier", 8)
    c.drawString(308, height - 93, f"Dated     : {invoice.invoice_date} (FY {invoice.financial_year})")
    c.drawString(308, height - 105, f"Due Date  : {invoice.due_date} ({invoice.payment_terms_days} Days)")
    c.drawString(308, height - 117, f"Buyer Ord : {invoice.po_id or 'NONE'} / {invoice.contract_id or 'NONE'}")

    c.setFont("Courier-Bold", 9)
    c.drawString(40, height - 140, f"Buyer (Bill to): {buyer.name[:40]}")
    c.setFont("Courier", 8)
    c.drawString(40, height - 153, f"GSTIN/UIN : {invoice.buyer_gstin}")
    c.drawString(40, height - 165, f"POS: {invoice.place_of_supply_state} | Supply: {_supply(invoice)}")
    if invoice.linked_invoice_id:
        c.drawString(40, height - 177, f"Against Invoice: {invoice.linked_invoice_id}")

    y = height - 202
    c.setFont("Courier-Bold", 7)
    c.drawString(34, y, "Sl Particulars                          HSN/SAC  Qty UOM  Rate        Taxable      GST%  Amount")
    c.line(32, y - 5, width - 32, y - 5)
    y -= 18
    for item in invoice.line_items:
        row = (
            f"{item.line_no:<2} {item.description}  {item.hsn_sac}  {item.qty} {item.unit}  "
            f"{rs(item.rate_paise)}  {rs(item.taxable_paise)}  {gst_pct(item)}  {rs(item.total_paise)}"
        )
        _fit(c, row, "Courier", 7, width - 68, 34, y)
        y -= 14

    y = min(y - 10, 230)
    c.line(32, y + 6, width - 32, y + 6)
    c.setFont("Courier-Bold", 8)
    c.drawString(40, y - 10, f"Taxable: {rs(invoice.subtotal_paise)}")
    c.drawString(40, y - 24, f"CGST: {rs(invoice.cgst_paise)} | SGST: {rs(invoice.sgst_paise)} | IGST: {rs(invoice.igst_paise)}")
    c.drawString(40, y - 40, f"TOTAL INVOICE VALUE: {rs(invoice.grand_total_paise)}")


def render_invoice_pdf_bytes(
    invoice: Invoice,
    seller: Party,
    buyer: Party,
    layout: LayoutKind = "tabular_gst",
    *,
    remarks: str | None = None,
) -> bytes:
    """Render an Invoice to deterministic PDF bytes using ReportLab invariant mode."""
    reportlab.rl_config.invariant = 1
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle(f"{_title(invoice)} {invoice.invoice_no}")
    c.setAuthor(seller.name)
    c.setCreator("Labyrinth ReportLab Invariant v1")
    c.setProducer("Labyrinth ReportLab Invariant v1")

    if layout == "tabular_gst":
        _draw_tabular_gst(c, invoice, seller, buyer)
    elif layout == "letterhead":
        _draw_letterhead(c, invoice, seller, buyer)
    elif layout == "tally":
        _draw_tally(c, invoice, seller, buyer)
    else:
        raise ValueError(f"Unsupported PDF layout: {layout!r}")

    _draw_remarks(c, remarks)
    c.showPage()
    c.save()
    return buf.getvalue()


def write_invoice_pdf(
    out_path: Path,
    invoice: Invoice,
    seller: Party,
    buyer: Party,
    layout: LayoutKind = "tabular_gst",
    *,
    remarks: str | None = None,
) -> str:
    """Render an Invoice PDF to out_path and return its hex SHA-256 digest."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_bytes = render_invoice_pdf_bytes(invoice, seller, buyer, layout=layout, remarks=remarks)
    out_path.write_bytes(pdf_bytes)
    return hashlib.sha256(pdf_bytes).hexdigest()


def invoice_text_lines(invoice: Invoice, seller: Party, buyer: Party) -> list[str]:
    """The visible lines of the tabular layout as plain text (used to draw scanned copies)."""
    lines = [
        f"{_title(invoice)} (GOODS & SERVICES TAX)",
        f"Supplier: {seller.name}",
        f"GSTIN: {invoice.seller_gstin} | State: {invoice.seller_gstin[:2]}",
        f"Invoice No: {invoice.invoice_no}",
        f"Invoice Date: {invoice.invoice_date} | Due Date: {invoice.due_date}",
        f"PO Ref: {invoice.po_id or 'N/A'} | Contract: {invoice.contract_id or 'N/A'}",
        *_extra_header_lines(invoice),
        f"Recipient (Bill To): {buyer.name}",
        f"Buyer GSTIN: {invoice.buyer_gstin} | Place of Supply: {invoice.place_of_supply_state} | Terms: {invoice.payment_terms_days} days",
        "# | Description | HSN/SAC | Qty | Rate | Taxable | GST% | Tax | Total",
    ]
    for it in invoice.line_items:
        lines.append(
            f"{it.line_no} | {it.description} | {it.hsn_sac} | {it.qty} {it.unit} | {rs(it.rate_paise)} | "
            f"{rs(it.taxable_paise)} | {gst_pct(it)} | {rs(it.tax_paise)} | {rs(it.total_paise)}"
        )
    lines += [
        f"Subtotal: {rs(invoice.subtotal_paise)}",
        f"CGST: {rs(invoice.cgst_paise)} | SGST: {rs(invoice.sgst_paise)} | IGST: {rs(invoice.igst_paise)}",
        f"Total Tax: {rs(invoice.total_tax_paise)}",
        f"Grand Total: {rs(invoice.grand_total_paise)}",
    ]
    return lines
