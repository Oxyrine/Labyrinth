"""Parse the *visible* text of a GST invoice (tabular_gst, letterhead, tally, or OCR of a scan).

Rules: nothing is invented. A field that is not found is reported as missing; the caller decides
(`ExtractionError` -> manual entry / review). Derived values (financial year, CGST/SGST/IGST split per
line, internal ids) are labelled as derived with lower confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from contract.models import DocType, FieldProvenance, Invoice, LineItem
from extract.layouts.common import parse_money_to_paise

GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
DATE_RE = r"(\d{4}-\d{2}-\d{2})"
MONEY = r"Rs\.?\s*-?[\d,]+\.\d{2}"
MONEY_RE = re.compile(MONEY)


class ExtractionError(Exception):
    """The document could not be read well enough to build an invoice."""

    def __init__(self, message: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = list(missing or [])


@dataclass
class Parsed:
    values: dict[str, Any] = field(default_factory=dict)
    lines: list[dict[str, Any]] = field(default_factory=list)
    derived: set[str] = field(default_factory=set)
    pages: dict[str, int] = field(default_factory=dict)


def _money(text: str) -> int:
    return parse_money_to_paise(text)


def _find(pattern: str, lines: list[str], group: int = 1, flags: int = 0) -> str | None:
    rx = re.compile(pattern, flags)
    for ln in lines:
        m = rx.search(ln)
        if m:
            return m.group(group).strip()
    return None


def _doc_type(lines: list[str]) -> DocType:
    head = " ".join(lines[:6]).upper()
    if "CREDIT NOTE" in head:
        return DocType.CREDIT_NOTE
    if "DEBIT NOTE" in head:
        return DocType.DEBIT_NOTE
    return DocType.INVOICE


def _financial_year(invoice_date: str) -> str:
    y, m, _ = (int(x) for x in invoice_date.split("-"))
    start = y if m >= 4 else y - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _row_from_cells(n: str, desc: str, hsn: str, qty: str, unit: str, rate: str, taxable: str, gst: str | None, tax: str | None, total: str) -> dict[str, Any]:
    return {
        "line_no": int(n), "description": desc.strip(), "hsn_sac": hsn, "qty": int(qty), "unit": unit,
        "rate_paise": _money(rate), "taxable_paise": _money(taxable),
        "gst_bps": int(round(float(gst.rstrip("%")) * 100)) if gst else None,
        "tax_paise": _money(tax) if tax else None, "total_paise": _money(total),
    }


# ---------------------------------------------------------------------------------------- tabular_gst
_PIPE_ROW = re.compile(
    rf"^\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(\d{{4,8}})\s*\|\s*(\d+)\s+(\S+)\s*\|\s*({MONEY})\s*\|\s*({MONEY})\s*\|\s*([\d.]+%)\s*\|\s*({MONEY})\s*\|\s*({MONEY})\s*$"
)


def _tabular_rows(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ln in lines:  # OCR text keeps the row on one line
        m = _PIPE_ROW.match(ln)
        if m:
            n, desc, hsn, qty, unit, rate, taxable, gst, tax, total = m.groups()
            rows.append(_row_from_cells(n, desc, hsn, qty, unit, rate, taxable, gst, tax, total))
    if rows:
        return rows
    # Text layer: one cell per line after the header (#, Description, HSN/SAC, Qty, Rate, Taxable, GST%, Tax, Total).
    try:
        start = next(i for i, ln in enumerate(lines) if ln == "GST%") + 3
    except StopIteration:
        return []
    i = start
    while i < len(lines) and not lines[i].startswith("Subtotal:"):
        if not lines[i].isdigit():
            i += 1
            continue
        n = lines[i]
        i += 1
        desc: list[str] = []
        while i < len(lines) and not re.fullmatch(r"\d{4,8}", lines[i]):
            desc.append(lines[i])
            i += 1
        if i + 7 >= len(lines):
            break
        hsn = lines[i]
        qm = re.fullmatch(r"(\d+)\s+(\S+)", lines[i + 1])
        cells = lines[i + 2 : i + 7]
        if not qm or len(cells) < 5 or not all(MONEY_RE.fullmatch(c) for c in (cells[0], cells[1], cells[3], cells[4])):
            i += 1
            continue
        rows.append(_row_from_cells(n, " ".join(desc), hsn, qm.group(1), qm.group(2), cells[0], cells[1], cells[2], cells[3], cells[4]))
        i += 7
    return rows


def _parse_tabular(lines: list[str]) -> Parsed:
    p = Parsed()
    v = p.values
    v["invoice_no"] = _find(r"^Invoice No:\s*(.+)$", lines)
    m = re.search(rf"Invoice Date:\s*{DATE_RE}\s*\|\s*Due Date:\s*{DATE_RE}", "\n".join(lines))
    if m:
        v["invoice_date"], v["due_date"] = m.groups()
    ref = re.search(r"PO Ref:\s*(.+?)\s*\|\s*Contract:\s*(.+)$", "\n".join(lines), re.M)
    if ref:
        v["po_ref"], v["contract_ref"] = ref.group(1).strip(), ref.group(2).strip()
    seller = _find(r"^GSTIN:\s*(\S+)", lines)
    buyer = re.search(r"Buyer GSTIN:\s*(\S+)\s*\|\s*Place of Supply:\s*(\d+)\s*\|\s*Terms:\s*(\d+)\s*days", "\n".join(lines))
    v["seller_gstin"] = seller
    if buyer:
        v["buyer_gstin"], v["place_of_supply_state"], v["payment_terms_days"] = buyer.group(1), buyer.group(2), int(buyer.group(3))
    v["supply_type"] = _find(r"^Supply Type:\s*(.+)$", lines)
    v["linked_invoice_no"] = _find(r"^Against Invoice:\s*(.+)$", lines)
    v["seller_name"] = _find(r"^Supplier:\s*(.+)$", lines)
    v["buyer_name"] = _find(r"^Recipient \(Bill To\):\s*(.+)$", lines)
    p.lines = _tabular_rows(lines)
    _totals(v, lines, subtotal=r"^Subtotal:\s*({m})", grand=r"^Grand Total:\s*({m})", tax=r"^Total Tax:\s*({m})")
    return p


# ---------------------------------------------------------------------------------------- letterhead
_LH_TITLE = re.compile(r"^(\d+)\.\s+(.+)\s+\[(\d{4,8})\]$")
_LH_ONE_LINE = re.compile(
    rf"^(\d+)\.\s+(.+)\s+\[(\d{{4,8}})\]\s+(\d+)\s+(\S+)\s+({MONEY})\s+({MONEY})\s+([\d.]+%)\s+({MONEY})$"
)


def _parse_letterhead(lines: list[str]) -> Parsed:
    p = Parsed()
    v = p.values
    text = "\n".join(lines)
    v["seller_name"] = lines[0].title() if lines else None
    v["seller_gstin"] = _find(r"\|\s*GSTIN:\s*(\S+)", lines)
    v["invoice_no"] = _find(r"^Invoice Number:\s*(.+)$", lines)
    m = re.search(rf"^Date:\s*{DATE_RE}\s*\|\s*Due:\s*{DATE_RE}", text, re.M)
    if m:
        v["invoice_date"], v["due_date"] = m.groups()
    fy = re.search(r"^FY:\s*(\d{4}-\d{2})\s*\|\s*Terms:\s*(\d+)d", text, re.M)
    if fy:
        v["financial_year"], v["payment_terms_days"] = fy.group(1), int(fy.group(2))
    ref = re.search(r"^PO:\s*(.+?)\s*\|\s*Contract:\s*(.+)$", text, re.M)
    if ref:
        v["po_ref"], v["contract_ref"] = ref.group(1).strip(), ref.group(2).strip()
    v["buyer_gstin"] = _find(r"^Buyer GSTIN:\s*(\S+)", lines)
    v["place_of_supply_state"] = _find(r"^Place of Supply:\s*(\d+)", lines)
    v["supply_type"] = _find(r"^Supply Type:\s*(.+)$", lines)
    v["linked_invoice_no"] = _find(r"^Against Invoice:\s*(.+)$", lines)
    if "BILLED TO:" in lines:
        idx = lines.index("BILLED TO:")
        v["buyer_name"] = lines[idx + 1] if idx + 1 < len(lines) else None
    rows: list[dict[str, Any]] = []
    for ln in lines:
        m1 = _LH_ONE_LINE.match(ln)
        if m1:
            n, desc, hsn, qty, unit, rate, taxable, gst, total = m1.groups()
            rows.append(_row_from_cells(n, desc, hsn, qty, unit, rate, taxable, gst, None, total))
    if not rows:
        i = 0
        while i < len(lines):
            mt = _LH_TITLE.match(lines[i])
            if mt and i + 5 < len(lines):
                qm = re.fullmatch(r"(\d+)\s+(\S+)", lines[i + 1])
                cells = lines[i + 2 : i + 6]
                if qm and len(cells) == 4 and all(MONEY_RE.fullmatch(c) for c in (cells[0], cells[1], cells[3])):
                    rows.append(_row_from_cells(mt.group(1), mt.group(2), mt.group(3), qm.group(1), qm.group(2), cells[0], cells[1], cells[2], None, cells[3]))
                    i += 6
                    continue
            i += 1
    p.lines = rows
    _totals(v, lines, subtotal=r"^Taxable Subtotal:\s*({m})", grand=r"^Net Payable:\s*({m})", tax=r"Total GST:\s*({m})")
    return p


# ---------------------------------------------------------------------------------------- tally
_TALLY_ROW = re.compile(
    rf"^(\d+)\s+(.+?)\s+(\d{{4,8}})\s+(\d+)\s+([A-Za-z]+)\s+({MONEY})\s+({MONEY})\s+([\d.]+%)\s+({MONEY})$"
)


def _parse_tally(lines: list[str]) -> Parsed:
    p = Parsed()
    v = p.values
    text = "\n".join(lines)
    v["invoice_no"] = _find(r"^Voucher No:\s*(.+)$", lines)
    m = re.search(rf"^Dated\s*:\s*{DATE_RE}\s*\(FY\s*(\d{{4}}-\d{{2}})\)", text, re.M)
    if m:
        v["invoice_date"], v["financial_year"] = m.groups()
    d = re.search(rf"^Due Date\s*:\s*{DATE_RE}\s*\((\d+)\s*Days\)", text, re.M)
    if d:
        v["due_date"], v["payment_terms_days"] = d.group(1), int(d.group(2))
    o = re.search(r"^Buyer Ord\s*:\s*(.+?)\s*/\s*(.+)$", text, re.M)
    if o:
        v["po_ref"], v["contract_ref"] = o.group(1).strip(), o.group(2).strip()
    gst = [m_.group(1) for m_ in re.finditer(r"GSTIN/UIN\s*:\s*(\S+)", text)]
    if gst:
        v["seller_gstin"] = gst[0]
    if len(gst) > 1:
        v["buyer_gstin"] = gst[1]
    pos = re.search(r"POS:\s*(\d+)\s*\|\s*Supply:\s*(\S+)", text)
    if pos:
        v["place_of_supply_state"], v["supply_type"] = pos.groups()
    v["linked_invoice_no"] = _find(r"^Against Invoice:\s*(.+)$", lines)
    v["buyer_name"] = _find(r"^Buyer \(Bill to\):\s*(.+)$", lines)
    for ln in lines:
        mr = _TALLY_ROW.match(ln)
        if mr:
            n, desc, hsn, qty, unit, rate, taxable, gstp, total = mr.groups()
            p.lines.append(_row_from_cells(n, desc, hsn, qty, unit, rate, taxable, gstp, None, total))
    _totals(v, lines, subtotal=r"^Taxable:\s*({m})", grand=r"^TOTAL INVOICE VALUE:\s*({m})", tax=None)
    return p


# ---------------------------------------------------------------------------------------- shared
def _totals(v: dict[str, Any], lines: list[str], *, subtotal: str, grand: str, tax: str | None) -> None:
    m = MONEY
    s = _find(subtotal.format(m=m), lines)
    g = _find(grand.format(m=m), lines)
    t = _find(tax.format(m=m), lines) if tax else None
    split = re.search(rf"CGST:\s*({m})\s*\|\s*SGST:\s*({m})\s*\|\s*IGST:\s*({m})", "\n".join(lines))
    if s:
        v["subtotal_paise"] = _money(s)
    if g:
        v["grand_total_paise"] = _money(g)
    if t:
        v["total_tax_paise"] = _money(t)
    if split:
        v["cgst_paise"], v["sgst_paise"], v["igst_paise"] = (_money(x) for x in split.groups())


def parse_visible(pages_text: list[str], layout: str, *, ocr: bool = False) -> tuple[Invoice, dict[str, FieldProvenance], dict[str, Any]]:
    """Parse sanitized page text. Returns `(Invoice, provenance, extras)`; raises `ExtractionError` when incomplete.

    `extras` carries what a printed invoice shows but the `Invoice` model does not hold (party names, PO and
    contract reference strings, the linked invoice number) so the platform can resolve parties and documents.
    """
    pages_lines = [[ln.strip() for ln in page.splitlines() if ln.strip()] for page in pages_text]
    lines = [ln for pl in pages_lines for ln in pl]
    parser = {"tabular_gst": _parse_tabular, "letterhead": _parse_letterhead, "tally": _parse_tally}.get(layout)
    if parser is None:
        raise ExtractionError(f"Unrecognised invoice layout '{layout}'.", ["layout"])
    parsed = parser(lines)
    v = parsed.values

    # Normalise OCR-style text (tabular layout drawn as pipe rows) and fill what is derivable.
    if "financial_year" not in v and v.get("invoice_date"):
        v["financial_year"] = _financial_year(v["invoice_date"])
        parsed.derived.add("financial_year")
    if not v.get("seller_gstin") and lines:
        found = GSTIN_RE.findall("\n".join(lines))
        if found:
            v["seller_gstin"] = found[0]

    required = ["invoice_no", "invoice_date", "due_date", "seller_gstin", "buyer_gstin", "place_of_supply_state",
                "payment_terms_days", "subtotal_paise", "grand_total_paise"]
    missing = [k for k in required if v.get(k) in (None, "")]
    if not parsed.lines:
        missing.append("line_items")
    if missing:
        raise ExtractionError("Could not read: " + ", ".join(missing), missing)

    # Tax split: printed on every layout; if only a total is printed, split by supply type (derived).
    cg, sg, ig = v.get("cgst_paise"), v.get("sgst_paise"), v.get("igst_paise")
    line_tax_total = sum((r["tax_paise"] if r["tax_paise"] is not None else r["total_paise"] - r["taxable_paise"]) for r in parsed.lines)
    if cg is None or sg is None or ig is None:
        total_tax = v.get("total_tax_paise", line_tax_total)
        if v["seller_gstin"][:2] == v["place_of_supply_state"]:
            cg, sg, ig = total_tax // 2, total_tax - total_tax // 2, 0
        else:
            cg, sg, ig = 0, 0, total_tax
        parsed.derived.add("tax_split")
    total_tax_paise = v.get("total_tax_paise", cg + sg + ig)

    items: list[LineItem] = []
    for r in parsed.lines:
        tax_paise = r["tax_paise"] if r["tax_paise"] is not None else r["total_paise"] - r["taxable_paise"]
        bps = r["gst_bps"] if r["gst_bps"] is not None else (round(tax_paise * 10000 / r["taxable_paise"]) if r["taxable_paise"] else 0)
        inter = ig > 0 and cg == 0 and sg == 0
        items.append(LineItem(
            line_no=r["line_no"], description=r["description"], hsn_sac=r["hsn_sac"],
            is_service=r["hsn_sac"].startswith("99") and len(r["hsn_sac"]) == 6,
            qty=r["qty"], unit=r["unit"], rate_paise=r["rate_paise"], taxable_paise=r["taxable_paise"],
            cgst_bps=0 if inter else bps // 2, sgst_bps=0 if inter else bps - bps // 2, igst_bps=bps if inter else 0,
            tax_paise=tax_paise, total_paise=r["total_paise"],
        ))

    def ref(value: str | None) -> str | None:
        return None if value is None or value.strip().upper() in ("N/A", "NONE", "-", "") else value.strip()

    supply = (v.get("supply_type") or "REGULAR").upper()
    invoice = Invoice(
        invoice_id="INV-PENDING",  # replaced by the pipeline (platform-assigned)
        invoice_no=v["invoice_no"], financial_year=v["financial_year"], doc_type=_doc_type(lines),
        invoice_date=v["invoice_date"], due_date=v["due_date"],
        seller_id="P-PENDING", seller_gstin=v["seller_gstin"], buyer_id="P-PENDING", buyer_gstin=v["buyer_gstin"],
        place_of_supply_state=v["place_of_supply_state"], is_export_or_sez=supply.startswith("SEZ") or "EXPORT" in supply,
        po_id=ref(v.get("po_ref")), contract_id=ref(v.get("contract_ref")), payment_terms_days=v["payment_terms_days"],
        line_items=items, subtotal_paise=v["subtotal_paise"], cgst_paise=cg, sgst_paise=sg, igst_paise=ig,
        total_tax_paise=total_tax_paise, grand_total_paise=v["grand_total_paise"], linked_invoice_id=None,
    )

    text_conf = 0.85 if ocr else 0.99
    derived_conf = 0.75 if ocr else 0.85

    def page_of(needle: str) -> int:
        for idx, pl in enumerate(pages_lines, start=1):
            if any(needle in ln for ln in pl):
                return idx
        return 1

    prov: dict[str, FieldProvenance] = {}
    for key, value in (
        ("invoice_no", invoice.invoice_no), ("invoice_date", invoice.invoice_date), ("due_date", invoice.due_date),
        ("financial_year", invoice.financial_year), ("seller_gstin", invoice.seller_gstin), ("buyer_gstin", invoice.buyer_gstin),
        ("place_of_supply_state", invoice.place_of_supply_state), ("payment_terms_days", invoice.payment_terms_days),
        ("subtotal_paise", invoice.subtotal_paise), ("total_tax_paise", invoice.total_tax_paise),
        ("grand_total_paise", invoice.grand_total_paise),
    ):
        conf = derived_conf if key in parsed.derived else text_conf
        prov[key] = FieldProvenance(value=value, confidence=conf, page=page_of(str(value)[:12]))
    for key in ("cgst_paise", "sgst_paise", "igst_paise"):
        prov[key] = FieldProvenance(value=getattr(invoice, key), confidence=derived_conf if "tax_split" in parsed.derived else text_conf, page=1)

    prov["line_items"] = FieldProvenance(value=len(items), confidence=text_conf, page=1)
    extras = {
        "seller_name": v.get("seller_name"), "buyer_name": v.get("buyer_name"),
        "po_ref": ref(v.get("po_ref")), "contract_ref": ref(v.get("contract_ref")), "linked_invoice_no": v.get("linked_invoice_no"),
    }
    return invoice, prov, extras
