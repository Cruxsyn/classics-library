"""Command-line crawler/normalizer for classics.mit.edu."""

from __future__ import annotations

import argparse
import logging
from typing import Iterable

from .common import NORMALIZED_DIR, CachedHttpClient, write_json
from .enumerate import AuthorRecord, WorkStub, enumerate_all
from .index import write_catalogs
from .parse import build_normalized_work
from .resolve import resolve_work

LOG = logging.getLogger(__name__)

MARQUEE_REQUESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Homer", ("Iliad",)),
    ("Homer", ("Odyssey",)),
    ("Plato", ("Republic",)),
    ("Plato", ("Apology",)),
    ("Plato", ("Symposium",)),
    ("Aristotle", ("Poetics",)),
    ("Aristotle", ("Nicomachean Ethics",)),
    ("Aristotle", ("Politics",)),
    ("Tzu", ("Art of War",)),
    ("Confucius", ("Analects",)),
    ("Antoninus", ("Meditations",)),
    ("Sophocles", ("Oedipus",)),
    ("Aeschylus", ("Agamemnon",)),
    ("Euripides", ("Medea",)),
    ("Virgil", ("Aeneid",)),
    ("Herodotus", ("The History", "History")),
    ("Aristophanes", ("Frogs", "Birds")),
    ("Thucydides", ("Peloponnesian War",)),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marquee", action="store_true", help="crawl the 18-work validation subset")
    parser.add_argument("--authors", help="comma-separated author keys or display names to crawl")
    parser.add_argument("--limit", type=int, help="limit selected works after filtering")
    parser.add_argument("--all", action="store_true", help="crawl all enumerated works")
    parser.add_argument("--refresh", action="store_true", help="revalidate/refetch cached raw responses")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(levelname)s %(name)s: %(message)s")

    with CachedHttpClient(refresh=args.refresh) as client:
        authors, all_works = enumerate_all(client)
        selected, skipped = select_works(
            authors=authors,
            works=all_works,
            all_mode=args.all,
            authors_arg=args.authors,
            marquee=args.marquee or (not args.all and not args.authors),
            limit=args.limit,
        )
        LOG.info("selected %s works", len(selected))
        for message in skipped:
            LOG.warning("selection skip: %s", message)

        _clear_normalized_json()
        normalized: list[dict] = []
        oddities = list(skipped)
        for work in selected:
            try:
                resolved = resolve_work(client, work)
                normalized_work = build_normalized_work(client, work, resolved)
                _write_normalized(normalized_work)
                normalized.append(normalized_work)
                LOG.info(
                    "normalized %s sections=%s words=%s",
                    work.id,
                    normalized_work["stats"]["sectionCount"],
                    normalized_work["stats"]["wordCount"],
                )
            except Exception as exc:  # noqa: BLE001 - one bad source work must not fail the crawl.
                message = f"{work.id}: {exc}"
                oddities.append(message)
                LOG.exception("failed to normalize %s", work.id)

        catalog, authors_index = write_catalogs(normalized, authors, all_works)
        LOG.info(
            "wrote catalog works=%s represented_authors=%s authors_index=%s oddities=%s",
            len(catalog["works"]),
            len(catalog["authors"]),
            len(authors_index),
            len(oddities),
        )
        if oddities:
            for oddity in oddities:
                LOG.warning("known oddity: %s", oddity)
    return 0


def select_works(
    *,
    authors: list[AuthorRecord],
    works: list[WorkStub],
    all_mode: bool,
    authors_arg: str | None,
    marquee: bool,
    limit: int | None,
) -> tuple[list[WorkStub], list[str]]:
    del marquee  # marquee is the default whenever all/authors are not selected.
    skipped: list[str] = []
    selected: list[WorkStub]

    if all_mode:
        selected = list(works)
    elif authors_arg:
        keys = _author_keys_from_arg(authors_arg, authors)
        selected = [work for work in works if work.authorKey in keys]
        missing = [item for item in _split_csv(authors_arg) if _resolve_author_key(item, authors) is None]
        skipped.extend(f"author not resolved: {item}" for item in missing)
    else:
        selected = []
        for author_key, candidates in MARQUEE_REQUESTS:
            match = _find_marquee_work(works, author_key, candidates)
            if match is None:
                skipped.append(f"{author_key}: no title matching {', '.join(candidates)}")
            else:
                selected.append(match)

    deduped: list[WorkStub] = []
    seen: set[str] = set()
    for work in selected:
        if work.id in seen:
            continue
        seen.add(work.id)
        deduped.append(work)
    if limit is not None:
        deduped = deduped[: max(limit, 0)]
    return deduped, skipped


def _find_marquee_work(
    works: Iterable[WorkStub],
    author_key: str,
    candidates: tuple[str, ...],
) -> WorkStub | None:
    author_works = [work for work in works if work.authorKey.lower() == author_key.lower()]
    for candidate in candidates:
        matches = [work for work in author_works if _title_matches(work.title, candidate)]
        if matches:
            return sorted(matches, key=lambda work: (len(work.title), work.title))[0]
    return None


def _title_matches(title: str, candidate: str) -> bool:
    title_lower = title.lower()
    candidate_lower = candidate.lower()
    if candidate_lower in title_lower:
        return True
    tokens = [token for token in candidate_lower.replace("'", " ").split() if token]
    return bool(tokens) and all(token in title_lower for token in tokens)


def _author_keys_from_arg(value: str, authors: list[AuthorRecord]) -> set[str]:
    keys: set[str] = set()
    for item in _split_csv(value):
        if key := _resolve_author_key(item, authors):
            keys.add(key)
    return keys


def _resolve_author_key(value: str, authors: list[AuthorRecord]) -> str | None:
    value_lower = value.strip().lower()
    for author in authors:
        if author.key.lower() == value_lower or author.name.lower() == value_lower:
            return author.key
    return None


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _clear_normalized_json() -> None:
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    for path in NORMALIZED_DIR.glob("*/*.json"):
        path.unlink()
    for child in NORMALIZED_DIR.iterdir():
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()


def _write_normalized(work: dict) -> None:
    author_dir = NORMALIZED_DIR / str(work["authorKey"])
    output_path = author_dir / f"{work['id'].split('/', 1)[1]}.json"
    write_json(output_path, work)


if __name__ == "__main__":
    raise SystemExit(main())
