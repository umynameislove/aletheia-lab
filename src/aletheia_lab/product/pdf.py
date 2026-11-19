"""Deterministic, embedded-font text PDF; imported content is never interpreted."""
from __future__ import annotations

import importlib.util
import io
import threading
import unicodedata
from importlib.resources import files

from .boundary import ProductError

_FONT = "AletheiaNotoMono"
_LOCK = threading.Lock()


def pdf_available() -> bool:
    """Capability probe without loading the renderer or registering fonts."""
    return (importlib.util.find_spec("reportlab") is not None
            and files("aletheia_lab.product").joinpath("fonts/NotoSansMono-Regular.ttf").is_file())


def render_text_pdf(pages: tuple[tuple[str, ...], ...]) -> bytes:
    if not pdf_available():
        raise ProductError("feature_unavailable", "PDF rendering requires the PDF installation extra.")
    from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
    from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
    from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

    with _LOCK:
        if _FONT not in pdfmetrics.getRegisteredFontNames():
            payload = files("aletheia_lab.product").joinpath("fonts/NotoSansMono-Regular.ttf").read_bytes()
            pdfmetrics.registerFont(TTFont(_FONT, io.BytesIO(payload)))
    font = pdfmetrics.getFont(_FONT)
    # Refuse missing glyphs instead of silently replacing evidence or units with boxes.
    if any(ord(character) not in font.face.charToGlyph
           for page in pages for line in page for character in line):
        raise ProductError("pdf_unsupported_text", "Some report characters are outside the PDF font. Use JSON or Markdown.")
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(595, 842), pageCompression=1, invariant=1,
                    pdfVersion=(1, 4), initialFontName=_FONT)
    canvas.setTitle("Aletheia Lab Product Report")
    canvas.setAuthor("Aletheia Lab")
    for number, lines in enumerate(pages, 1):
        canvas.setFillColorRGB(0.16, 0.22, 0.29)
        canvas.setFont(_FONT, 8)
        canvas.drawString(54, 812, "Aletheia Lab Product Report")
        text = canvas.beginText(54, 788)
        text.setFont(_FONT, 9)
        text.setLeading(12)
        for line in lines:
            text.textLine(unicodedata.normalize("NFC", line))
        canvas.drawText(text)
        canvas.setFont(_FONT, 8)
        canvas.drawCentredString(297.5, 30, f"Page {number} of {len(pages)}")
        canvas.showPage()
    canvas.save()
    return output.getvalue()
