"""Enumerate Internet Classics Archive authors and work stubs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .common import AUTHORS_URL, BASE_URL, CachedHttpClient, clean_text, normalize_date, source_url

LOG = logging.getLogger(__name__)
_AUTHOR_HREF_RE = re.compile(r"(?:^|/)(?:Browse/)?browse-([A-Za-z]+)\.html$", re.I)

_WORK_HREF_RE = re.compile(r"^/([A-Za-z]+)/([^/?#]+)\.html$")
_EXCLUDED_PREFIXES = ("/Buy/", "/Help/", "/Search/", "/Images/", "/Browse/")


@dataclass(frozen=True)
class AuthorRecord:
    key: str
    name: str
    language: str | None
    dates: str | None
    browseUrl: str

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class WorkStub:
    id: str
    authorKey: str
    author: str
    title: str
    workSlug: str
    href: str
    landingUrl: str
    written: str | None
    translator: str | None
    sourceNote: str | None
    language: str | None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def enumerate_authors(client: CachedHttpClient) -> list[AuthorRecord]:
    response = client.fetch(AUTHORS_URL)
    authors = parse_authors_html(response.text, AUTHORS_URL)
    LOG.info("enumerated %s authors", len(authors))
    return authors


def enumerate_works(client: CachedHttpClient, author: AuthorRecord) -> list[WorkStub]:
    response = client.fetch(author.browseUrl)
    works = parse_author_works_html(response.text, author)
    LOG.info("enumerated %s works for %s", len(works), author.key)
    return works


def enumerate_all(client: CachedHttpClient) -> tuple[list[AuthorRecord], list[WorkStub]]:
    authors = enumerate_authors(client)
    works: list[WorkStub] = []
    for author in authors:
        works.extend(enumerate_works(client, author))
    return authors, works


def parse_authors_html(html: str, base_url: str = AUTHORS_URL) -> list[AuthorRecord]:
    soup = BeautifulSoup(html, "lxml")
    authors: list[AuthorRecord] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = str(link.get("href", ""))
        match = _AUTHOR_HREF_RE.search(href)
        if not match:
            continue
        key = match.group(1)
        if key in seen:
            continue
        seen.add(key)
        name = clean_text(link.get_text(" "))
        metadata = _sibling_text_until_next_author(link)
        language = _match_optional(r"Wrote\s+in\s+([^\n]+?)(?:\s{2,}|$)", metadata)
        if language is None:
            language = _match_optional(r"Wrote\s+in\s+([A-Za-z]+)", metadata)
        dates = metadata
        if language:
            dates = re.sub(r"Wrote\s+in\s+" + re.escape(language), "", dates, flags=re.I)
        dates = clean_text(dates)
        dates = dates if dates and not dates.lower().startswith("wrote in") else None
        authors.append(
            AuthorRecord(
                key=key,
                name=name,
                language=clean_text(language) if language else None,
                dates=normalize_date(dates),
                browseUrl=urljoin(base_url, f"browse-{key}.html"),
            )
        )
    return authors


def parse_author_works_html(html: str, author: AuthorRecord) -> list[WorkStub]:
    soup = BeautifulSoup(html, "lxml")
    works: list[WorkStub] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = str(link.get("href", ""))
        if href.startswith(_EXCLUDED_PREFIXES):
            continue
        parsed = urlparse(urljoin(BASE_URL, href))
        path = parsed.path
        match = _WORK_HREF_RE.match(path)
        if not match:
            continue
        href_author, filename = match.groups()
        if href_author != author.key:
            continue
        if path in seen:
            continue
        seen.add(path)
        title = _title_from_work_link(link)
        metadata = _work_metadata_text(link)
        written, translator, source_note = _parse_work_metadata(metadata)
        work_slug = PurePosixPath(filename).name
        works.append(
            WorkStub(
                id=f"{author.key}/{work_slug}",
                authorKey=author.key,
                author=author.name,
                title=title,
                workSlug=work_slug,
                href=path,
                landingUrl=source_url(path),
                written=written,
                translator=translator,
                sourceNote=source_note,
                language=author.language,
            )
        )
    return works


def authors_by_key(authors: Iterable[AuthorRecord]) -> dict[str, AuthorRecord]:
    return {author.key: author for author in authors}


def works_by_author(works: Iterable[WorkStub]) -> dict[str, list[WorkStub]]:
    grouped: dict[str, list[WorkStub]] = {}
    for work in works:
        grouped.setdefault(work.authorKey, []).append(work)
    return grouped


def _sibling_text_until_next_author(link: Tag) -> str:
    chunks: list[str] = []
    for sibling in link.next_siblings:
        if isinstance(sibling, Tag):
            if sibling.name == "a" and _AUTHOR_HREF_RE.search(str(sibling.get("href", ""))):
                break
            chunks.append(sibling.get_text("\n", strip=True))
        else:
            chunks.append(str(sibling))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _title_from_work_link(link: Tag) -> str:
    underlined = link.find("u")
    if underlined is not None:
        return clean_text(underlined.get_text(" "))
    return clean_text(link.get_text(" "))


def _work_metadata_text(link: Tag) -> str:
    for sibling in link.next_siblings:
        if isinstance(sibling, Tag):
            if sibling.name == "a":
                break
            if sibling.name == "font":
                return sibling.get_text("\n", strip=True)
        elif str(sibling).strip():
            continue
    return ""


def _parse_work_metadata(metadata: str) -> tuple[str | None, str | None, str | None]:
    lines = [clean_text(line) for line in metadata.splitlines()]
    lines = [line for line in lines if line]
    written: str | None = None
    translator: str | None = None
    notes: list[str] = []
    for line in lines:
        if match := re.search(r"Written\s+(.+)", line, flags=re.I):
            written = normalize_date(match.group(1))
        elif match := re.search(r"Translated\s+by\s+(.+)", line, flags=re.I):
            translator = clean_text(match.group(1))
        else:
            notes.append(line)
    source_note = clean_text(" ".join(notes)) if notes else None
    return written, translator, source_note or None


def _match_optional(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.I)
    return clean_text(match.group(1)) if match else None
