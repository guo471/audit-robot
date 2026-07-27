# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


IMAGE_DIR = Path(r"C:\Users\85169\Desktop\sn码测试")
CSV_PATH = Path(r"C:\audit_robot\reports\sn_batch_test_results.csv")
OUT_PATH = Path(r"C:\audit_robot\reports\sn_contact_sheet.jpg")


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def main() -> int:
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    by_name = {row["file_name"]: row for row in rows}
    images = [IMAGE_DIR / row["file_name"] for row in rows]

    cols = 4
    thumb_w = 360
    thumb_h = 260
    label_h = 82
    pad = 14
    rows_count = (len(images) + cols - 1) // cols
    sheet_w = cols * (thumb_w + pad) + pad
    sheet_h = rows_count * (thumb_h + label_h + pad) + pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = load_font(18)
    small = load_font(15)

    for idx, path in enumerate(images):
        col = idx % cols
        row_i = idx // cols
        x = pad + col * (thumb_w + pad)
        y = pad + row_i * (thumb_h + label_h + pad)

        item = by_name[path.name]
        matched = str(item["matched"]).lower() == "true"
        color = (20, 130, 60) if matched else (190, 40, 40)
        status = "命中" if matched else "未命中"

        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            bg = Image.new("RGB", (thumb_w, thumb_h), (245, 245, 245))
            bx = (thumb_w - img.width) // 2
            by = (thumb_h - img.height) // 2
            bg.paste(img, (bx, by))
            sheet.paste(bg, (x, y))

        draw.rectangle([x, y, x + thumb_w, y + thumb_h], outline=color, width=4)
        text_y = y + thumb_h + 6
        draw.text((x, text_y), f"{idx + 1:02d}. {status} {item['path_used']} {item['total_elapsed_sec']}s", fill=color, font=font)
        draw.text((x, text_y + 26), path.name[:38], fill=(20, 20, 20), font=small)
        recognized = item["recognized_sn"] or "未识别到候选"
        draw.text((x, text_y + 50), recognized[:42], fill=(80, 80, 80), font=small)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT_PATH, quality=92)
    print(OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
