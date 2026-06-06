"""Resolve work landing pages into text-page shapes and section URLs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .common import CachedHttpClient, clean_text
from .enumerate import WorkStub

LOG = logging.getLogger(__name__)

_LINE_ANCHOR_RE = re.compile(r"<a\s+name=[\"']\d+[\"']\s*>\s*</a>", re.I)
_TXT_RE = re.compile(r"\.txt(?:$|[?#])", re.I)


@dataclass(frozen=True)
class SectionLink:
    seq: int
    href: str
    url: str
    label: str

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedWork:
    shape: str
    landingUrl: str
    textUrls: list[str]
    txtUrl: str | None
    sections: list[SectionLink]

    def to_dict(self) -> dict[str, object]:
        return {
            "shape": self.shape,
            "landingUrl": self.landingUrl,
            "textUrls": self.textUrls,
            "txtUrl": self.txtUrl,
            "sections": [section.to_dict() for section in self.sections],
        }


def resolve_work(client: CachedHttpClient, work: WorkStub) -> ResolvedWork:
    response = client.fetch(work.landingUrl)
    resolved = parse_landing_html(response.text, work.landingUrl, work.workSlug)
    LOG.info(
        "resolved %s as %s with %s text pages",
        work.id,
        resolved.shape,
        len(resolved.textUrls),
    )
    return resolved


def parse_landing_html(html: str, landing_url: str, work_slug: str) -> ResolvedWork:
    soup = BeautifulSoup(html, "lxml")
    txt_url = _find_txt_url(soup, landing_url)

    if _LINE_ANCHOR_RE.search(html):
        return ResolvedWork(
            shape="single",
            landingUrl=landing_url,
            textUrls=[landing_url],
            txtUrl=txt_url,
            sections=[],
        )

    sections = _section_links(soup, landing_url, work_slug)
    return ResolvedWork(
        shape="multi",
        landingUrl=landing_url,
        textUrls=[section.url for section in sections],
        txtUrl=txt_url,
        sections=sections,
    )


def _section_links(soup: BeautifulSoup, landing_url: str, work_slug: str) -> list[SectionLink]:
    pattern = re.compile(rf"^{re.escape(work_slug)}\.(\d+)\.[^/\"']+\.html$", re.I)
    found: dict[str, SectionLink] = {}
    order = 0
    for link in soup.find_all("a", href=True):
        href = str(link.get("href", ""))
        match = pattern.match(href)
        if not match or href in found:
            continue
        order += 1
        seq = int(match.group(1))
        found[href] = SectionLink(
            seq=seq,
            href=href,
            url=urljoin(landing_url, href),
            label=clean_text(link.get_text(" ")),
        )
    return sorted(found.values(), key=lambda section: (section.seq, section.href))


def _find_txt_url(soup: BeautifulSoup, landing_url: str) -> str | None:
    txt_links: list[str] = []
    for link in soup.find_all("a", href=True):
        href = str(link.get("href", ""))
        if _TXT_RE.search(href):
            txt_links.append(urljoin(landing_url, href))
    return txt_links[-1] if txt_links else None
