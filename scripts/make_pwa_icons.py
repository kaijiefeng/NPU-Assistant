"""生成 PWA 图标（192/512）。

用系统 Python（需 Pillow）运行；仓库 .venv 不强制安装 Pillow：
    python scripts\make_pwa_icons.py
输出：frontend/assets/icons/icon-192.png、icon-512.png、icon-maskable-512.png
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("需要 Pillow：请用系统 Python 运行，或 pip install pillow")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "assets" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

BLUE = (11, 61, 145)
BLUE_LIGHT = (18, 90, 190)
CYAN = (94, 234, 212)
WHITE = (240, 247, 255)


def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def make_icon(size: int, *, maskable: bool = False) -> Image.Image:
    scale = 4  # 超采样，边缘更平滑
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), BLUE + (255,))
    draw = ImageDraw.Draw(image)
    # 背景：从左上到右下的渐变
    for y in range(canvas):
        ratio = y / max(1, canvas - 1)
        color = tuple(int(BLUE[i] + (BLUE_LIGHT[i] - BLUE[i]) * ratio) for i in range(3))
        draw.line([(0, y), (canvas, y)], fill=color + (255,))
    # 圆角（maskable 用全出血背景，不做圆角）
    if not maskable:
        mask = Image.new("L", (canvas, canvas), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, canvas - 1, canvas - 1], radius=int(canvas * 0.22), fill=255
        )
        image.putalpha(mask)
    # 中央 "N" + 底部青条
    draw = ImageDraw.Draw(image)
    letter_ratio = 0.42 if maskable else 0.5
    font = _font(int(canvas * letter_ratio))
    text = "N"
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(
        ((canvas - tw) / 2 - box[0], (canvas - th) / 2 - box[1] - canvas * 0.02),
        text,
        font=font,
        fill=WHITE + (255,),
    )
    bar_w, bar_h = int(canvas * 0.30), int(canvas * 0.045)
    draw.rounded_rectangle(
        [(canvas - bar_w) / 2, canvas * 0.70, (canvas + bar_w) / 2, canvas * 0.70 + bar_h],
        radius=bar_h // 2,
        fill=CYAN + (255,),
    )
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    make_icon(192).save(OUT / "icon-192.png")
    make_icon(512).save(OUT / "icon-512.png")
    make_icon(512, maskable=True).save(OUT / "icon-maskable-512.png")
    for path in sorted(OUT.glob("*.png")):
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
