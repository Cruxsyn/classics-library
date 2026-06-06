"""Parse Internet Classics Archive text pages into normalized block JSON."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Literal

from bs4 import BeautifulSoup

from .common import CachedHttpClient, clean_text, slugify, word_count
from .enumerate import WorkStub
from .resolve import ResolvedWork, SectionLink

# Raw bytes are decoded by common.decode_bytes before parsing:
# utf-8 first, then cp1252, then latin-1 for legacy smart punctuation pages.

LOG = logging.getLogger(__name__)

_START_RE = re.compile(r"<a\s+name=[\"']start[\"']\s*>\s*</a>", re.I)
_END_RE = re.compile(r"<a\s+name=[\"']end[\"']\s*>\s*</a>", re.I)
_PART_TITLE_RE = re.compile(r"<!--\s*PART_TITLE:\s*(.*?)\s*-->", re.I | re.S)
_TOKEN_RE = re.compile(
    r"<a\s+name=[\"'](\d+)[\"']\s*>\s*</a>|(<br\s*/?>)|<b\b[^>]*>(.*?)</b>|<[^>]+>|([^<]+)",
    re.I | re.S,
)
_SPEAKER_RE = re.compile(r"^([A-Z][A-Z .'-]{1,32})(?::|\.)\s+(.+)$")
_WORDISH_RE = re.compile(r"[A-Za-z]")
_NUMERIC_LABEL_RE = re.compile(r"^\d+(?:,\d+)?\.$")
_ROMAN_RE = re.compile(r"^Book\s+([IVXLCDM]+)$", re.I)
_SECTION_NUM_RE = re.compile(r"^Section\s+(\d+)$", re.I)

_TERMINAL_END_CHARS = tuple('.!?:;"\'')
_DIALOGUE_DASH_HEADING_RE = re.compile(
    r"^[A-Z][A-Za-z'.-]*(?:\s+[A-Z][A-Za-z'.-]*){0,3}"
    r"(?:\s+-\s+[A-Z][A-Za-z'.-]*(?:\s+[A-Z][A-Za-z'.-]*){0,3}){1,8}$"
)


@dataclass(frozen=True)
class TextModeDecision:
    mode: Literal["prose", "verse"]
    reason: str
    stats: dict[str, Any]


def verse_work_ids(works: list[dict[str, Any]]) -> list[str]:
    return [work["id"] for work in works if any(_block_type(work, "verseLine"))]


def build_normalized_work(
    client: CachedHttpClient,
    work: WorkStub,
    resolved: ResolvedWork,
) -> dict[str, Any]:
    pages: list[tuple[int, str, str, str]] = []

    if resolved.shape == "single":
        html = client.fetch(resolved.textUrls[0]).text
        pages.append((1, work.title, work.title, html))
    else:
        by_url = {section.url: section for section in resolved.sections}
        for url in resolved.textUrls:
            section_link = by_url[url]
            html = client.fetch(url).text
            pages.append((section_link.seq, section_link.label, section_link.label, html))

    line_event_pages = [_line_events(extract_body_fragment(html)) for _seq, _heading, _part_title, html in pages]
    text_mode = detect_text_mode(line_event_pages)
    is_verse = text_mode.mode == "verse"
    sections: list[dict[str, Any]] = []

    for seq, fallback_heading, fallback_part_title, html in pages:
        parsed = parse_text_page(html, is_verse=is_verse, section_heading=fallback_heading)
        sections.append(
            _section_record(
                work_slug=work.workSlug,
                seq=seq,
                heading=parsed["heading"] or fallback_heading,
                part_title=parsed["partTitle"] or parsed["heading"] or fallback_part_title,
                blocks=parsed["blocks"],
            )
        )

    sections.sort(key=lambda section: int(section["seq"]))
    stats = _stats(sections)
    _cross_check_txt(client, resolved.txtUrl, stats, work.id)

    return {
        "id": work.id,
        "author": work.author,
        "authorKey": work.authorKey,
        "title": work.title,
        "translator": work.translator,
        "language": work.language,
        "written": work.written,
        "sourceNote": work.sourceNote,
        "shape": resolved.shape,
        "textMode": text_mode.mode,
        "textModeReason": text_mode.reason,
        "textModeStats": text_mode.stats,
        "landingUrl": work.landingUrl,
        "textUrls": resolved.textUrls,
        "txtUrl": resolved.txtUrl,
        "sections": sections,
        "stats": stats,
    }


def parse_text_page(
    html: str,
    *,
    is_verse: bool | None = None,
    section_heading: str | None = None,
) -> dict[str, Any]:
    part_title = extract_part_title(html)
    body = extract_body_fragment(html)
    line_events = _line_events(body)
    if is_verse is None:
        text_mode = detect_text_mode([line_events])
        mode = text_mode.mode
        reason = text_mode.reason
    else:
        mode = "verse" if is_verse else "prose"
        reason = "forced by caller"
    blocks = _blocks_for_mode(line_events, mode)
    return {
        "heading": part_title or section_heading,
        "partTitle": part_title,
        "textMode": mode,
        "textModeReason": reason,
        "blocks": blocks,
    }


def detect_text_mode(
    line_event_pages: list[list[tuple[int, list[tuple[str, str]]]]],
) -> TextModeDecision:
    stats = _text_mode_stats(line_event_pages)
    interior_count = stats["interiorLineCount"]
    if interior_count < 20:
        return TextModeDecision(
            "prose",
            f"ambiguous: only {interior_count} interior citation lines; defaulted to prose",
            stats,
        )

    long_unterminated = stats["longUnterminatedInteriorLineRatio"]
    median_length = stats["medianLineLength"]
    unmarked = stats["unmarkedLineRatio"]
    explicit_breaks = stats["explicitBreakLineRatio"]

    hard_wrapped = long_unterminated >= 0.50 and median_length >= 55 and unmarked >= 0.60
    strongly_wrapped = long_unterminated >= 0.70 and median_length >= 50
    if hard_wrapped or strongly_wrapped:
        return TextModeDecision(
            "prose",
            "hard-wrapped prose: "
            f"{long_unterminated:.3f} long unterminated interior lines, "
            f"median line length {median_length:.1f}, "
            f"{unmarked:.3f} unmarked continuation lines",
            stats,
        )

    if long_unterminated <= 0.25 and median_length <= 52 and explicit_breaks >= 0.25:
        return TextModeDecision(
            "verse",
            "verse: "
            f"{long_unterminated:.3f} long unterminated interior lines, "
            f"median line length {median_length:.1f}, "
            f"{explicit_breaks:.3f} explicit poetic line breaks",
            stats,
        )

    return TextModeDecision(
        "prose",
        "ambiguous: "
        f"{long_unterminated:.3f} long unterminated interior lines, "
        f"median line length {median_length:.1f}, "
        f"{explicit_breaks:.3f} explicit breaks; defaulted to prose",
        stats,
    )


def _text_mode_stats(
    line_event_pages: list[list[tuple[int, list[tuple[str, str]]]]],
) -> dict[str, Any]:
    line_lengths: list[int] = []
    line_count = 0
    paragraph_count = 0
    interior_count = 0
    long_interior_count = 0
    unterminated_interior_count = 0
    long_unterminated_interior_count = 0
    explicit_break_count = 0
    unmarked_count = 0

    for line_events in line_event_pages:
        for paragraph in _text_paragraphs(line_events):
            paragraph_count += 1
            for index, (_line_no, text, leading_breaks) in enumerate(paragraph):
                line_count += 1
                if leading_breaks == 1:
                    explicit_break_count += 1
                elif leading_breaks == 0:
                    unmarked_count += 1

                length = len(text.rstrip())
                line_lengths.append(length)
                if index == len(paragraph) - 1:
                    continue

                interior_count += 1
                long_line = 55 <= length <= 90
                unterminated = not text.rstrip().endswith(_TERMINAL_END_CHARS)
                if long_line:
                    long_interior_count += 1
                if unterminated:
                    unterminated_interior_count += 1
                if long_line and unterminated:
                    long_unterminated_interior_count += 1

    return {
        "lineCount": line_count,
        "paragraphCount": paragraph_count,
        "interiorLineCount": interior_count,
        "medianLineLength": _median(line_lengths),
        "meanLineLength": (sum(line_lengths) / len(line_lengths)) if line_lengths else 0.0,
        "longInteriorLineRatio": _ratio(long_interior_count, interior_count),
        "unterminatedInteriorLineRatio": _ratio(unterminated_interior_count, interior_count),
        "longUnterminatedInteriorLineRatio": _ratio(long_unterminated_interior_count, interior_count),
        "explicitBreakLineRatio": _ratio(explicit_break_count, line_count),
        "unmarkedLineRatio": _ratio(unmarked_count, line_count),
    }


def _text_paragraphs(
    line_events: list[tuple[int, list[tuple[str, str]]]],
) -> list[list[tuple[int, str, int]]]:
    paragraphs: list[list[tuple[int, str, int]]] = []
    current: list[tuple[int, str, int]] = []
    for line_no, events in line_events:
        leading_breaks = _leading_breaks(events)
        if leading_breaks >= 2 and current:
            paragraphs.append(current)
            current = []
        text = _events_text(events)
        if text and _WORDISH_RE.search(text):
            current.append((line_no, text, leading_breaks))
    if current:
        paragraphs.append(current)
    return paragraphs


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _blocks_for_mode(line_events: list[tuple[int, list[tuple[str, str]]]], mode: str) -> list[dict[str, Any]]:
    return _verse_blocks(line_events) if mode == "verse" else _prose_blocks(line_events)

def extract_part_title(html: str) -> str | None:
    match = _PART_TITLE_RE.search(html)
    return clean_text(match.group(1)) if match else None


def extract_body_fragment(html: str) -> str:
    start = _START_RE.search(html)
    if not start:
        return html
    end = _END_RE.search(html, start.end())
    return html[start.end() : end.start()] if end else html[start.end() :]


def landing_section_count(resolved_sections: list[SectionLink]) -> int:
    return len(resolved_sections)


def _section_record(
    *,
    work_slug: str,
    seq: int,
    heading: str,
    part_title: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "seq": seq,
        "id": section_id(work_slug, seq, heading),
        "heading": heading,
        "partTitle": part_title,
        "blocks": blocks,
    }


def section_id(work_slug: str, seq: int, heading: str) -> str:
    if match := _ROMAN_RE.match(heading):
        suffix = f"bk-{_roman_to_int(match.group(1))}"
    elif match := _SECTION_NUM_RE.match(heading):
        suffix = f"section-{match.group(1)}"
    else:
        suffix = slugify(heading)
    if not suffix:
        suffix = str(seq)
    return f"{work_slug}-{suffix}"


def _line_events(fragment: str) -> list[tuple[int, list[tuple[str, str]]]]:
    events_by_line: list[tuple[int, list[tuple[str, str]]]] = []
    current_line: int | None = None
    current_events: list[tuple[str, str]] = []

    def flush_current() -> None:
        nonlocal current_line, current_events
        if current_line is not None:
            events_by_line.append((current_line, current_events))
        current_line = None
        current_events = []

    for match in _TOKEN_RE.finditer(fragment):
        if line_no := match.group(1):
            flush_current()
            current_line = int(line_no)
            current_events = []
        elif match.group(2):
            if current_line is not None:
                current_events.append(("br", ""))
        elif match.group(3) is not None:
            if current_line is not None:
                text = _html_fragment_text(match.group(3))
                if text:
                    current_events.append(("bold", text))
        elif match.group(4) is not None:
            if current_line is not None:
                text = clean_text(match.group(4))
                if text:
                    current_events.append(("text", text))
    flush_current()
    return events_by_line


def _verse_blocks(line_events: list[tuple[int, list[tuple[str, str]]]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for line_no, events in line_events:
        text = _events_text(events)
        if not text or not _WORDISH_RE.search(text):
            continue
        blocks.append({"type": "verseLine", "text": text, "indent": 0, "lineNo": line_no})
    return blocks


def _prose_blocks(line_events: list[tuple[int, list[tuple[str, str]]]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    para_parts: list[str] = []
    para_line_start: int | None = None

    def flush_para() -> None:
        nonlocal para_parts, para_line_start
        if not para_parts:
            return
        text = clean_text(" ".join(para_parts))
        if text:
            speaker = _speaker_block(text, para_line_start)
            if speaker:
                blocks.append(speaker)
            else:
                block: dict[str, Any] = {"type": "para", "text": text}
                if para_line_start is not None:
                    block["lineStart"] = para_line_start
                blocks.append(block)
        para_parts = []
        para_line_start = None

    for line_no, events in line_events:
        leading_breaks = _leading_breaks(events)
        segments = _segments(events)
        if leading_breaks >= 2:
            flush_para()
        if not segments:
            continue
        text = _segments_text(segments)
        if not text or not _WORDISH_RE.search(text):
            continue
        if _is_standalone_heading(segments) or (not para_parts and _is_dialogue_heading(text)):
            flush_para()
            blocks.append({"type": "heading", "level": 3, "text": text, "lineStart": line_no})
            continue
        if para_line_start is None:
            para_line_start = line_no
        para_parts.append(text)
    flush_para()
    return blocks


def _segments(events: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(kind, text) for kind, text in events if kind != "br" and text]


def _events_text(events: list[tuple[str, str]]) -> str:
    return _segments_text(_segments(events))


def _segments_text(segments: list[tuple[str, str]]) -> str:
    return clean_text(" ".join(text for _kind, text in segments))


def _leading_breaks(events: list[tuple[str, str]]) -> int:
    count = 0
    for kind, text in events:
        if kind == "br":
            count += 1
        elif text:
            break
    return count


def _is_standalone_heading(segments: list[tuple[str, str]]) -> bool:
    if not segments:
        return False
    text = _segments_text(segments)
    if _NUMERIC_LABEL_RE.match(text):
        return False
    return all(kind == "bold" for kind, _text in segments)

def _is_dialogue_heading(text: str) -> bool:
    if len(text) > 96 or text.endswith(_TERMINAL_END_CHARS):
        return False
    if _DIALOGUE_DASH_HEADING_RE.match(text):
        return True
    return text.upper() in {"PERSONS OF THE DIALOGUE", "DRAMATIS PERSONAE", "SCENE"}


def _speaker_block(text: str, line_start: int | None) -> dict[str, Any] | None:
    match = _SPEAKER_RE.match(text)
    if not match:
        return None
    name = clean_text(match.group(1))
    body = clean_text(match.group(2))
    if not name or name.title() == name:
        return None
    block: dict[str, Any] = {"type": "speaker", "name": name, "text": body}
    if line_start is not None:
        block["lineStart"] = line_start
    return block


def _html_fragment_text(fragment: str) -> str:
    return clean_text(BeautifulSoup(fragment, "lxml").get_text(" "))


def _stats(sections: list[dict[str, Any]]) -> dict[str, int]:
    paragraph_count = 0
    total_words = 0
    for section in sections:
        for block in section["blocks"]:
            if block["type"] in {"para", "speaker"}:
                paragraph_count += 1
            if block["type"] == "speaker":
                total_words += word_count(block.get("text", ""))
            else:
                total_words += word_count(block.get("text", ""))
    return {
        "sectionCount": len(sections),
        "paragraphCount": paragraph_count,
        "wordCount": total_words,
    }


def _cross_check_txt(
    client: CachedHttpClient,
    txt_url: str | None,
    stats: dict[str, int],
    work_id: str,
) -> None:
    if not txt_url:
        return
    try:
        txt = client.fetch(txt_url).text
    except Exception as exc:  # noqa: BLE001 - cross-check must not block normalization.
        LOG.warning("txt cross-check failed for %s: %s", work_id, exc)
        return
    txt_words = word_count(txt)
    if not txt_words:
        return
    delta = abs(stats["wordCount"] - txt_words) / txt_words
    if delta > 0.35:
        LOG.warning(
            "word-count cross-check diverged for %s: html=%s txt=%s delta=%.2f",
            work_id,
            stats["wordCount"],
            txt_words,
            delta,
        )
    else:
        LOG.info("txt cross-check ok for %s html=%s txt=%s", work_id, stats["wordCount"], txt_words)


def _block_type(work: dict[str, Any], block_type: str) -> list[bool]:
    return [
        block.get("type") == block_type
        for section in work.get("sections", [])
        for block in section.get("blocks", [])
    ]


def _roman_to_int(value: str) -> int:
    numerals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for char in reversed(value.upper()):
        current = numerals[char]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total
