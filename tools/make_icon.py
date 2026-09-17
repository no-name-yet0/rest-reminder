"""生成 exe 图标（打包时调用，不参与运行时）。"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

ACCENT = (108, 140, 255, 255)
WHITE = (255, 255, 255, 255)
SIZE = 1024


def build(path: str) -> str:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, SIZE - 1, SIZE - 1], radius=int(SIZE * 0.23), fill=ACCENT
    )

    moon = Image.new("L", (SIZE, SIZE), 0)
    md = ImageDraw.Draw(moon)
    md.ellipse(
        [SIZE * 0.20, SIZE * 0.24, SIZE * 0.20 + SIZE * 0.54, SIZE * 0.24 + SIZE * 0.54],
        fill=255,
    )
    md.ellipse(
        [SIZE * 0.35, SIZE * 0.15, SIZE * 0.35 + SIZE * 0.54, SIZE * 0.15 + SIZE * 0.54],
        fill=0,
    )
    img.paste(Image.new("RGBA", (SIZE, SIZE), WHITE), (0, 0), moon)

    for cx, cy, r in ((0.74, 0.30, 0.045), (0.80, 0.47, 0.030), (0.68, 0.62, 0.024)):
        draw.ellipse(
            [
                SIZE * (cx - r),
                SIZE * (cy - r),
                SIZE * (cx + r),
                SIZE * (cy + r),
            ],
            fill=(255, 255, 255, 215),
        )

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(
        path,
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return path


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(here, "assets", "icon.ico")
    print(build(target))
