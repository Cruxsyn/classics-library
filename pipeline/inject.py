"""Inject the shared reader runtime and stylesheet into built PreTeXt pages."""

from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
CATALOG_PATH = ROOT / "catalog" / "catalog.json"
TOKENS_SRC = ROOT / "web" / "styles" / "tokens.css"
LIBRARY_SRC = ROOT / "web" / "styles" / "library.css"
FONTS_SRC = ROOT / "assets" / "fonts"
READER_ASSET_DIR = OUTPUT_DIR / "assets" / "reader"
FONT_OUT_DIR = OUTPUT_DIR / "assets" / "fonts"
READER_MARK = "b5"
FONT_FILES = (
    "source-serif-4-latin-opsz-normal.woff2",
    "source-serif-4-latin-opsz-italic.woff2",
    "newsreader-latin-opsz-normal.woff2",
    "newsreader-latin-opsz-italic.woff2",
    "inter-latin-opsz-normal.woff2",
    "inter-latin-opsz-italic.woff2",
)
READER_HEAD_TAGS = "\n".join(
    [
        *(
            f'<link rel="preload" href="/assets/fonts/{font}" as="font" type="font/woff2" crossorigin data-reader-injected="{READER_MARK}">'
            for font in FONT_FILES
        ),
        f'<link rel="stylesheet" href="/assets/reader/tokens.css" data-reader-injected="{READER_MARK}">',
        f'<link rel="stylesheet" href="/assets/reader/library.css" data-reader-injected="{READER_MARK}">',
        f'<script defer src="/reader-runtime.js" data-reader-injected="{READER_MARK}"></script>',
    ]
)
INJECTED_TAG_RE = re.compile(
    r"\n?<(?:"
    r"link\b[^>]*(?:data-reader-injected=[\"']b5[\"']|/assets/reader/(?:tokens|library)\.css|/assets/fonts/(?:source-serif-4|newsreader|inter)-[^\"']+\.woff2)[^>]*>"
    r"|script\b[^>]*(?:data-reader-injected=[\"']b5[\"']|/reader-runtime\.js)[^>]*>\s*</script>"
    r")",
    re.IGNORECASE,
)
HTML_OPEN_RE = re.compile(r"<html\b([^>]*)>", re.IGNORECASE)
HEAD_CLOSE_RE = re.compile(r"</head>", re.IGNORECASE)
DATA_ATTR_RE_TEMPLATE = r"\s+{name}(?:=(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?"


@dataclass(frozen=True)
class Work:
    id: str
    title: str


@dataclass(frozen=True)
class InjectResult:
    pages_seen: int
    pages_changed: int
    books_seen: int
    missing_books: tuple[str, ...]


def read_catalog() -> list[Work]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    works: list[Work] = []
    for raw in data.get("works", []):
        if not isinstance(raw, dict):
            continue
        work_id = raw.get("id")
        title = raw.get("title")
        if isinstance(work_id, str) and isinstance(title, str):
            works.append(Work(id=work_id, title=title))
    return sorted(works, key=lambda work: work.id)


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def copy_file_if_changed(src: Path, dst: Path) -> bool:
    content = src.read_bytes()
    if dst.exists() and dst.read_bytes() == content:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(content)
    shutil.copystat(src, dst)
    return True


def copy_assets() -> None:
    READER_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    FONT_OUT_DIR.mkdir(parents=True, exist_ok=True)

    tokens = TOKENS_SRC.read_text(encoding="utf-8").replace(
        "../../assets/fonts/",
        "/assets/fonts/",
    )
    write_if_changed(READER_ASSET_DIR / "tokens.css", tokens)
    write_if_changed(READER_ASSET_DIR / "library.css", LIBRARY_SRC.read_text(encoding="utf-8"))

    for font in FONT_FILES:
        copy_file_if_changed(FONTS_SRC / font, FONT_OUT_DIR / font)


def escaped_attr(value: str) -> str:
    return html.escape(value, quote=True)


def upsert_data_attr(attrs: str, name: str, value: str) -> str:
    attr_re = re.compile(DATA_ATTR_RE_TEMPLATE.format(name=re.escape(name)), re.IGNORECASE)
    cleaned = attr_re.sub("", attrs).rstrip()
    return f'{cleaned} {name}="{escaped_attr(value)}"'


def inject_html_attrs(document: str, work: Work) -> str:
    def replace(match: re.Match[str]) -> str:
        attrs = match.group(1)
        attrs = upsert_data_attr(attrs, "data-reader-book", work.id)
        attrs = upsert_data_attr(attrs, "data-reader-title", work.title)
        return f"<html{attrs}>"

    return HTML_OPEN_RE.sub(replace, document, count=1)


def inject_head_tags(document: str) -> str:
    stripped = INJECTED_TAG_RE.sub("", document)
    return HEAD_CLOSE_RE.sub(f"{READER_HEAD_TAGS}\n</head>", stripped, count=1)


def inject_page(path: Path, work: Work) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = inject_html_attrs(original, work)
    updated = inject_head_tags(updated)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def inject_all() -> InjectResult:
    copy_assets()
    pages_seen = 0
    pages_changed = 0
    books_seen = 0
    missing: list[str] = []

    for work in read_catalog():
        book_dir = OUTPUT_DIR / work.id
        if not book_dir.is_dir():
            missing.append(work.id)
            continue
        books_seen += 1
        for page in sorted(book_dir.rglob("*.html")):
            pages_seen += 1
            if inject_page(page, work):
                pages_changed += 1

    return InjectResult(
        pages_seen=pages_seen,
        pages_changed=pages_changed,
        books_seen=books_seen,
        missing_books=tuple(missing),
    )


def main() -> None:
    result = inject_all()
    payload: dict[str, Any] = {
        "pages_seen": result.pages_seen,
        "pages_changed": result.pages_changed,
        "books_seen": result.books_seen,
        "missing_books": list(result.missing_books),
    }
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
