"""Resolve Wikimedia portraits and attribution metadata for catalog authors.

The resolver is intentionally conservative about network access: API responses are
cached on disk, requests use a descriptive user agent, and already-downloaded
portrait bytes are reused on subsequent runs.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

import httpx
from PIL import Image

AUTHOR_CATALOG = Path("catalog/authors.json")
PORTRAIT_DIR = Path("assets/portraits")
CACHE_DIR = PORTRAIT_DIR / "_cache"
API_CACHE_DIR = CACHE_DIR / "api"

USER_AGENT = "ClassicsLibrary/1.0 (super.croox@gmail.com)"
REQUEST_PAUSE_SECONDS = 0.3
DOWNLOAD_PAUSE_SECONDS = 1.25
MAX_DOWNLOAD_ATTEMPTS = 4
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# The catalog key is used where the display name is ambiguous, archaic, or not
# the English Wikipedia title for the intended author.
TITLE_OVERRIDES: dict[str, str] = {
    "Antoninus": "Marcus Aurelius",
    "Apollodorus": "Pseudo-Apollodorus",
    "Apollonius": "Apollonius of Rhodes",
    "Antiphon": "Antiphon (orator)",
    "Carus": "Lucretius",
    "Diodorus": "Diodorus Siculus",
    "Hirtius": "Aulus Hirtius",
    "Hyperides": "Hypereides",
    "Khayyam": "Omar Khayyam",
    "Lao": "Laozi",
    "Lycurgus": "Lycurgus of Athens",
    "Pausanias": "Pausanias (geographer)",
    "Porphyry": "Porphyry (philosopher)",
    "Quintus": "Quintus Smyrnaeus",
    "Sadi": "Saadi Shirazi",
    "Tzu": "Sun Tzu",
}

# Manual Commons file choices are used only after the intended Wikipedia page is
# known and either has no page image or exposes a non-portrait/unsuitable one.
MANUAL_FILE_OVERRIDES: dict[str, str] = {
    "Apollonius": "File:Apollonius.png",
    "Lao": "File:Lao Tzu - Project Gutenberg eText 15250.jpg",
    "Quintus": "File:Quintus Smyrnaeus, Posthomerica, Vaticanus Ottobonianus graecus 103.jpg",
}

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

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class PageImage:
    key: str
    requested_title: str
    wikipedia_title: str | None
    image_url: str | None
    commons_file_title: str | None
    missing_reason: str | None = None


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


def ordered_author(record: dict[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for field in AUTHOR_FIELD_ORDER:
        if field in record:
            ordered[field] = record[field]
    for field, value in record.items():
        if field not in ordered:
            ordered[field] = value
    return ordered


def requested_title(author: dict[str, Any]) -> str:
    return TITLE_OVERRIDES.get(author["key"], author["name"])


def normalize_url_for_cache(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query_items = sorted(parse_qsl(parsed.query, keep_blank_values=True))
    query_items.extend(sorted(params.items()))
    return urlunparse(parsed._replace(query=urlencode(query_items, doseq=True)))


class CachedWikimediaClient:
    def __init__(self, cache_dir: Path = API_CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True)
        self.cache_hits = 0
        self.network_fetches = 0

    def close(self) -> None:
        self.client.close()

    def _cache_path(self, url: str, params: dict[str, str]) -> Path:
        normalized = normalize_url_for_cache(url, params)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        cache_path = self._cache_path(url, params)
        if cache_path.exists():
            self.cache_hits += 1
            return read_json(cache_path)["body"]

        response = self.client.get(url, params=params)
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

    def get_bytes(self, url: str) -> bytes:
        for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
            response = self.client.get(url)
            self.network_fetches += 1
            if response.status_code == 429 and attempt < MAX_DOWNLOAD_ATTEMPTS:
                retry_after = response.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 12 * attempt
                time.sleep(delay)
                continue
            response.raise_for_status()
            time.sleep(DOWNLOAD_PAUSE_SECONDS)
            return response.content
        raise RuntimeError(f"Unable to download {url}")


def batched(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def resolve_wikipedia_page_images(client: CachedWikimediaClient, authors: list[dict[str, Any]]) -> dict[str, PageImage]:
    titles_by_key = {author["key"]: requested_title(author) for author in authors}
    result: dict[str, PageImage] = {}

    for title_batch in batched(list(titles_by_key.values()), 50):
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
                "titles": "|".join(title_batch),
            },
        )
        query = response.get("query", {})
        normalized = {item["from"]: item["to"] for item in query.get("normalized", [])}
        redirects = {item["from"]: item["to"] for item in query.get("redirects", [])}
        pages = {page.get("title"): page for page in query.get("pages", [])}

        for key, title in titles_by_key.items():
            if title not in title_batch:
                continue
            normalized_title = normalized.get(title, title)
            final_title = redirects.get(normalized_title, normalized_title)
            page = pages.get(final_title)
            if not page or page.get("missing"):
                result[key] = PageImage(key, title, final_title, None, None, "missing page")
                continue
            if "disambiguation" in page.get("pageprops", {}):
                result[key] = PageImage(key, title, page.get("title"), None, None, "disambiguation")
                continue
            image = page.get("thumbnail") or page.get("original")
            image_url = image.get("source") if image else None
            if not image_url:
                result[key] = PageImage(key, title, page.get("title"), None, None, "no page image")
                continue
            file_title = commons_file_title_from_url(image_url)
            result[key] = PageImage(key, title, page.get("title"), image_url, file_title, None)

    return result


def commons_file_title_from_url(url: str) -> str:
    segments = [segment for segment in urlparse(url).path.split("/") if segment]
    if "thumb" in segments and len(segments) >= 2:
        filename = segments[-2]
    else:
        filename = segments[-1]
    return f"File:{unquote(filename)}"


def fetch_commons_imageinfo(client: CachedWikimediaClient, file_titles: list[str]) -> dict[str, CommonsImageInfo]:
    infos: dict[str, CommonsImageInfo] = {}
    unique_titles = sorted(set(file_titles))
    for title_batch in batched(unique_titles, 50):
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
                "titles": "|".join(title_batch),
            },
        )
        for page in response.get("query", {}).get("pages", []):
            title = page.get("title")
            imageinfo = (page.get("imageinfo") or [{}])[0]
            metadata = imageinfo.get("extmetadata", {})
            license_label = metadata_value(metadata, "LicenseShortName") or metadata_value(metadata, "UsageTerms")
            info = CommonsImageInfo(
                file_title=title,
                source_url=imageinfo.get("url"),
                thumb_url=imageinfo.get("thumburl"),
                commons_file_page=imageinfo.get("descriptionurl") or commons_page_for_title(title),
                license=license_label,
                license_url=metadata_value(metadata, "LicenseUrl"),
                artist=metadata_value(metadata, "Artist"),
                credit=metadata_value(metadata, "Credit"),
                attribution_required=metadata_value(metadata, "AttributionRequired") == "true",
            )
            infos[title] = info
            infos[title.replace(" ", "_")] = info
    return infos


def metadata_value(metadata: dict[str, Any], key: str) -> str | None:
    raw = metadata.get(key, {}).get("value")
    return html_to_text(raw)


def commons_page_for_title(file_title: str | None) -> str | None:
    if not file_title:
        return None
    return "https://commons.wikimedia.org/wiki/" + quote(file_title.replace(" ", "_"), safe="/:_(),.-")


def best_download_url(page_image: PageImage, info: CommonsImageInfo | None) -> str | None:
    candidates = [page_image.image_url, info.thumb_url if info else None, info.source_url if info else None]
    for url in candidates:
        if not url:
            continue
        suffix = suffix_from_url(url)
        if suffix in SUPPORTED_IMAGE_SUFFIXES:
            return url
    return None


def suffix_from_url(url: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if suffix in {".jpeg", ".jpe"}:
        return ".jpg"
    if not suffix:
        guessed = mimetypes.guess_extension(urlparse(url).path)
        suffix = guessed.lower() if guessed else ""
    return suffix


def validate_image_bytes(data: bytes) -> bool:
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


def download_portrait(
    client: CachedWikimediaClient,
    key: str,
    image_url: str,
    portrait_dir: Path = PORTRAIT_DIR,
) -> str | None:
    suffix = suffix_from_url(image_url)
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        return None
    destination = portrait_dir / f"{key}{suffix}"
    if destination.exists() and destination.stat().st_size > 0:
        return "/" + destination.as_posix()

    data = client.get_bytes(image_url)
    if not data or not validate_image_bytes(data):
        return None
    portrait_dir.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return "/" + destination.as_posix()


def apply_portrait_metadata(
    authors: list[dict[str, Any]],
    page_images: dict[str, PageImage],
    image_infos: dict[str, CommonsImageInfo],
    client: CachedWikimediaClient,
) -> tuple[list[dict[str, Any]], list[str]]:
    fallbacks: list[str] = []
    merged: list[dict[str, Any]] = []

    for author in authors:
        key = author["key"]
        page_image = page_images[key]
        info = image_infos.get(page_image.commons_file_title or "")
        image_url = best_download_url(page_image, info)
        portrait_path = download_portrait(client, key, image_url) if image_url and info else None

        if not portrait_path:
            fallbacks.append(key)

        updated = dict(author)
        updated["portrait"] = portrait_path
        updated.setdefault("cover", None)
        updated.setdefault("coverType", None)
        updated["wikipediaTitle"] = page_image.wikipedia_title
        updated["commonsFileTitle"] = info.file_title if info and portrait_path else None
        updated["commonsFilePage"] = info.commons_file_page if info and portrait_path else None
        updated["license"] = info.license if info and portrait_path else None
        updated["licenseUrl"] = info.license_url if info and portrait_path else None
        updated["artist"] = info.artist if info and portrait_path else None
        updated["credit"] = info.credit if info and portrait_path else None
        updated["attributionRequired"] = bool(info.attribution_required) if info and portrait_path else False
        updated.setdefault("fallback", None)
        merged.append(ordered_author(updated))

    return merged, fallbacks


def resolve_authors(authors_path: Path = AUTHOR_CATALOG) -> dict[str, Any]:
    authors = read_json(authors_path)
    PORTRAIT_DIR.mkdir(parents=True, exist_ok=True)
    client = CachedWikimediaClient()
    try:
        page_images = resolve_wikipedia_page_images(client, authors)
        for key, manual_file in MANUAL_FILE_OVERRIDES.items():
            if key not in page_images:
                continue
            current = page_images[key]
            manual_url_stub = commons_page_for_title(manual_file) or ""
            page_images[key] = PageImage(
                key=key,
                requested_title=current.requested_title,
                wikipedia_title=current.wikipedia_title,
                image_url=manual_url_stub,
                commons_file_title=manual_file,
                missing_reason=None,
            )

        file_titles = [page.commons_file_title for page in page_images.values() if page.commons_file_title]
        image_infos = fetch_commons_imageinfo(client, file_titles)
        # For manual files and non-raster page images, prefer the Commons thumbnail URL
        # returned by imageinfo; the placeholder image_url is used only for deriving
        # file titles before imageinfo is available.
        for key, page in list(page_images.items()):
            info = image_infos.get(page.commons_file_title or "")
            if info and page.commons_file_title in MANUAL_FILE_OVERRIDES.values():
                page_images[key] = PageImage(
                    key=key,
                    requested_title=page.requested_title,
                    wikipedia_title=page.wikipedia_title,
                    image_url=info.thumb_url or info.source_url,
                    commons_file_title=page.commons_file_title,
                    missing_reason=None,
                )

        merged, fallbacks = apply_portrait_metadata(authors, page_images, image_infos, client)
        write_json(authors_path, merged)
        return {
            "authors_total": len(authors),
            "portraits_resolved": sum(1 for author in merged if author.get("portrait")),
            "monogram_fallbacks": fallbacks,
            "network_fetches": client.network_fetches,
            "cache_hits": client.cache_hits,
        }
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve Wikimedia portrait images for catalog authors.")
    parser.parse_args()
    result = resolve_authors()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
