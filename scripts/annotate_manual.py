"""Draw numbered callouts on manual screenshots (Pillow)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "assets" / "manual"
FONT = "C:/Windows/Fonts/msyhbd.ttc"


def annotate(src: str, dst: str, boxes: list[tuple[tuple[int, int, int, int], int]]):
    image = Image.open(OUT / src).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.truetype(FONT, 30)
    for (x1, y1, x2, y2), number in boxes:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=16, outline=(255, 92, 58, 255), width=6, fill=(255, 92, 58, 22))
        r = 24
        cx, cy = x1 + 4, y1 + 4
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 92, 58, 255), outline=(255, 255, 255, 255), width=3)
        text = str(number)
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (cx - (bbox[2] - bbox[0]) / 2, cy - (bbox[3] - bbox[1]) / 2 - 3),
            text,
            font=font,
            fill=(255, 255, 255, 255),
        )
    result = Image.alpha_composite(image, overlay).convert("RGB")
    result.save(OUT / dst, "PNG", optimize=True)
    print(dst, result.size)


annotate(
    "student-chat.png",
    "student-chat-annotated.png",
    [
        ((588, 840, 1394, 908), 1),   # composer
        ((624, 722, 1336, 800), 2),   # citation card
        ((284, 96, 548, 570), 3),     # history
    ],
)
