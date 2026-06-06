"""Shared helpers for the Internet Classics Archive pipeline."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

BASE_URL = "https://classics.mit.edu"
AUTHORS_URL = f"{BASE_URL}/Browse/authors.html"
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
NORMALIZED_DIR = DATA_DIR / "normalized"
CATALOG_DIR = Path("catalog")

USER_AGENT = (
    "ClassicsLibrary/1.0 "
    "(+https://classics.mit.edu reader; super.croox@gmail.com)"
)

LOG = logging.getLogger(__name__)

_SMART_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00ad": "",
        "\u2026": "...",
        "\xa0": " ",
    }
)

_WS_RE = re.compile(r"[ \t\r\f\v]+")


@dataclass(frozen=True)
class CachedResponse:
    """A raw response loaded from disk or fetched from the source site."""

    url: str
    content: bytes
    status_code: int
    headers: dict[str, str]
    cache_path: Path
    meta_path: Path
    from_cache: bool
    revalidated: bool = False

    @property
    def text(self) -> str:
        return decode_bytes(self.content)


class CachedHttpClient:
    """Sequential, polite, disk-cached HTTP client for classics.mit.edu."""

    def __init__(
        self,
        *,
        refresh: bool = False,
        raw_dir: Path = RAW_DIR,
        min_interval: float = 1.0,
    ) -> None:
        self.refresh = refresh
        self.raw_dir = raw_dir
        self.min_interval = min_interval
        self._last_request_at = 0.0
        self._client = httpx.Client(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": "text/html,text/plain,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CachedHttpClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def fetch(self, url: str) -> CachedResponse:
        """Fetch a URL, preferring the on-disk cache unless refresh is set."""

        absolute_url = urljoin(BASE_URL, url)
        cache_path = cache_path_for_url(absolute_url, self.raw_dir)
        meta_path = meta_path_for_cache(cache_path)

        if cache_path.exists() and not self.refresh:
            LOG.info("cache hit %s", absolute_url)
            return CachedResponse(
                url=absolute_url,
                content=cache_path.read_bytes(),
                status_code=_meta_status(meta_path, 200),
                headers=_meta_headers(meta_path),
                cache_path=cache_path,
                meta_path=meta_path,
                from_cache=True,
            )

        headers: dict[str, str] = {}
        if meta_path.exists() and cache_path.exists():
            meta = read_json(meta_path)
            if etag := meta.get("etag"):
                headers["If-None-Match"] = str(etag)
            if last_modified := meta.get("last_modified"):
                headers["If-Modified-Since"] = str(last_modified)

        response = self._request_with_retries(absolute_url, headers)
        if response.status_code == 304 and cache_path.exists():
            LOG.info("304 not modified %s", absolute_url)
            meta = _response_meta(absolute_url, response)
            meta.update({"cached_status": 304, "status": _meta_status(meta_path, 200)})
            write_json(meta_path, meta)
            return CachedResponse(
                url=absolute_url,
                content=cache_path.read_bytes(),
                status_code=_meta_status(meta_path, 200),
                headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                cache_path=cache_path,
                meta_path=meta_path,
                from_cache=True,
                revalidated=True,
            )

        if response.status_code >= 400:
            response.raise_for_status()

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
        write_json(meta_path, _response_meta(absolute_url, response))
        LOG.info("fetched %s status=%s bytes=%s", absolute_url, response.status_code, len(response.content))
        return CachedResponse(
            url=absolute_url,
            content=response.content,
            status_code=response.status_code,
            headers={str(k).lower(): str(v) for k, v in response.headers.items()},
            cache_path=cache_path,
            meta_path=meta_path,
            from_cache=False,
        )

    def _request_with_retries(self, url: str, headers: dict[str, str]) -> httpx.Response:
        backoff = 1.0
        last_exc: Exception | None = None
        for attempt in range(4):
            self._rate_limit()
            try:
                response = self._client.get(url, headers=headers)
                if response.status_code < 500:
                    return response
                LOG.warning("server error %s for %s; retrying", response.status_code, url)
            except httpx.HTTPError as exc:
                last_exc = exc
                LOG.warning("http error for %s: %s; retrying", url, exc)
            if attempt < 3:
                time.sleep(backoff)
                backoff *= 2
        if last_exc is not None:
            raise last_exc
        return response

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def cache_path_for_url(url: str, raw_dir: Path = RAW_DIR) -> Path:
    parsed = urlparse(urljoin(BASE_URL, url))
    host = parsed.netloc or "classics.mit.edu"
    raw_path = parsed.path.strip("/") or "index.html"
    parts = [quote(part, safe=".-_") for part in raw_path.split("/") if part]
    if not parts:
        parts = ["index.html"]
    path = raw_dir / host / Path(*parts)
    if parsed.query:
        digest = hashlib.sha256(parsed.query.encode("utf-8")).hexdigest()[:16]
        path = path.with_name(f"{path.name}.{digest}")
    return path


def meta_path_for_cache(cache_path: Path) -> Path:
    return cache_path.with_name(f"{cache_path.name}.meta.json")


def decode_bytes(data: bytes) -> str:
    """Decode source bytes defensively: UTF-8, then cp1252, then latin-1."""

    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return normalize_unicode(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return normalize_unicode(data.decode("latin-1", errors="replace"))


def normalize_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).translate(_SMART_TRANSLATION)


def clean_text(value: str) -> str:
    return normalize_ws(html.unescape(normalize_unicode(value)))


def normalize_ws(value: str) -> str:
    value = normalize_unicode(value).replace("\n", " ")
    return _WS_RE.sub(" ", value).strip()


def normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = clean_text(value)
    cleaned = re.sub(r"B\.C\.E\.?", "BCE", cleaned)
    cleaned = re.sub(r"A\.C\.E\.?", "CE", cleaned)
    cleaned = re.sub(r"C\.E\.?", "CE", cleaned)
    cleaned = re.sub(r"B\.C\.?", "BCE", cleaned)
    cleaned = re.sub(r"A\.D\.?", "CE", cleaned)
    cleaned = cleaned.replace("CE.-", "CE-").replace("BCE.-", "BCE-")
    cleaned = re.sub(r"\b(BCE|CE)\.", r"\1", cleaned)
    return cleaned or None


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", normalize_unicode(value))
    ascii_value = value.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value.lower()).strip("-")
    return slug or "untitled"


def word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?", value))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def source_url(path_or_url: str) -> str:
    return urljoin(BASE_URL, path_or_url)


def _response_meta(url: str, response: httpx.Response) -> dict[str, Any]:
    return {
        "url": url,
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
        "fetched_at": now_iso(),
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
    }


def _meta_status(meta_path: Path, default: int) -> int:
    if not meta_path.exists():
        return default
    try:
        return int(read_json(meta_path).get("status", default))
    except (ValueError, TypeError, json.JSONDecodeError):
        return default


def _meta_headers(meta_path: Path) -> dict[str, str]:
    if not meta_path.exists():
        return {}
    try:
        meta = read_json(meta_path)
    except json.JSONDecodeError:
        return {}
    headers: dict[str, str] = {}
    if meta.get("etag"):
        headers["etag"] = str(meta["etag"])
    if meta.get("last_modified"):
        headers["last-modified"] = str(meta["last_modified"])
    if meta.get("content_type"):
        headers["content-type"] = str(meta["content_type"])
    return headers
