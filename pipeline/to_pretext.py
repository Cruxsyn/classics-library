"""Generate schema-valid PreTeXt sources for normalized marquee works."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from pipeline.common import CATALOG_DIR, NORMALIZED_DIR, normalize_unicode, slugify

ROOT_DIR = Path(__file__).resolve().parents[1]
PRETEXT_DIR = ROOT_DIR / "pretext"
SOURCE_DIR = PRETEXT_DIR / "source"
PUBLICATION_DIR = PRETEXT_DIR / "publication"
PROJECT_PATH = PRETEXT_DIR / "project.ptx"
OUTPUT_DIR = ROOT_DIR / "output"

XML_NS = "http://www.w3.org/XML/1998/namespace"
XML_ID = f"{{{XML_NS}}}id"
XML_LANG = f"{{{XML_NS}}}lang"
ET.register_namespace("xml", XML_NS)

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
STRUCTURAL_HEADING = re.compile(
    r"^(?:(?:book|part|chapter)\s+(?:[ivxlcdm]+|\d+)\b|(?:[ivxlcdm]+|\d+)\.\s+\S)",
    re.IGNORECASE,
)
PERSONA_LIST_TITLES = {"dramatis personae", "persons of the dialogue"}


@dataclass(frozen=True)
class WorkTarget:
    """A normalized work and the corresponding PreTeXt target metadata."""

    work_id: str
    author_key: str
    work_slug: str
    normalized_path: Path
    target_name: str
    source_rel: Path
    output_rel: Path
    publication: str


class IdFactory:
    """Generate stable XML ids unique within one PreTeXt source."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def make(self, *parts: object, fallback: str = "id") -> str:
        raw = "-".join(str(part) for part in parts if part is not None and str(part).strip())
        candidate = slugify(raw or fallback)
        if not re.match(r"[A-Za-z_]", candidate):
            candidate = f"id-{candidate}"
        base = candidate
        index = 2
        while candidate in self._used:
            candidate = f"{base}-{index}"
            index += 1
        self._used.add(candidate)
        return candidate


def clean_xml_text(value: object | None) -> str:
    """Normalize text and remove code points illegal in XML 1.0."""

    if value is None:
        return ""
    return CONTROL_CHARS.sub(" ", normalize_unicode(str(value))).strip()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_work_targets() -> list[WorkTarget]:
    """Return all normalized works in catalog order."""

    catalog_path = ROOT_DIR / CATALOG_DIR / "catalog.json"
    work_ids: list[str]
    if catalog_path.exists():
        catalog = load_json(catalog_path)
        work_ids = [work["id"] for work in catalog.get("works", [])]
    else:
        base = ROOT_DIR / NORMALIZED_DIR
        work_ids = [str(path.relative_to(base).with_suffix("")) for path in sorted(base.glob("*/*.json"))]

    targets: list[WorkTarget] = []
    for work_id in work_ids:
        author_key, work_slug = work_id.split("/", 1)
        normalized_path = ROOT_DIR / NORMALIZED_DIR / author_key / f"{work_slug}.json"
        if not normalized_path.exists():
            continue
        work = load_json(normalized_path)
        section_count = len(work.get("sections", []))
        publication = "per-chapter.ptx" if work.get("shape") == "multi" and section_count > 1 else "single-page.ptx"
        targets.append(
            WorkTarget(
                work_id=work_id,
                author_key=author_key,
                work_slug=work_slug,
                normalized_path=normalized_path,
                target_name=f"{author_key}-{work_slug}",
                source_rel=Path(author_key) / f"{work_slug}.ptx",
                output_rel=Path(author_key) / work_slug,
                publication=publication,
            )
        )
    return targets


def is_verse_work(work: dict[str, Any]) -> bool:
    if work.get("textMode") == "verse":
        return True
    return any(block.get("type") == "verseLine" for section in work.get("sections", []) for block in section.get("blocks", []))


def is_persona_heading(text: str) -> bool:
    cleaned = clean_xml_text(text)
    if not cleaned:
        return False
    if cleaned.lower() in PERSONA_LIST_TITLES:
        return True
    if " - " in cleaned:
        parts = [part.strip() for part in cleaned.split(" - ") if part.strip()]
        if 1 < len(parts) <= 8 and all(len(part.split()) <= 2 for part in parts):
            return True
    letters = [char for char in cleaned if char.isalpha()]
    if letters and not any(char.islower() for char in letters) and len(cleaned) <= 80:
        return True
    return False


def is_structural_heading(block: dict[str, Any], *, verse: bool) -> bool:
    if block.get("type") != "heading" or verse:
        return False
    text = clean_xml_text(block.get("text"))
    if is_persona_heading(text):
        return False
    return bool(STRUCTURAL_HEADING.match(text))


def block_has_structural_headings(blocks: Iterable[dict[str, Any]], *, verse: bool) -> bool:
    return any(is_structural_heading(block, verse=verse) for block in blocks)


def add_text(parent: ET.Element, tag: str, text: object | None, attrib: dict[str, str] | None = None) -> ET.Element:
    elem = ET.SubElement(parent, tag, attrib or {})
    elem.text = clean_xml_text(text)
    return elem


def add_title(parent: ET.Element, title: object | None) -> ET.Element:
    return add_text(parent, "title", clean_xml_text(title) or "Untitled")


def add_paragraph(parent: ET.Element, text: object | None, ids: IdFactory, base_id: str, counter: int) -> bool:
    cleaned = clean_xml_text(text)
    if not cleaned:
        return False
    add_text(parent, "p", cleaned, {XML_ID: ids.make(base_id, "p", counter)})
    return True


def add_emphasis_paragraph(parent: ET.Element, text: object | None, ids: IdFactory, base_id: str, counter: int) -> bool:
    cleaned = clean_xml_text(text)
    if not cleaned:
        return False
    paragraph = ET.SubElement(parent, "p", {XML_ID: ids.make(base_id, "head", counter)})
    emphasis = ET.SubElement(paragraph, "em")
    emphasis.text = cleaned
    return True


def add_speaker_paragraph(parent: ET.Element, block: dict[str, Any], ids: IdFactory, base_id: str, counter: int) -> bool:
    name = clean_xml_text(block.get("name"))
    speech = clean_xml_text(block.get("text"))
    if not name and not speech:
        return False
    paragraph = ET.SubElement(parent, "p", {XML_ID: ids.make(base_id, "speaker", counter)})
    if name:
        emphasis = ET.SubElement(paragraph, "em")
        emphasis.text = f"{name}."
        emphasis.tail = f" {speech}" if speech else ""
    else:
        paragraph.text = speech
    return True


def division_has_body(division: ET.Element) -> bool:
    return any(child.tag != "title" for child in division)


def title_text(division: ET.Element) -> str:
    title = division.find("title")
    return clean_xml_text(title.text if title is not None else "")


class BlockRenderer:
    """Append normalized blocks to one chapter/section-level container."""

    def __init__(self, parent: ET.Element, ids: IdFactory, base_id: str) -> None:
        self.parent = parent
        self.ids = ids
        self.base_id = base_id
        self.block_counter = 0
        self.poem_counter = 0
        self._verse_lines: list[dict[str, Any]] = []

    def render(self, blocks: Iterable[dict[str, Any]]) -> None:
        for block in blocks:
            block_type = block.get("type")
            if block_type == "verseLine":
                self._verse_lines.append(block)
                continue
            self.flush_verse()
            self.block_counter += 1
            if block_type == "para":
                add_paragraph(self.parent, block.get("text"), self.ids, self.base_id, self.block_counter)
            elif block_type == "heading":
                add_emphasis_paragraph(self.parent, block.get("text"), self.ids, self.base_id, self.block_counter)
            elif block_type == "speaker":
                add_speaker_paragraph(self.parent, block, self.ids, self.base_id, self.block_counter)
        self.flush_verse()

    def flush_verse(self) -> None:
        if not self._verse_lines:
            return
        self.poem_counter += 1
        poem = ET.SubElement(self.parent, "poem", {XML_ID: self.ids.make(self.base_id, "poem", self.poem_counter)})
        stanza: ET.Element | None = None
        stanza_index = 0
        for verse_line in self._verse_lines:
            text = clean_xml_text(verse_line.get("text"))
            if not text:
                stanza = None
                continue
            if stanza is None:
                stanza_index += 1
                stanza = ET.SubElement(poem, "stanza", {XML_ID: self.ids.make(self.base_id, "stanza", self.poem_counter, stanza_index)})
            attrib: dict[str, str] = {}
            indent = verse_line.get("indent")
            if isinstance(indent, int) and indent > 0:
                attrib["indent"] = str(indent)
            line = ET.SubElement(stanza, "line", attrib)
            line.text = text
        if stanza_index == 0:
            self.parent.remove(poem)
        self._verse_lines.clear()


def render_blocks(parent: ET.Element, blocks: list[dict[str, Any]], ids: IdFactory, base_id: str, *, verse: bool, section_title: str) -> None:
    """Render blocks directly, or as titled sections when structural headings exist."""

    if not block_has_structural_headings(blocks, verse=verse):
        BlockRenderer(parent, ids, base_id).render(blocks)
        return

    current_section: ET.Element | None = None
    current_id = ""
    current_renderer: BlockRenderer | None = None

    def ensure_section(title: str, raw_id: object) -> tuple[ET.Element, str, BlockRenderer]:
        section_id = ids.make(base_id, raw_id)
        section = ET.SubElement(parent, "section", {XML_ID: section_id})
        add_title(section, title)
        return section, section_id, BlockRenderer(section, ids, section_id)

    for block in blocks:
        if is_structural_heading(block, verse=verse):
            heading = clean_xml_text(block.get("text"))
            if current_section is not None and not division_has_body(current_section):
                existing = current_section.find("title")
                if existing is not None:
                    existing.text = f"{clean_xml_text(existing.text)}: {heading}"
                continue
            current_section, current_id, current_renderer = ensure_section(heading, heading)
            continue
        if current_section is None or current_renderer is None:
            current_section, current_id, current_renderer = ensure_section(section_title, "text")
        current_renderer.render([block])

    if current_section is not None and not division_has_body(current_section):
        dangling_title = title_text(current_section)
        parent.remove(current_section)
        if len(parent):
            previous = parent[-1]
            if previous.tag == "section":
                renderer = BlockRenderer(previous, ids, previous.get(XML_ID, base_id))
                renderer.render([{"type": "heading", "text": dangling_title}])


def chapter_title(section: dict[str, Any], fallback_seq: int) -> str:
    return clean_xml_text(section.get("heading") or section.get("partTitle")) or f"Book {fallback_seq}"


def add_chapter(book: ET.Element, ids: IdFactory, raw_id: object, title: str) -> tuple[ET.Element, str]:
    chapter_id = ids.make(raw_id)
    chapter = ET.SubElement(book, "chapter", {XML_ID: chapter_id})
    add_title(chapter, title)
    return chapter, chapter_id


def render_multi_work(book: ET.Element, work: dict[str, Any], ids: IdFactory, *, verse: bool) -> None:
    for fallback_seq, section in enumerate(work.get("sections", []), start=1):
        title = chapter_title(section, fallback_seq)
        raw_id = clean_xml_text(section.get("id")) or f"section-{fallback_seq}"
        chapter, chapter_id = add_chapter(book, ids, raw_id, title)
        render_blocks(chapter, section.get("blocks", []), ids, chapter_id, verse=verse, section_title=title)


def group_single_chapters(work: dict[str, Any], *, verse: bool) -> list[tuple[str, object, list[dict[str, Any]]]]:
    sections = work.get("sections", [])
    blocks: list[dict[str, Any]] = []
    for section in sections:
        blocks.extend(section.get("blocks", []))
    if verse or not block_has_structural_headings(blocks, verse=verse):
        section = sections[0] if sections else {}
        title = clean_xml_text(section.get("heading") or section.get("partTitle") or work.get("title")) or "Text"
        raw_id = clean_xml_text(section.get("id")) or slugify(title)
        return [(title, raw_id, blocks)]

    groups: list[tuple[str, object, list[dict[str, Any]]]] = []
    current_title = clean_xml_text(work.get("title")) or "Text"
    current_raw_id: object = "text"
    current_blocks: list[dict[str, Any]] = []
    started = False

    for block in blocks:
        if is_structural_heading(block, verse=verse):
            heading = clean_xml_text(block.get("text"))
            if started and not current_blocks:
                current_title = f"{current_title}: {heading}"
                current_raw_id = current_title
                continue
            if started or current_blocks:
                groups.append((current_title, current_raw_id, current_blocks))
            current_title = heading
            current_raw_id = heading
            current_blocks = []
            started = True
        else:
            current_blocks.append(block)
    if started or current_blocks or not groups:
        groups.append((current_title, current_raw_id, current_blocks))
    return groups


def render_single_work(book: ET.Element, work: dict[str, Any], ids: IdFactory, *, verse: bool) -> None:
    for index, (title, raw_id, blocks) in enumerate(group_single_chapters(work, verse=verse), start=1):
        chapter, chapter_id = add_chapter(book, ids, raw_id or f"chapter-{index}", title)
        render_blocks(chapter, blocks, ids, chapter_id, verse=verse, section_title=title)


def add_colophon(book: ET.Element, work: dict[str, Any], ids: IdFactory, book_id: str) -> None:
    rows = [f"Author: {clean_xml_text(work.get('author'))}"]
    translator = clean_xml_text(work.get("translator"))
    if translator:
        rows.append(f"Translated by {translator}")
    written = clean_xml_text(work.get("written"))
    if written:
        rows.append(f"Written: {written}")
    source_note = clean_xml_text(work.get("sourceNote"))
    if source_note:
        rows.append(source_note)
    if not any(row for row in rows):
        return
    backmatter = ET.SubElement(book, "backmatter")
    colophon = ET.SubElement(backmatter, "colophon", {XML_ID: ids.make(book_id, "colophon")})
    for index, row in enumerate(rows, start=1):
        add_text(colophon, "p", row, {XML_ID: ids.make(book_id, "colophon", "p", index)})


def work_to_pretext_tree(work: dict[str, Any]) -> ET.ElementTree:
    ids = IdFactory()
    root = ET.Element("pretext", {XML_LANG: "en-US"})
    book_id = ids.make(work.get("id") or work.get("title") or "book")
    book = ET.SubElement(root, "book", {XML_ID: book_id})
    add_title(book, work.get("title") or work.get("id") or "Untitled")

    verse = is_verse_work(work)
    if work.get("shape") == "multi":
        render_multi_work(book, work, ids, verse=verse)
    else:
        render_single_work(book, work, ids, verse=verse)
    add_colophon(book, work, ids, book_id)
    validate_pretext_tree(ET.ElementTree(root))
    return ET.ElementTree(root)


def validate_pretext_tree(tree: ET.ElementTree) -> None:
    root = tree.getroot()
    seen_ids: set[str] = set()
    for elem in root.iter():
        xml_id = elem.get(XML_ID)
        if xml_id:
            if xml_id in seen_ids:
                raise ValueError(f"duplicate xml:id {xml_id}")
            seen_ids.add(xml_id)
        if elem.tag in {"book", "part", "chapter", "section", "subsection", "subsubsection"}:
            title = elem.find("title")
            if title is None or not clean_xml_text(title.text):
                ident = xml_id or "<unidentified>"
                raise ValueError(f"division {elem.tag} {ident} is missing a title")


def write_tree(tree: ET.ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True, short_empty_elements=False)


def publication_xml(chunk_level: int) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<publication>
  <common>
    <chunking level="{chunk_level}" />
    <tableofcontents level="2" />
  </common>
  <source>
    <directories external="../assets" generated="../generated-assets" />
  </source>
  <numbering>
    <divisions level="1" />
    <blocks level="1" />
    <projects level="1" />
    <equations level="1" />
    <footnotes level="1" />
  </numbering>
  <html>
    <navigation logic="linear" upbutton="yes" />
    <search variant="none" />
  </html>
</publication>
'''


def write_publications() -> None:
    PUBLICATION_DIR.mkdir(parents=True, exist_ok=True)
    (PUBLICATION_DIR / "single-page.ptx").write_text(publication_xml(0), encoding="utf-8")
    (PUBLICATION_DIR / "per-chapter.ptx").write_text(publication_xml(1), encoding="utf-8")
    (PUBLICATION_DIR / "publication.ptx").write_text(publication_xml(1), encoding="utf-8")


def write_project(targets: list[WorkTarget]) -> None:
    PROJECT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<project ptx-version="2" source="source" publication="publication" output-dir="../output">',
        "  <targets>",
    ]
    for target in targets:
        lines.append(
            f'    <target name="{target.target_name}" format="html" source="{target.source_rel.as_posix()}" '
            f'publication="{target.publication}" output-dir="{target.output_rel.as_posix()}" />'
        )
    lines.extend(["  </targets>", "</project>", ""])
    PROJECT_PATH.write_text("\n".join(lines), encoding="utf-8")


def reset_source_dir() -> None:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    for child in SOURCE_DIR.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def generate_all() -> list[WorkTarget]:
    targets = iter_work_targets()
    reset_source_dir()
    for target in targets:
        work = load_json(target.normalized_path)
        tree = work_to_pretext_tree(work)
        write_tree(tree, SOURCE_DIR / target.source_rel)
    write_publications()
    write_project(targets)
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-targets", action="store_true", help="print target names in build order and exit")
    args = parser.parse_args(argv)

    targets = iter_work_targets()
    if args.print_targets:
        for target in targets:
            print(target.target_name)
        return 0

    targets = generate_all()
    print(f"Generated {len(targets)} PreTeXt source files in {SOURCE_DIR.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
