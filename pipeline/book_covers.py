"""Generate a unique per-work cover image for each readable book.

For every work in ``catalog/catalog.json`` this resolves a *depicting* image for
the work itself (not its author) from public-domain / openly-licensed artwork on
Wikimedia, smart-crops it to a 2:3 colour cover, and applies a subtle unifying
treatment. When no usable artwork is found it falls back to a deterministic,
genre-themed generative cover seeded from the work id so every cover is distinct.

Network access is conservative, matching ``pipeline/portraits.py``: a descriptive
user agent with a contact address, on-disk caching of every API response and
downloaded image, and a modest request pace so re-runs hit the cache.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import mimetypes
import random
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

import httpx
from PIL import Image, ImageDraw, ImageOps

CATALOG = Path("catalog/catalog.json")
COVER_DIR = Path("assets/book-covers")
CACHE_DIR = Path("data/raw/book_covers")
API_CACHE_DIR = CACHE_DIR / "api"
IMAGE_CACHE_DIR = CACHE_DIR / "images"

COVER_SIZE = (600, 900)
COVER_RATIO = COVER_SIZE[0] / COVER_SIZE[1]

USER_AGENT = "ClassicsLibrary/1.0 (super.croox@gmail.com)"
REQUEST_PAUSE_SECONDS = 0.3
DOWNLOAD_PAUSE_SECONDS = 1.0
MAX_DOWNLOAD_ATTEMPTS = 4
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
REST_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

# --- Genre classification -------------------------------------------------

# Primary classification is by ``authorKey``; ``GENRE_TITLE_KEYWORDS`` refines a
# handful of works whose subject diverges from the author's usual genre.
GENRE_BY_AUTHOR: dict[str, str] = {
    # epic
    "Homer": "epic",
    "Virgil": "epic",
    "Ovid": "epic",
    "Quintus": "epic",
    # Apollonius of Rhodes (Argonautica) — epic, not the mathematician of Perga.
    "Apollonius": "epic",
    # tragedy
    "Aeschylus": "tragedy",
    "Sophocles": "tragedy",
    "Euripides": "tragedy",
    # comedy
    "Aristophanes": "comedy",
    # philosophy
    "Plato": "philosophy",
    "Aristotle": "philosophy",
    "Plotinus": "philosophy",
    "Epictetus": "philosophy",
    "Epicurus": "philosophy",
    "Porphyry": "philosophy",
    "Carus": "philosophy",  # Lucretius, De Rerum Natura
    "Apuleius": "philosophy",
    # history
    "Herodotus": "history",
    "Thucydides": "history",
    "Plutarch": "history",
    "Xenophon": "history",
    "Josephus": "history",
    "Livy": "history",
    "Tacitus": "history",
    "Caesar": "history",
    "Diodorus": "history",
    "Strabo": "history",
    "Augustus": "history",
    # oratory
    "Demosthenes": "oratory",
    "Isocrates": "oratory",
    "Lysias": "oratory",
    "Aeschines": "oratory",
    "Antiphon": "oratory",
    "Andocides": "oratory",
    "Hyperides": "oratory",
    "Dinarchus": "oratory",
    "Lycurgus": "oratory",
    "Demades": "oratory",
    # science
    "Hippocrates": "science",
    "Euclid": "science",
    "Galen": "science",
    "Archimedes": "science",
    # wisdom
    "Confucius": "wisdom",
    "Lao": "wisdom",
    "Antoninus": "wisdom",
    "Sadi": "wisdom",
    "Khayyam": "wisdom",
    "Ferdowsi": "wisdom",
    "Aesop": "wisdom",
    "Tzu": "wisdom",
    # lyric
    "Pindar": "lyric",
    "Hesiod": "lyric",
    "Bacchylides": "lyric",
}

GENRE_ACCENT: dict[str, str] = {
    "epic": "#7C2230",
    "tragedy": "#2E2A4D",
    "comedy": "#8A6A1F",
    "philosophy": "#1F5A52",
    "history": "#5A3E2B",
    "oratory": "#3A4A55",
    "science": "#3F5A2E",
    "wisdom": "#6B4A2A",
    "lyric": "#5A2E4D",
}

DEFAULT_GENRE = "philosophy"

# Lightweight title-keyword refinement layer. The first matching keyword wins.
GENRE_TITLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("comedy", "comedy"),
    ("tragedy", "tragedy"),
    ("oration", "oratory"),
    ("oratio", "oratory"),
    ("speech", "oratory"),
    ("philippic", "oratory"),
    ("against ", "oratory"),
)


def classify_genre(work: dict[str, Any]) -> str:
    genre = GENRE_BY_AUTHOR.get(work.get("authorKey", ""), DEFAULT_GENRE)
    title = (work.get("title") or "").lower()
    for keyword, mapped in GENRE_TITLE_KEYWORDS:
        if keyword in title:
            genre = mapped
            break
    return genre


# --- JSON / text helpers (mirrors portraits.py conventions) ---------------


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if cleaned:
            self.parts.append(cleaned)


def html_to_text(value: str | None) -> str | None:
    if not value:
        return None
    parser = _TextExtractor()
    parser.feed(value)
    text = " ".join(parser.parts) if parser.parts else value
    text = " ".join(html.unescape(text).split())
    return text or None


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_url_for_cache(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query_items = sorted(parse_qsl(parsed.query, keep_blank_values=True))
    query_items.extend(sorted(params.items()))
    return urlunparse(parsed._replace(query=urlencode(query_items, doseq=True)))


# --- Cached Wikimedia client ----------------------------------------------


class CachedWikimediaClient:
    def __init__(self, cache_dir: Path = API_CACHE_DIR, image_dir: Path = IMAGE_CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        self.image_dir = image_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True)
        self.cache_hits = 0
        self.network_fetches = 0

    def close(self) -> None:
        self.client.close()

    def _cache_path(self, url: str, params: dict[str, str]) -> Path:
        normalized = normalize_url_for_cache(url, params)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get_json(self, url: str, params: dict[str, str] | None = None, optional: bool = False) -> dict[str, Any] | None:
        params = params or {}
        cache_path = self._cache_path(url, params)
        if cache_path.exists():
            self.cache_hits += 1
            cached = read_json(cache_path)
            if cached.get("_missing"):
                return None
            return cached["body"]

        response = self.client.get(url, params=params)
        if optional and response.status_code == 404:
            write_json(cache_path, {"url": str(response.url), "status_code": 404, "_missing": True})
            self.network_fetches += 1
            time.sleep(REQUEST_PAUSE_SECONDS)
            return None
        response.raise_for_status()
        body = response.json()
        write_json(
            cache_path,
            {
                "url": str(response.url),
                "status_code": response.status_code,
                "fetched_with": USER_AGENT,
                "body": body,
            },
        )
        self.network_fetches += 1
        time.sleep(REQUEST_PAUSE_SECONDS)
        return body

    def get_image_bytes(self, url: str) -> bytes | None:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        suffix = suffix_from_url(url) or ".img"
        cached = self.image_dir / f"{digest}{suffix}"
        if cached.exists() and cached.stat().st_size > 0:
            self.cache_hits += 1
            return cached.read_bytes()

        for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
            response = self.client.get(url)
            self.network_fetches += 1
            if response.status_code == 429 and attempt < MAX_DOWNLOAD_ATTEMPTS:
                retry_after = response.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 12 * attempt
                time.sleep(delay)
                continue
            response.raise_for_status()
            data = response.content
            time.sleep(DOWNLOAD_PAUSE_SECONDS)
            if data:
                cached.write_bytes(data)
            return data
        return None


# --- URL / image utilities ------------------------------------------------


def suffix_from_url(url: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if suffix in {".jpeg", ".jpe"}:
        return ".jpg"
    if not suffix:
        guessed = mimetypes.guess_extension(urlparse(url).path)
        suffix = guessed.lower() if guessed else ""
    return suffix


def commons_file_title_from_url(url: str) -> str | None:
    if not url:
        return None
    segments = [segment for segment in urlparse(url).path.split("/") if segment]
    if not segments:
        return None
    if "thumb" in segments and len(segments) >= 2:
        filename = segments[-2]
    else:
        filename = segments[-1]
    return f"File:{unquote(filename)}"


def commons_page_for_title(file_title: str | None) -> str | None:
    if not file_title:
        return None
    return "https://commons.wikimedia.org/wiki/" + quote(file_title.replace(" ", "_"), safe="/:_(),.-")


def metadata_value(metadata: dict[str, Any], key: str) -> str | None:
    raw = metadata.get(key, {}).get("value")
    return html_to_text(raw)


def validate_image_bytes(data: bytes) -> bool:
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


# --- Wikipedia work-image resolution --------------------------------------


@dataclass(frozen=True)
class CommonsImageInfo:
    file_title: str
    source_url: str | None
    thumb_url: str | None
    commons_file_page: str | None
    license: str | None
    license_url: str | None
    artist: str | None
    credit: str | None
    attribution_required: bool


@dataclass(frozen=True)
class WorkArtwork:
    wikipedia_title: str
    image_url: str
    info: CommonsImageInfo


def de_article(title: str) -> str:
    for prefix in ("The ", "A ", "An "):
        if title.startswith(prefix):
            return title[len(prefix):]
    return title


def candidate_titles(work: dict[str, Any]) -> list[str]:
    """Ordered candidate Wikipedia article titles for the *work* itself."""
    title = (work.get("title") or "").strip()
    author = (work.get("author") or "").strip()
    bare = de_article(title)
    ordered: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)

    add(title)
    add(bare)
    for suffix in ("poem", "play", "tragedy", "dialogue", "Plato dialogue"):
        add(f"{bare} ({suffix})")
    if author:
        add(f"{bare} ({author})")
        add(f"{title} ({author})")
    return ordered


ACCEPTED_LICENSE_PREFIXES = ("pd", "cc0", "cc-by")
ACCEPTED_LICENSE_PHRASES = ("public domain", "pd-art", "cc0", "cc by", "cc-by")


def license_is_open(machine: str | None, short_name: str | None) -> bool:
    code = (machine or "").strip().lower()
    if code.startswith(ACCEPTED_LICENSE_PREFIXES):
        return True
    text = (short_name or "").strip().lower()
    return any(phrase in text for phrase in ACCEPTED_LICENSE_PHRASES)


def fetch_commons_imageinfo(client: CachedWikimediaClient, file_title: str) -> CommonsImageInfo | None:
    response = client.get_json(
        COMMONS_API,
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "imageinfo",
            "iiprop": "extmetadata|url",
            "iiextmetadatafilter": "LicenseShortName|License|LicenseUrl|Artist|Credit|AttributionRequired|UsageTerms",
            "iiurlwidth": "960",
            "titles": file_title,
        },
    )
    if not response:
        return None
    pages = response.get("query", {}).get("pages", [])
    for page in pages:
        if page.get("missing"):
            continue
        imageinfo = (page.get("imageinfo") or [{}])[0]
        metadata = imageinfo.get("extmetadata", {})
        machine_license = metadata.get("License", {}).get("value")
        short_name = metadata_value(metadata, "LicenseShortName") or metadata_value(metadata, "UsageTerms")
        if not license_is_open(machine_license, short_name):
            return None
        return CommonsImageInfo(
            file_title=page.get("title") or file_title,
            source_url=imageinfo.get("url"),
            thumb_url=imageinfo.get("thumburl"),
            commons_file_page=imageinfo.get("descriptionurl") or commons_page_for_title(page.get("title") or file_title),
            license=short_name,
            license_url=metadata_value(metadata, "LicenseUrl"),
            artist=metadata_value(metadata, "Artist"),
            credit=metadata_value(metadata, "Credit"),
            attribution_required=metadata_value(metadata, "AttributionRequired") == "true",
        )
    return None


def best_download_url(page_image_url: str | None, info: CommonsImageInfo) -> str | None:
    for url in (info.thumb_url, page_image_url, info.source_url):
        if not url:
            continue
        if suffix_from_url(url) in SUPPORTED_IMAGE_SUFFIXES:
            return url
    return None


def resolve_pageimages(client: CachedWikimediaClient, titles: list[str]) -> dict[str, dict[str, Any]]:
    """Map each requested title to its resolved page (after redirects)."""
    response = client.get_json(
        WIKIPEDIA_API,
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
            "prop": "pageimages|pageprops",
            "piprop": "original|thumbnail|name",
            "pithumbsize": "960",
            "titles": "|".join(titles),
        },
    )
    out: dict[str, dict[str, Any]] = {}
    if not response:
        return out
    query = response.get("query", {})
    normalized = {item["from"]: item["to"] for item in query.get("normalized", [])}
    redirects = {item["from"]: item["to"] for item in query.get("redirects", [])}
    pages = {page.get("title"): page for page in query.get("pages", [])}
    for title in titles:
        step = normalized.get(title, title)
        final = redirects.get(step, step)
        page = pages.get(final)
        if page:
            out[title] = page
    return out


def resolve_via_rest(client: CachedWikimediaClient, title: str) -> tuple[str, str] | None:
    url = REST_SUMMARY + quote(title.replace(" ", "_"), safe="")
    summary = client.get_json(url, optional=True)
    if not summary:
        return None
    if summary.get("type") == "disambiguation":
        return None
    image = summary.get("originalimage") or summary.get("thumbnail")
    source = image.get("source") if image else None
    if not source:
        return None
    return summary.get("title") or title, source


def resolve_work_artwork(client: CachedWikimediaClient, work: dict[str, Any]) -> WorkArtwork | None:
    candidates = candidate_titles(work)
    if not candidates:
        return None
    pages = resolve_pageimages(client, candidates)

    # First pass: pageimages original/thumbnail in candidate priority order.
    for title in candidates:
        page = pages.get(title)
        if not page or page.get("missing"):
            continue
        if "disambiguation" in page.get("pageprops", {}):
            continue
        image = page.get("original") or page.get("thumbnail")
        image_url = image.get("source") if image else None
        if not image_url:
            continue
        artwork = _accept_image(client, page.get("title") or title, image_url)
        if artwork:
            return artwork

    # Second pass: REST summary as a fallback source of a depicting image.
    for title in candidates:
        resolved = resolve_via_rest(client, title)
        if not resolved:
            continue
        wiki_title, image_url = resolved
        artwork = _accept_image(client, wiki_title, image_url)
        if artwork:
            return artwork
    return None


def _accept_image(client: CachedWikimediaClient, wiki_title: str, image_url: str) -> WorkArtwork | None:
    file_title = commons_file_title_from_url(image_url)
    if not file_title:
        return None
    info = fetch_commons_imageinfo(client, file_title)
    if not info:
        return None
    return WorkArtwork(wikipedia_title=wiki_title, image_url=image_url, info=info)


# --- Colour helpers -------------------------------------------------------


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def mix(color: tuple[int, int, int], target: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(c + (target[c_i] - c) * t) for c_i, c in enumerate(color))  # type: ignore[return-value]


def darken(color: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return mix(color, (0, 0, 0), t)


def lighten(color: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return mix(color, (255, 255, 255), t)


def vertical_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=mix(top, bottom, t))
    return image


# --- Art cover treatment --------------------------------------------------


def smart_crop_cover(image: Image.Image, ratio: float = COVER_RATIO, vertical_bias: float = 0.12) -> Image.Image:
    width, height = image.size
    current = width / height
    if current > ratio:
        # Too wide: crop horizontally, keep the centre.
        crop_width = int(round(height * ratio))
        left = max(0, (width - crop_width) // 2)
        box = (left, 0, left + crop_width, height)
    else:
        # Too tall: crop vertically, bias toward the upper/centre region so
        # faces and subjects near the top are preserved.
        crop_height = min(height, int(round(width / ratio)))
        top = max(0, int(round((height - crop_height) * vertical_bias)))
        box = (0, top, width, top + crop_height)
    return image.crop(box)


def bottom_vignette_mask(size: tuple[int, int], start: float = 0.5, max_alpha: int = 165) -> Image.Image:
    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    start_y = int(height * start)
    span = max(1, height - start_y)
    for y in range(start_y, height):
        t = (y - start_y) / span
        draw.line([(0, y), (width, y)], fill=int(round((t ** 1.4) * max_alpha)))
    return mask


def render_art_cover(data: bytes, destination: Path) -> None:
    with Image.open(BytesIO(data)) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
    cropped = smart_crop_cover(image)
    resized = cropped.resize(COVER_SIZE, Image.Resampling.LANCZOS)

    # Subtle warm overlay (~8%) to unify the shelf without duotoning the art.
    warm = Image.new("RGB", COVER_SIZE, (214, 162, 94))
    toned = Image.blend(resized, warm, 0.08)

    # Gentle bottom vignette so the CSS title overlay stays legible.
    dark = Image.new("RGB", COVER_SIZE, (14, 10, 12))
    vignetted = Image.composite(dark, toned, bottom_vignette_mask(COVER_SIZE))

    destination.parent.mkdir(parents=True, exist_ok=True)
    vignetted.save(destination, format="WEBP", quality=84, method=6)


# --- Themed generative cover ----------------------------------------------


def draw_meander_border(draw: ImageDraw.ImageDraw, size: tuple[int, int], inset: int, color: tuple[int, int, int]) -> None:
    """A Greek key / meander band rendered as two offset square waves per side."""
    width, height = size
    left, top = inset, inset
    right, bottom = width - inset, height - inset
    band = 16
    period = 32
    line_width = 3

    def square_wave(a: int, b: int, base: int, depth: int, horizontal: bool) -> list[tuple[int, int]]:
        points: list[tuple[int, int]] = []
        level = 0
        pos = a
        while pos <= b:
            value = base + (depth if level else 0)
            points.append((pos, value) if horizontal else (value, pos))
            points.append((min(pos + period // 2, b), value) if horizontal else (value, min(pos + period // 2, b)))
            level ^= 1
            pos += period // 2
        return points

    # Outer frame.
    draw.rectangle([left, top, right, bottom], outline=color, width=line_width)
    # Top / bottom meander bands.
    draw.line(square_wave(left, right, top + 6, band, True), fill=color, width=line_width, joint="curve")
    draw.line(square_wave(left, right, bottom - 6 - band, band, True), fill=color, width=line_width, joint="curve")
    # Left / right meander bands.
    draw.line(square_wave(top, bottom, left + 6, band, False), fill=color, width=line_width, joint="curve")
    draw.line(square_wave(top, bottom, right - 6 - band, band, False), fill=color, width=line_width, joint="curve")


def draw_polygon_motif(draw: ImageDraw.ImageDraw, center: tuple[int, int], rng: random.Random, ink: tuple[int, int, int]) -> None:
    cx, cy = center
    sides = rng.randint(3, 8)
    rings = rng.randint(4, 7)
    base_radius = rng.uniform(150, 235)
    rotation_step = rng.uniform(math.pi / 18, math.pi / 5)
    phase = rng.uniform(0, math.tau)
    for ring in range(rings):
        radius = base_radius * (1 - ring / (rings + 0.6))
        if radius < 14:
            continue
        rotation = phase + rotation_step * ring
        points = [
            (
                cx + radius * math.cos(rotation + i * math.tau / sides),
                cy + radius * math.sin(rotation + i * math.tau / sides),
            )
            for i in range(sides)
        ]
        alpha = int(70 + 150 * (1 - ring / rings))
        draw.polygon(points, outline=ink + (alpha,))


def draw_constellation_motif(draw: ImageDraw.ImageDraw, center: tuple[int, int], rng: random.Random, ink: tuple[int, int, int]) -> None:
    cx, cy = center
    count = rng.randint(7, 13)
    stars: list[tuple[int, int]] = []
    for _ in range(count):
        angle = rng.uniform(0, math.tau)
        dist = rng.uniform(40, 230)
        x = cx + dist * math.cos(angle) * rng.uniform(0.65, 1.0)
        y = cy + dist * math.sin(angle)
        stars.append((int(x), int(y)))
    # Connect the path with thin lines, then mark the stars.
    for a, b in zip(stars, stars[1:]):
        draw.line([a, b], fill=ink + (110,), width=2)
    for x, y in stars:
        r = rng.choice((3, 4, 5, 6))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=ink + (230,))


GENRE_GLYPH_ORDER = ("epic", "tragedy", "comedy", "philosophy", "history", "oratory", "science", "wisdom", "lyric")


def draw_genre_glyph(draw: ImageDraw.ImageDraw, center: tuple[int, int], genre: str, ink: tuple[int, int, int]) -> None:
    """A small per-genre emblem inside a ring (kept deliberately simple)."""
    cx, cy = center
    r = 30
    stroke = ink + (210,)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=stroke, width=3)
    if genre == "epic":  # laurel-ish prow: an upward triangle
        draw.line([(cx - 16, cy + 12), (cx, cy - 16), (cx + 16, cy + 12)], fill=stroke, width=3, joint="curve")
    elif genre == "tragedy":  # downturned mask: a frown arc
        draw.arc([cx - 16, cy - 4, cx + 16, cy + 24], start=200, end=340, fill=stroke, width=3)
    elif genre == "comedy":  # upturned mask: a smile arc
        draw.arc([cx - 16, cy - 18, cx + 16, cy + 10], start=20, end=160, fill=stroke, width=3)
    elif genre == "philosophy":  # sun / mind: concentric dot
        draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], outline=stroke, width=3)
    elif genre == "history":  # scroll: a horizontal bar with end curls
        draw.line([(cx - 15, cy), (cx + 15, cy)], fill=stroke, width=3)
        draw.arc([cx - 18, cy - 8, cx - 6, cy + 8], start=270, end=90, fill=stroke, width=3)
        draw.arc([cx + 6, cy - 8, cx + 18, cy + 8], start=90, end=270, fill=stroke, width=3)
    elif genre == "oratory":  # rostrum: a stepped podium
        draw.line([(cx - 16, cy + 12), (cx + 16, cy + 12)], fill=stroke, width=3)
        draw.line([(cx, cy + 12), (cx, cy - 14)], fill=stroke, width=3)
    elif genre == "science":  # cross of measure
        draw.line([(cx - 16, cy), (cx + 16, cy)], fill=stroke, width=3)
        draw.line([(cx, cy - 16), (cx, cy + 16)], fill=stroke, width=3)
    elif genre == "wisdom":  # diamond / lotus
        draw.polygon([(cx, cy - 16), (cx + 14, cy), (cx, cy + 16), (cx - 14, cy)], outline=stroke, width=3)
    elif genre == "lyric":  # lyre strings
        for dx in (-9, -3, 3, 9):
            draw.line([(cx + dx, cy - 14), (cx + dx, cy + 14)], fill=stroke, width=2)


def render_themed_cover(work_id: str, genre: str, destination: Path) -> None:
    accent = hex_to_rgb(GENRE_ACCENT.get(genre, GENRE_ACCENT[DEFAULT_GENRE]))
    seed = int(hashlib.sha256(work_id.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)

    top = darken(accent, 0.58)
    bottom = darken(accent, 0.26)
    base = vertical_gradient(COVER_SIZE, top, bottom).convert("RGBA")

    overlay = Image.new("RGBA", COVER_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    ink = lighten(accent, 0.72)  # warm cream-ish line colour

    center = (COVER_SIZE[0] // 2, int(COVER_SIZE[1] * 0.46))
    if rng.random() < 0.5:
        draw_polygon_motif(draw, center, rng, ink)
    else:
        draw_constellation_motif(draw, center, rng, ink)

    draw_meander_border(draw, COVER_SIZE, inset=34, color=ink + (150,))
    draw_genre_glyph(draw, (COVER_SIZE[0] // 2, 120), genre, ink)

    composed = Image.alpha_composite(base, overlay).convert("RGB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    composed.save(destination, format="WEBP", quality=88, method=6)


# --- Per-work driver ------------------------------------------------------


def work_paths(work: dict[str, Any]) -> tuple[str, Path, Path, str]:
    work_id = work["id"]
    author_key = work["authorKey"]
    slug = work_id.split("/", 1)[1] if "/" in work_id else work_id
    cover_path = COVER_DIR / author_key / f"{slug}.webp"
    sidecar_path = COVER_DIR / author_key / f"{slug}.json"
    web_path = f"/assets/book-covers/{author_key}/{slug}.webp"
    return work_id, cover_path, sidecar_path, web_path


def themed_sidecar(work_id: str, web_path: str, genre: str) -> dict[str, Any]:
    return {
        "id": work_id,
        "cover": web_path,
        "source": "themed",
        "genre": genre,
        "license": None,
        "licenseUrl": None,
        "artist": None,
        "credit": None,
        "commonsFilePage": None,
        "attributionRequired": False,
        "wikipediaTitle": None,
    }


def art_sidecar(work_id: str, web_path: str, genre: str, artwork: WorkArtwork) -> dict[str, Any]:
    info = artwork.info
    return {
        "id": work_id,
        "cover": web_path,
        "source": "art",
        "genre": genre,
        "license": info.license,
        "licenseUrl": info.license_url,
        "artist": info.artist,
        "credit": info.credit,
        "commonsFilePage": info.commons_file_page,
        "attributionRequired": bool(info.attribution_required),
        "wikipediaTitle": artwork.wikipedia_title,
    }


def generate_work_cover(client: CachedWikimediaClient, work: dict[str, Any], refresh: bool) -> dict[str, Any]:
    work_id, cover_path, sidecar_path, web_path = work_paths(work)
    genre = classify_genre(work)

    if not refresh and cover_path.exists() and cover_path.stat().st_size > 0 and sidecar_path.exists():
        sidecar = read_json(sidecar_path)
        return {"id": work_id, "status": "skipped", "source": sidecar.get("source"), "genre": sidecar.get("genre", genre)}

    artwork = resolve_work_artwork(client, work)
    if artwork:
        download_url = best_download_url(artwork.image_url, artwork.info)
        data = client.get_image_bytes(download_url) if download_url else None
        if data and validate_image_bytes(data):
            try:
                render_art_cover(data, cover_path)
                sidecar = art_sidecar(work_id, web_path, genre, artwork)
                write_json(sidecar_path, sidecar)
                return {
                    "id": work_id,
                    "status": "ok",
                    "source": "art",
                    "genre": genre,
                    "license": artwork.info.license,
                    "wikipediaTitle": artwork.wikipedia_title,
                }
            except Exception as exc:  # noqa: BLE001 - fall through to themed cover
                print(f"  art render failed for {work_id}: {exc}; using themed fallback")

    render_themed_cover(work_id, genre, cover_path)
    write_json(sidecar_path, themed_sidecar(work_id, web_path, genre))
    return {"id": work_id, "status": "ok", "source": "themed", "genre": genre}


def select_works(catalog_works: list[dict[str, Any]], ids: list[str] | None, do_all: bool) -> list[dict[str, Any]]:
    if do_all:
        return catalog_works
    if not ids:
        return []
    wanted = {work_id.strip() for work_id in ids if work_id.strip()}
    by_id = {work["id"]: work for work in catalog_works}
    missing = [work_id for work_id in wanted if work_id not in by_id]
    for work_id in missing:
        print(f"  unknown work id (skipped): {work_id}")
    return [by_id[work_id] for work_id in by_id if work_id in wanted]


def run(ids: list[str] | None, do_all: bool, refresh: bool, catalog_path: Path = CATALOG) -> dict[str, Any]:
    catalog = read_json(catalog_path)
    works = catalog["works"] if isinstance(catalog, dict) else catalog
    selected = select_works(works, ids, do_all)
    COVER_DIR.mkdir(parents=True, exist_ok=True)

    client = CachedWikimediaClient()
    results: list[dict[str, Any]] = []
    counts = {"art": 0, "themed": 0, "skipped": 0, "error": 0}
    try:
        for work in selected:
            try:
                result = generate_work_cover(client, work, refresh)
            except Exception as exc:  # noqa: BLE001 - continue-on-error per work
                print(f"  ERROR {work.get('id')}: {exc}")
                results.append({"id": work.get("id"), "status": "error", "error": str(exc)})
                counts["error"] += 1
                continue
            results.append(result)
            if result["status"] == "skipped":
                counts["skipped"] += 1
            else:
                counts[result["source"]] = counts.get(result["source"], 0) + 1
            line = f"  {result['id']}: {result['status']} [{result.get('source', '-')}/{result.get('genre', '-')}]"
            if result.get("source") == "art":
                line += f" license={result.get('license')!r} wiki={result.get('wikipediaTitle')!r}"
            print(line)
    finally:
        client.close()

    return {
        "selected": len(selected),
        "art": counts["art"],
        "themed": counts["themed"],
        "skipped": counts["skipped"],
        "errors": counts["error"],
        "network_fetches": client.network_fetches,
        "cache_hits": client.cache_hits,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-work book cover images (PD artwork + themed fallback).")
    parser.add_argument("--works", help="Comma-separated work ids, e.g. 'Homer/iliad,Plato/republic'.")
    parser.add_argument("--all", action="store_true", help="Generate covers for every readable work in the catalog.")
    parser.add_argument("--refresh", action="store_true", help="Regenerate even if a cover and sidecar already exist.")
    args = parser.parse_args()

    if not args.all and not args.works:
        parser.error("provide --works <ids> or --all")

    ids = args.works.split(",") if args.works else None
    summary = run(ids, args.all, args.refresh)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
