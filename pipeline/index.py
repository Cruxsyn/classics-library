"""Build public catalog indexes from normalized work JSON."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .common import CATALOG_DIR, NORMALIZED_DIR, now_iso, read_json, write_json
from .enumerate import AuthorRecord, WorkStub



PRESERVED_AUTHOR_FIELDS = (
    "portrait",
    "cover",
    "coverType",
    "license",
    "licenseUrl",
    "artist",
    "credit",
    "attributionRequired",
    "wikipediaTitle",
    "fallback",
)


def _preserved_author_assets(catalog_dir: Path) -> dict[str, dict[str, Any]]:
    authors_path = catalog_dir / "authors.json"
    if not authors_path.exists():
        return {}

    try:
        existing = read_json(authors_path)
    except (OSError, ValueError):
        return {}

    if not isinstance(existing, list):
        return {}

    preserved: dict[str, dict[str, Any]] = {}
    for record in existing:
        if not isinstance(record, dict):
            continue
        key = record.get("key")
        if not isinstance(key, str):
            continue
        fields: dict[str, Any] = {}
        for field, value in record.items():
            if field in PRESERVED_AUTHOR_FIELDS or field.startswith("commons"):
                fields[field] = value
        if fields:
            preserved[key] = fields
    return preserved


def _merge_author_assets(record: dict[str, Any], preserved: dict[str, dict[str, Any]]) -> dict[str, Any]:
    key = record.get("key")
    if not isinstance(key, str):
        return record
    fields = preserved.get(key)
    if not fields:
        return record
    merged = dict(record)
    merged.update(fields)
    return merged

def load_normalized(root: Path = NORMALIZED_DIR) -> list[dict[str, Any]]:
    works: list[dict[str, Any]] = []
    if not root.exists():
        return works
    for path in sorted(root.glob("*/*.json")):
        works.append(read_json(path))
    return works


def write_catalogs(
    works: list[dict[str, Any]],
    authors: list[AuthorRecord],
    enumerated_works: list[WorkStub],
    catalog_dir: Path = CATALOG_DIR,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    generated_at = now_iso()
    work_records = [_catalog_work_record(work) for work in sorted(works, key=lambda item: item["id"])]
    normalized_counts = Counter(work["authorKey"] for work in works)
    enumerated_counts = Counter(work.authorKey for work in enumerated_works)
    preserved_assets = _preserved_author_assets(catalog_dir)

    catalog_authors = [
        _catalog_author_record(author, normalized_counts[author.key])
        for author in authors
        if normalized_counts[author.key]
    ]
    authors_index = [
        _merge_author_assets(_authors_index_record(author, enumerated_counts[author.key]), preserved_assets)
        for author in sorted(authors, key=lambda item: item.key)
    ]

    catalog = {
        "version": 1,
        "generatedAt": generated_at,
        "works": work_records,
        "authors": catalog_authors,
    }
    write_json(catalog_dir / "catalog.json", catalog)
    write_json(catalog_dir / "authors.json", authors_index)
    return catalog, authors_index


def _catalog_work_record(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": work["id"],
        "title": work["title"],
        "author": work["author"],
        "authorKey": work["authorKey"],
        "translator": work.get("translator"),
        "language": work.get("language"),
        "written": work.get("written"),
        "shape": work["shape"],
        "sectionCount": work["stats"]["sectionCount"],
        "wordCount": work["stats"]["wordCount"],
        "cover": None,
        "readerUrl": f"/{work['authorKey']}/{_work_slug(work['id'])}/",
        "tags": [],
    }


def _catalog_author_record(author: AuthorRecord, work_count: int) -> dict[str, Any]:
    return {
        "key": author.key,
        "name": author.name,
        "language": author.language,
        "dates": author.dates,
        "workCount": work_count,
        "portrait": None,
        "blurb": None,
    }


def _authors_index_record(author: AuthorRecord, work_count: int) -> dict[str, Any]:
    return {
        "key": author.key,
        "name": author.name,
        "language": author.language,
        "dates": author.dates,
        "workCount": work_count,
        "portrait": None,
        "blurb": None,
    }


def _work_slug(work_id: str) -> str:
    return work_id.split("/", 1)[1]
