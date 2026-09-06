#!/usr/bin/env python3
"""Generate icon.png (512 px) + multi-size icon.ico with Qt. Edit draw() per app; keep the rounded gradient tile."""
import pathlib
import sys
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication


def draw(size: int, c1="#7c3aed", c2="#b45309") -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(c1))
    g.setColorAt(1, QColor(c2))
    p.setBrush(QBrush(g))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.2, size * 0.2)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(max(1.0, size * 0.06))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    # lens: circle outline + filled aperture dot (2 strokes, reads at 16 px)
    p.drawEllipse(QPointF(size * 0.5, size * 0.5), size * 0.26, size * 0.26)
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawEllipse(QPointF(size * 0.5, size * 0.5), size * 0.10, size * 0.10)
    p.end()
    return img


def write_ico(png: str = "icon.png", ico: str = "icon.ico") -> None:
    """
    Write the .ico the way Windows has always accepted it: bitmaps up to 128, PNG for the 256.

    Pillow writes every entry PNG-compressed, which modern Windows reads - but the taskbar draws at 16 to 32
    pixels, and an .ico that cannot serve those sizes is answered with the shell's own generic icon. Writing
    the small entries as bitmaps removes any question of which half is at fault.
    """
    import io, struct
    from PIL import Image
    src = Image.open(png).convert("RGBA")
    images = []
    for size in (16, 20, 24, 32, 40, 48, 64, 128):
        buf = io.BytesIO()
        src.resize((size, size), Image.LANCZOS).save(buf, format="ICO", sizes=[(size, size)],
                                                     bitmap_format="bmp")
        blob = buf.getvalue()
        _, _, _, _, _, bits, length, offset = struct.unpack("<BBBBHHII", blob[6:22])
        images.append((size, blob[offset:offset + length], bits))
    buf = io.BytesIO()
    src.resize((256, 256), Image.LANCZOS).save(buf, format="PNG")
    images.append((256, buf.getvalue(), 32))

    offset = 6 + 16 * len(images)
    directory = payload = b""
    for size, blob, bits in images:
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, bits, len(blob), offset)
        payload += blob
        offset += len(blob)
    pathlib.Path(ico).write_bytes(struct.pack("<HHH", 0, 1, len(images)) + directory + payload)
    print(f"{ico}: {len(images)} images — {', '.join(str(s) for s, _, _ in images)} px")


if __name__ == "__main__":
    QApplication(sys.argv)
    draw(512).save("icon.png")
    write_ico()
    print("icon.png + icon.ico written")
