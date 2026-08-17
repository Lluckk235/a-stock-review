#!/usr/bin/env python3
"""生成 A股收盘复盘 站点图标，输出到 assets/。

- apple-touch-icon.png (180, 不透明)  -> Safari「添加到主屏幕」必需
- favicon-32.png / favicon-16.png / favicon.ico (透明圆角) -> 浏览器标签
- icon-192.png / icon-512.png -> PWA manifest 备用
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ASSETS = os.path.join(REPO, "assets")
os.makedirs(ASSETS, exist_ok=True)

S = 512
BG = (26, 29, 36, 255)        # 深底 #1A1D24，与页面背景一致
RED = (226, 59, 59, 255)      # 涨 红（中国习惯）
GREEN = (22, 199, 132, 255)   # 跌 绿
WHITE = (255, 255, 255, 255)
RADIUS = 96


def new_canvas(size, radius, color):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=color)
    return img, d


def candle(d, cx, body_top, body_bottom, wick_top, wick_bottom, body_color, wick_color=WHITE, bw=64, ww=10):
    d.line([(cx, wick_top), (cx, wick_bottom)], fill=wick_color, width=ww)
    d.rounded_rectangle([cx - bw // 2, body_top, cx + bw // 2, body_bottom], radius=10, fill=body_color)


# 内容层：圆角深底 + 两根 K 线（红涨 / 绿跌），四角透明
content, d = new_canvas(S, RADIUS, BG)
candle(d, cx=190, body_top=170, body_bottom=330, wick_top=110, wick_bottom=400, body_color=RED)
candle(d, cx=330, body_top=250, body_bottom=320, wick_top=200, wick_bottom=380, body_color=GREEN)

# 透明圆角版（favicon 用）
fav = content
# 不透明版（apple-touch-icon / PWA 用，iOS 会自己加圆角遮罩，四角须不透明）
opq = Image.new("RGBA", (S, S), BG)
opq.alpha_composite(content)


def exp(img, sz):
    return img.resize((sz, sz), Image.LANCZOS)


exp(opq, 180).save(os.path.join(ASSETS, "apple-touch-icon.png"))
exp(fav, 32).save(os.path.join(ASSETS, "favicon-32.png"))
exp(fav, 16).save(os.path.join(ASSETS, "favicon-16.png"))
exp(opq, 192).save(os.path.join(ASSETS, "icon-192.png"))
exp(opq, 512).save(os.path.join(ASSETS, "icon-512.png"))
exp(fav, 48).save(os.path.join(ASSETS, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])

print("ICONS ->", ASSETS)
for f in sorted(os.listdir(ASSETS)):
    p = os.path.join(ASSETS, f)
    print(f"  {f}  {os.path.getsize(p)}B")
