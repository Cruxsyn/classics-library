"""Generate unified duotone author covers and deterministic monogram fallbacks."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

AUTHOR_CATALOG = Path("catalog/authors.json")
COVER_DIR = Path("assets/covers")
COVER_SIZE = (600, 900)
DARK_INK = "#2A1A1C"
WARM_CREAM = "#F4ECD8"
PALETTE = (
    "#7B2430",
    "#8A3A2A",
    "#9A2B3B",
    "#6F2C3F",
    "#7C3F2D",
    "#5F3446",
    "#8F4A36",
    "#71313A",
)

AUTHOR_FIELD_ORDER = (
    "key",
    "name",
    "language",
    "dates",
    "workCount",
    "portrait",
    "cover",
    "coverType",
    "wikipediaTitle",
    "commonsFileTitle",
    "commonsFilePage",
    "license",
    "licenseUrl",
    "artist",
    "credit",
    "attributionRequired",
    "fallback",
    "blurb",
)


@dataclass(frozen=True)
class MonogramFallback:
    type: str
    initial: str
    color: str

    def as_json(self) -> dict[str, str]:
        return {"type": self.type, "initial": self.initial, "color": self.color}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ordered_author(record: dict[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for field in AUTHOR_FIELD_ORDER:
        if field in record:
            ordered[field] = record[field]
    for field, value in record.items():
        if field not in ordered:
            ordered[field] = value
    return ordered


def monogram_for_author(key: str, name: str) -> MonogramFallback:
    words = [word for word in name.replace("-", " ").replace("'", " ").split() if word]
    if not words:
        initials = key[:1].upper()
    elif len(words) == 1:
        initials = words[0][:1].upper()
    else:
        initials = (words[0][:1] + words[-1][:1]).upper()
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    color = PALETTE[digest[0] % len(PALETTE)]
    return MonogramFallback(type="monogram", initial=initials, color=color)


def monogram_svg(key: str, name: str) -> tuple[str, MonogramFallback]:
    fallback = monogram_for_author(key, name)
    initial = html.escape(fallback.initial)
    label = html.escape(name)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900" role="img" aria-labelledby="title desc">
  <title id="title">{label}</title>
  <desc id="desc">Monogram cover for {label}</desc>
  <rect width="600" height="900" fill="{fallback.color}"/>
  <rect x="28" y="28" width="544" height="844" rx="18" fill="none" stroke="#F4ECD8" stroke-opacity="0.36" stroke-width="2"/>
  <circle cx="300" cy="438" r="204" fill="#2A1A1C" fill-opacity="0.18"/>
  <text x="300" y="472" text-anchor="middle" dominant-baseline="middle" fill="#F4ECD8" font-family="Newsreader, Georgia, 'Times New Roman', serif" font-size="188" font-weight="600" letter-spacing="8">{initial}</text>
  <text x="300" y="812" text-anchor="middle" fill="#F4ECD8" fill-opacity="0.76" font-family="Newsreader, Georgia, 'Times New Roman', serif" font-size="30" letter-spacing="2">CLASSICS</text>
</svg>
'''
    return svg, fallback


def crop_to_cover(image: Image.Image, ratio: float = 2 / 3) -> Image.Image:
    width, height = image.size
    current_ratio = width / height
    if current_ratio > ratio:
        crop_width = int(height * ratio)
        left = max(0, (width - crop_width) // 2)
        box = (left, 0, left + crop_width, height)
    else:
        crop_height = int(width / ratio)
        if crop_height > height:
            crop_height = height
        top = max(0, int((height - crop_height) * 0.18))
        box = (0, top, width, top + crop_height)
    return image.crop(box)


def duotone_cover(source: Path, destination: Path) -> None:
    with Image.open(source) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
    cropped = crop_to_cover(image)
    resized = cropped.resize(COVER_SIZE, Image.Resampling.LANCZOS)
    luminance = ImageOps.grayscale(resized)
    luminance = ImageOps.autocontrast(luminance, cutoff=1)
    toned = ImageOps.colorize(luminance, black=DARK_INK, white=WARM_CREAM)
    destination.parent.mkdir(parents=True, exist_ok=True)
    toned.save(destination, format="WEBP", quality=82, method=6)


def local_path(web_path: str) -> Path:
    return Path(web_path.lstrip("/"))


def write_contact_sheet(authors: list[dict[str, Any]], destination: Path = COVER_DIR / "_contactsheet.html") -> None:
    cards: list[str] = []
    for author in authors:
        cover = author.get("cover")
        if not cover:
            continue
        cover_name = html.escape(Path(cover).name)
        name = html.escape(author.get("name") or author.get("key") or "")
        key = html.escape(author.get("key") or "")
        cover_type = html.escape(author.get("coverType") or "")
        cards.append(
            f'<figure><img src="{cover_name}" alt="{name} cover" loading="lazy"><figcaption><strong>{name}</strong><span>{key} · {cover_type}</span></figcaption></figure>'
        )
    markup = "\n".join(cards)
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Author cover contact sheet</title>
  <style>
    body {{ margin: 0; background: #1f1517; color: #f4ecd8; font-family: Georgia, serif; }}
    main {{ padding: 32px; }}
    h1 {{ font-size: 28px; font-weight: 500; margin: 0 0 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 24px; }}
    figure {{ margin: 0; background: rgba(244,236,216,.06); border: 1px solid rgba(244,236,216,.16); padding: 12px; border-radius: 14px; }}
    img {{ display: block; width: 100%; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 8px; box-shadow: 0 10px 30px rgba(0,0,0,.28); }}
    figcaption {{ display: grid; gap: 4px; margin-top: 10px; font-size: 14px; line-height: 1.25; }}
    figcaption span {{ color: rgba(244,236,216,.68); font-size: 12px; }}
  </style>
</head>
<body>
<main>
  <h1>Author cover contact sheet</h1>
  <section class="grid">
{markup}
  </section>
</main>
</body>
</html>
"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html_doc, encoding="utf-8")


def generate_covers(authors_path: Path = AUTHOR_CATALOG) -> dict[str, Any]:
    authors = read_json(authors_path)
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    monogram_fallbacks: list[str] = []

    for author in authors:
        key = author["key"]
        name = author["name"]
        portrait = author.get("portrait")
        if portrait:
            source = local_path(portrait)
            destination = COVER_DIR / f"{key}.webp"
            duotone_cover(source, destination)
            author["cover"] = "/" + destination.as_posix()
            author["coverType"] = "duotone"
            author["fallback"] = None
        else:
            svg, fallback = monogram_svg(key, name)
            destination = COVER_DIR / f"{key}.svg"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(svg, encoding="utf-8")
            author["cover"] = "/" + destination.as_posix()
            author["coverType"] = "monogram"
            author["fallback"] = fallback.as_json()
            monogram_fallbacks.append(key)

    ordered = [ordered_author(author) for author in authors]
    write_contact_sheet(ordered)
    write_json(authors_path, ordered)
    return {
        "authors_total": len(ordered),
        "covers_written": sum(1 for author in ordered if author.get("cover")),
        "monogram_fallbacks": monogram_fallbacks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate author cover images.")
    parser.parse_args()
    result = generate_covers()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
