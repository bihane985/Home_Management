"""Génère les icônes PNG (iOS exige du PNG pour l'écran d'accueil), sans dépendance externe.

Usage : python tools/make_icons.py
"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "home_management" / "static" / "icons"
BG, FG = (0x46, 0x55, 0xD4), (0xFF, 0xFF, 0xFF)

# Maison dessinée dans un repère 512x512 (identique à icon.svg).
HOUSE = [(256, 112), (96, 248), (144, 248), (144, 400), (224, 400), (224, 304), (288, 304), (288, 400),
         (368, 400), (368, 248), (416, 248)]
CHIMNEY = (300, 140, 340, 210)


def inside_polygon(x, y, poly):
    result = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def pixel(x, y, rounded):
    if rounded:
        r = 112
        cx, cy = min(max(x, r), 512 - r), min(max(y, r), 512 - r)
        if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
            return None
    x0, y0, x1, y1 = CHIMNEY
    if inside_polygon(x, y, HOUSE) or (x0 <= x <= x1 and y0 <= y <= y1):
        return FG
    return BG


def render(size, rounded, samples=3):
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc, alpha = [0, 0, 0], 0
            for sy in range(samples):
                for sx in range(samples):
                    x = (px + (sx + 0.5) / samples) * 512 / size
                    y = (py + (sy + 0.5) / samples) * 512 / size
                    color = pixel(x, y, rounded)
                    if color:
                        alpha += 1
                        for k in range(3):
                            acc[k] += color[k]
            n = samples * samples
            rgb = [acc[k] // alpha if alpha else 0 for k in range(3)]
            row += bytes(rgb + [255 * alpha // n])
        rows.append(bytes(row))
    return b"".join(rows)


def write_png(path, size, rounded):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = render(size, rounded)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)
    print("écrit", path.name)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    write_png(OUT / "icon-192.png", 192, rounded=True)
    write_png(OUT / "icon-512.png", 512, rounded=False)  # « maskable » : fond plein, le système arrondit
    write_png(OUT / "apple-touch-icon.png", 180, rounded=False)  # iOS arrondit lui-même
