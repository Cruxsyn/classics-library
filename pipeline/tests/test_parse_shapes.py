from __future__ import annotations

from pathlib import Path

from pipeline.parse import parse_text_page
from pipeline.resolve import parse_landing_html

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_iliad_multi_verse_roman_sections() -> None:
    landing = parse_landing_html(
        fixture("homer_iliad.html"),
        "https://classics.mit.edu/Homer/iliad.html",
        "iliad",
    )

    assert landing.shape == "multi"
    assert len(landing.sections) == 24
    assert landing.sections[0].seq == 1
    assert landing.sections[0].href == "iliad.1.i.html"
    assert landing.sections[-1].seq == 24
    assert landing.sections[-1].href == "iliad.24.xxiv.html"

    parsed = parse_text_page(fixture("homer_iliad_1_i.html"), is_verse=True, section_heading="Book I")
    assert parsed["partTitle"] == "Book I"
    verse_lines = [block for block in parsed["blocks"] if block["type"] == "verseLine"]
    assert verse_lines
    assert verse_lines[0]["lineNo"] == 10
    assert verse_lines[0]["text"].startswith("Sing, O goddess")


def test_artwar_single_page_headings_and_prose_labels() -> None:
    landing = parse_landing_html(
        fixture("tzu_artwar.html"),
        "https://classics.mit.edu/Tzu/artwar.html",
        "artwar",
    )

    assert landing.shape == "single"
    assert landing.textUrls == ["https://classics.mit.edu/Tzu/artwar.html"]
    assert landing.txtUrl == "https://classics.mit.edu/Tzu/artwar.1b.txt"

    parsed = parse_text_page(fixture("tzu_artwar.html"), is_verse=False, section_heading="The Art of War")
    headings = [block["text"] for block in parsed["blocks"] if block["type"] == "heading"]
    paras = [block["text"] for block in parsed["blocks"] if block["type"] == "para"]
    assert "I. Laying Plans" in headings
    assert "II. Waging War" in headings
    assert paras[0] == "1. Sun Tzu said: The art of war is of vital importance to the State."


def test_poetics_multi_arabic_labels_are_parsed() -> None:
    landing = parse_landing_html(
        fixture("aristotle_poetics.html"),
        "https://classics.mit.edu/Aristotle/poetics.html",
        "poetics",
    )

    assert landing.shape == "multi"
    assert [section.href for section in landing.sections] == [
        "poetics.1.1.html",
        "poetics.2.2.html",
        "poetics.3.3.html",
    ]
    assert [section.label for section in landing.sections] == ["Section 1", "Section 2", "Section 3"]

    parsed = parse_text_page(fixture("aristotle_poetics_1_1.html"), is_verse=False, section_heading="Section 1")
    assert parsed["partTitle"] == "Section 1"
    assert any(block["type"] == "para" for block in parsed["blocks"])


def test_republic_multi_mixed_labels_are_parsed() -> None:
    landing = parse_landing_html(
        fixture("plato_republic.html"),
        "https://classics.mit.edu/Plato/republic.html",
        "republic",
    )

    assert landing.shape == "multi"
    assert len(landing.sections) == 11
    assert landing.sections[0].href == "republic.1.introduction.html"
    assert landing.sections[0].label == "The Introduction"
    assert landing.sections[1].href == "republic.2.i.html"
    assert landing.sections[-1].href == "republic.11.x.html"
