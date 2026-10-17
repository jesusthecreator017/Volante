#!/usr/bin/env python3
"""Draws the oso app icon (shuttlecock on court green) to OsoPhone/Assets.xcassets/AppIcon.appiconset/icon-1024.png.
Usage: python3 apple/scripts/make_icon.py   (needs Pillow)"""
import math
from pathlib import Path
from PIL import Image, ImageDraw

S = 4096  # supersample, downsampled to 1024
OUT = Path(__file__).resolve().parent.parent / "OsoPhone/Assets.xcassets/AppIcon.appiconset/icon-1024.png"

def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

# court green gradient, top light to bottom dark
c0, c1 = (30, 122, 84), (10, 56, 38)
grad = Image.linear_gradient("L").resize((S, S))
bg = Image.composite(Image.new("RGB", (S, S), c1), Image.new("RGB", (S, S), c0), grad)
d = ImageDraw.Draw(bg, "RGBA")
# court lines
w = S // 90
line = (255, 255, 255, 60)
m = int(S * 0.12)
d.rectangle([m, m, S - m, S - m], outline=line, width=w)
d.line([(S // 2, m), (S // 2, S - m)], fill=line, width=w)
d.line([(m, S // 2), (S - m, S // 2)], fill=line, width=w)

# shuttle, drawn pointing up, then rotated
sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
g = ImageDraw.Draw(sh)
cx, cork_y = S // 2, int(S * 0.70)
r = int(S * 0.095)
top_y, top_hw, bot_hw = int(S * 0.20), int(S * 0.215), int(S * 0.075)
feather = (250, 247, 238, 255)
# skirt: trapezoid with rounded top
g.polygon([(cx - bot_hw, cork_y - r // 2), (cx + bot_hw, cork_y - r // 2), (cx + top_hw, top_y + top_hw // 2), (cx - top_hw, top_y + top_hw // 2)], fill=feather)
g.ellipse([cx - top_hw, top_y, cx + top_hw, top_y + top_hw], fill=feather)
# feather ribs
for i in range(-3, 4):
    t = i / 3
    x0 = cx + int(bot_hw * 0.9 * t); x1 = cx + int(top_hw * 0.93 * t)
    g.line([(x0, cork_y - r // 2), (x1, top_y + top_hw // 2 - int(abs(t) ** 2 * top_hw * 0.18))], fill=(30, 110, 76, 120), width=S // 220)
# orange binding band
band_y = int(S * 0.50)
hw = int(bot_hw + (top_hw - bot_hw) * ((cork_y - band_y) / (cork_y - top_y - top_hw // 2)))
g.rectangle([cx - hw, band_y - S // 60, cx + hw, band_y + S // 60], fill=(242, 105, 18, 255))
# cork head
g.ellipse([cx - r, cork_y - r, cx + r, cork_y + r], fill=(242, 105, 18, 255))
g.ellipse([cx - r, cork_y - r, cx + r, cork_y + r], outline=(255, 255, 255, 60), width=S // 300)

sh = sh.rotate(-38, resample=Image.BICUBIC, center=(S // 2, S // 2))
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
shadow.paste((0, 0, 0, 90), mask=sh.split()[3])
shadow = shadow.transform(shadow.size, Image.AFFINE, (1, 0, -S * 0.012, 0, 1, -S * 0.016))
bg = Image.alpha_composite(bg.convert("RGBA"), shadow)
bg = Image.alpha_composite(bg, sh)
bg.convert("RGB").resize((1024, 1024), Image.LANCZOS).save(OUT)
print("wrote", OUT)
