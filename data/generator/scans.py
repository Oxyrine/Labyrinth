"""Deterministic noisy flatbed-scan generator: the invoice is drawn into a skewed, speckled raster image.

The resulting PDF holds the image only (no text layer), so reading it requires OCR.
"""

from __future__ import annotations

import hashlib
import io
import random
from pathlib import Path

import reportlab.rl_config

reportlab.rl_config.invariant = 1

import reportlab
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from contract.models import Invoice, Party
from data.generator.pdfs import invoice_text_lines

_FONT = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
_W, _H = 1654, 2339  # A4 at 200 dpi


def render_noisy_scan_png_bytes(invoice: Invoice, seller: Party, buyer: Party, seed: int) -> bytes:
    """Render a deterministic noisy, slightly rotated scan PNG of the invoice's visible text."""
    rng = random.Random(f"tessera-scan:{seed}:{invoice.invoice_no}:{invoice.invoice_date}")
    tint = rng.randint(238, 246)
    img = Image.new("RGB", (_W, _H), color=(tint, tint - 2, tint - 6))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(_FONT), 22)
    y = 120
    for line in invoice_text_lines(invoice, seller, buyer):
        draw.text((90 + rng.randint(-1, 1), y), line, fill=(25, 25, 30), font=font)
        y += 40

    pixels = img.load()
    assert pixels is not None
    for _ in range(9000):
        px, py = rng.randint(4, _W - 5), rng.randint(4, _H - 5)
        shade = rng.randint(120, 200)
        pixels[px, py] = (shade, shade, shade)

    angle = rng.choice([-0.6, -0.35, 0.35, 0.6])
    rotated = img.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=(232, 230, 224))
    out = io.BytesIO()
    rotated.save(out, format="PNG", optimize=False, compress_level=6)
    return out.getvalue()


def render_noisy_scan_pdf_bytes(invoice: Invoice, seller: Party, buyer: Party, seed: int) -> bytes:
    """Wrap the scan PNG into an image-only PDF."""
    reportlab.rl_config.invariant = 1
    png_bytes = render_noisy_scan_png_bytes(invoice, seller, buyer, seed=seed)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle("Scanned document")
    c.setCreator("Tessera ReportLab Invariant v1")
    c.setProducer("Tessera ReportLab Invariant v1")
    width, height = A4
    c.drawImage(ImageReader(io.BytesIO(png_bytes)), 0, 0, width=width, height=height, mask="auto")
    c.showPage()
    c.save()
    return buf.getvalue()


def write_noisy_scan_pdf(out_path: Path, invoice: Invoice, seller: Party, buyer: Party, seed: int) -> str:
    """Write a deterministic noisy scan PDF and return its SHA-256."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_bytes = render_noisy_scan_pdf_bytes(invoice, seller, buyer, seed=seed)
    out_path.write_bytes(pdf_bytes)
    return hashlib.sha256(pdf_bytes).hexdigest()
