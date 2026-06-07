"""Deduplicate the per-book PreTeXt ``_static`` trees into one shared copy.

PreTeXt copies a ~35MB, byte-identical ``_static`` directory into *every* book
output directory (``output/<Author>/<work>/_static/``). With 200+ books that
inflates ``output/`` to several GB of pure duplication. This step collapses all
of those into a single shared ``output/_static/`` and rewrites every book page to
reference the shared absolute path ``/_static/...`` instead of the book-relative
``_static/...``.

Only PreTeXt's own ``_static`` references are rewritten. Injected reader assets
(``/assets/...``, ``/reader-runtime.js``, ``/pagefind/...``) already use absolute
paths and never contain ``_static``, so they are untouched.

The step is idempotent: re-running it leaves an already-deduped ``output/`` alone
(pages that already reference ``/_static/`` are not modified, and the shared dir
is reused rather than recreated).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
SHARED_NAME = "_static"

# Match a *book-relative* PreTeXt static reference and capture the leading
# delimiter so we can re-emit it followed by a slash. The reference value must
# begin right at the attribute quote / ``url(`` (optionally with a ``./``)
# immediately followed by ``_static/``. A lookahead keeps ``_static/`` out of the
# consumed text, which makes the rewrite idempotent: an already-absolute
# ``/_static/`` ref has a ``/`` where the lookahead expects ``_``, so it never
# matches and is never turned into ``//_static/``.
#
# Forms handled (single or double quotes, optional whitespace, optional ``./``):
#   href="_static/...     src="_static/...     href='./_static/...
#   url(_static/...        url("_static/...      url('./_static/...
STATIC_REF_RE = re.compile(
    r"""(?P<pre>(?:href|src)\s*=\s*["']|url\(\s*["']?)(?:\./)?(?=_static/)""",
    re.IGNORECASE,
)


def human(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}TB"


def tree_stats(root: Path) -> tuple[int, int]:
    """Return (total_bytes, file_count) for every regular file under ``root``."""
    total = 0
    count = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
            count += 1
    return total, count


def shared_dir() -> Path:
    return OUTPUT_DIR / SHARED_NAME


def book_static_dirs() -> Iterator[Path]:
    """Yield each per-book ``_static`` dir (everything except the shared one)."""
    shared = shared_dir()
    for path in sorted(OUTPUT_DIR.rglob(SHARED_NAME)):
        if not path.is_dir():
            continue
        if path == shared or shared in path.parents:
            continue
        yield path


def consolidate(*, dry_run: bool = False) -> tuple[int, int]:
    """Collapse per-book ``_static`` dirs into ``output/_static``.

    Returns (dirs_removed, files_merged). The first per-book dir becomes the
    shared dir via a fast rename; remaining dirs are union-merged into it (any
    file whose relative path is missing from the shared copy is copied over —
    a no-op when the trees are identical, but robust if a book ever ships an
    extra asset) and then deleted.
    """
    shared = shared_dir()
    dirs = list(book_static_dirs())
    if not dirs:
        return 0, 0

    if not shared.exists():
        seed = dirs.pop(0)
        if dry_run:
            print(f"  would promote {seed.relative_to(OUTPUT_DIR)} -> {SHARED_NAME}/")
        else:
            shutil.move(str(seed), str(shared))

    dirs_removed = 0
    files_merged = 0
    for static_dir in dirs:
        if not dry_run:
            for src in static_dir.rglob("*"):
                if not src.is_file() or src.is_symlink():
                    continue
                dest = shared / src.relative_to(static_dir)
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    files_merged += 1
            shutil.rmtree(static_dir)
        dirs_removed += 1
    return dirs_removed, files_merged


def rewrite_page(text: str) -> str:
    return STATIC_REF_RE.sub(r"\g<pre>/", text)


def rewrite_html(*, dry_run: bool = False) -> tuple[int, int]:
    """Rewrite book-relative ``_static`` refs to ``/_static`` across all pages."""
    shared = shared_dir()
    pages = 0
    changed = 0
    for page in OUTPUT_DIR.rglob("*.html"):
        if shared in page.parents:  # never touch html inside the shared tree
            continue
        pages += 1
        original = page.read_text(encoding="utf-8")
        updated = rewrite_page(original)
        if updated != original:
            changed += 1
            if not dry_run:
                page.write_text(updated, encoding="utf-8")
    return pages, changed


def dedup(*, dry_run: bool = False) -> dict[str, Any]:
    if not OUTPUT_DIR.is_dir():
        raise SystemExit(f"output dir not found: {OUTPUT_DIR}")

    before_bytes, before_files = tree_stats(OUTPUT_DIR)
    print(f"before: {human(before_bytes)} ({before_bytes} bytes), {before_files} files")

    dirs_removed, files_merged = consolidate(dry_run=dry_run)
    pages_seen, pages_changed = rewrite_html(dry_run=dry_run)

    after_bytes, after_files = tree_stats(OUTPUT_DIR)
    print(f"after:  {human(after_bytes)} ({after_bytes} bytes), {after_files} files")
    print(
        f"removed {dirs_removed} per-book _static dir(s), merged {files_merged} extra file(s); "
        f"rewrote {pages_changed}/{pages_seen} page(s); "
        f"saved {human(max(before_bytes - after_bytes, 0))}"
    )

    return {
        "before_bytes": before_bytes,
        "before_files": before_files,
        "after_bytes": after_bytes,
        "after_files": after_files,
        "dirs_removed": dirs_removed,
        "files_merged": files_merged,
        "pages_seen": pages_seen,
        "pages_changed": pages_changed,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without moving dirs or editing pages",
    )
    args = parser.parse_args(argv)
    result = dedup(dry_run=args.dry_run)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
