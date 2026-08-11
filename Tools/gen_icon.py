"""Generate a crisp Infinity Code app-icon set (Pillow only, no SVG dep).

Draws the lemniscate (∞) as a round-cap amber stroke with a soft glow on a
charcoal rounded-square, supersampled 4x for clean anti-aliasing, then emits all
Tauri icon sizes + a multi-resolution .ico.
"""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(r"C:\Users\caleb\infinity-code\src-tauri\icons")
SS = 4                      # supersample factor
BASE = 512                 # logical icon size
S = BASE * SS              # working canvas

# palette
CHARCOAL_A = (32, 31, 36, 255)     # #201f24 (top)
CHARCOAL_B = (20, 19, 23, 255)     # #141317 (bottom)
AMBER = (240, 179, 71, 255)        # #f0b347
AMBER_HI = (248, 200, 110, 255)


def rounded_bg() -> Image.Image:
    """Charcoal rounded-square with a subtle vertical gradient + vignette."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # vertical gradient
    grad = Image.new("RGBA", (1, S))
    for y in range(S):
        t = y / (S - 1)
        r = int(CHARCOAL_A[0] * (1 - t) + CHARCOAL_B[0] * t)
        g = int(CHARCOAL_A[1] * (1 - t) + CHARCOAL_B[1] * t)
        b = int(CHARCOAL_A[2] * (1 - t) + CHARCOAL_B[2] * t)
        grad.putpixel((0, y), (r, g, b, 255))
    grad = grad.resize((S, S))
    # rounded-square mask (≈22% corner radius, modern app-icon look)
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def lemniscate_points(n=1400):
    """Lemniscate of Bernoulli, normalized to a nice icon footprint."""
    pts = []
    a = S * 0.34
    for i in range(n + 1):
        t = -math.pi + 2 * math.pi * i / n
        d = 1 + math.sin(t) ** 2
        x = a * math.cos(t) / d
        y = a * math.sin(t) * math.cos(t) / d
        pts.append((S / 2 + x, S / 2 + y))
    return pts


def stroke(layer_size, color, width):
    """Draw the lemniscate as dense round dots (round-cap/round-join stroke)."""
    layer = Image.new("RGBA", (layer_size, layer_size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    r = width / 2
    for (x, y) in lemniscate_points():
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)
    return layer


def build_master() -> Image.Image:
    img = rounded_bg()
    w = int(S * 0.055)                       # stroke width
    # soft glow under the mark
    glow = stroke(S, (240, 179, 71, 170), w * 1.5).filter(ImageFilter.GaussianBlur(S * 0.02))
    img.alpha_composite(glow)
    # main amber stroke
    img.alpha_composite(stroke(S, AMBER, w))
    # subtle top highlight pass for a bit of dimension
    hi = stroke(S, (248, 200, 110, 90), int(w * 0.45))
    img.alpha_composite(hi)
    return img.resize((BASE, BASE), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    master = build_master()  # 512
    # Tauri-required PNGs
    master.resize((32, 32), Image.LANCZOS).save(OUT / "32x32.png")
    master.resize((128, 128), Image.LANCZOS).save(OUT / "128x128.png")
    master.resize((256, 256), Image.LANCZOS).save(OUT / "128x128@2x.png")
    master.save(OUT / "icon.png")            # 512 (tray + generic)
    # multi-res .ico (Windows taskbar/installer)
    master.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32),
                                         (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote icons:", ", ".join(p.name for p in sorted(OUT.glob("*"))))


if __name__ == "__main__":
    main()
