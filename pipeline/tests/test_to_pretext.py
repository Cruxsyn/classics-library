from __future__ import annotations

import xml.etree.ElementTree as ET

from pipeline.to_pretext import XML_ID, validate_pretext_tree, work_to_pretext_tree


DIVISION_TAGS = {"book", "chapter", "section", "subsection", "subsubsection"}


def assert_valid_divisions_and_ids(tree: ET.ElementTree) -> None:
    validate_pretext_tree(tree)
    seen: set[str] = set()
    for elem in tree.getroot().iter():
        xml_id = elem.get(XML_ID)
        if xml_id:
            assert xml_id not in seen
            seen.add(xml_id)
        if elem.tag in DIVISION_TAGS:
            title = elem.find("title")
            assert title is not None
            assert title.text and title.text.strip()


def test_single_prose_structural_headings_split_into_chapters() -> None:
    work = {
        "id": "Tzu/artwar-fixture",
        "author": "Sun Tzu",
        "authorKey": "Tzu",
        "title": "The Art of War Fixture",
        "translator": "Lionel Giles",
        "shape": "single",
        "textMode": "prose",
        "sections": [
            {
                "seq": 1,
                "id": "artwar-fixture",
                "heading": "The Art of War Fixture",
                "partTitle": "The Art of War Fixture",
                "blocks": [
                    {"type": "heading", "level": 3, "text": "I. Laying Plans"},
                    {"type": "para", "text": "Sun Tzu said: The art of war is vital."},
                    {"type": "heading", "level": 3, "text": "II. Waging War"},
                    {"type": "para", "text": "When you engage in actual fighting, count the cost."},
                ],
            }
        ],
    }

    tree = work_to_pretext_tree(work)
    xml_text = ET.tostring(tree.getroot(), encoding="unicode")
    ET.fromstring(xml_text)
    assert_valid_divisions_and_ids(tree)

    chapters = tree.getroot().findall("./book/chapter")
    assert [chapter.findtext("title") for chapter in chapters] == ["I. Laying Plans", "II. Waging War"]
    assert chapters[0].find("p") is not None


def test_verse_and_speaker_blocks_render_as_poem_and_emphasis() -> None:
    work = {
        "id": "Sophocles/oedipus-fixture",
        "author": "Sophocles",
        "authorKey": "Sophocles",
        "title": "Oedipus Fixture",
        "translator": "F. Storr",
        "shape": "single",
        "textMode": "verse",
        "sections": [
            {
                "seq": 1,
                "id": "oedipus-fixture",
                "heading": "Oedipus Fixture",
                "partTitle": "Oedipus Fixture",
                "blocks": [
                    {"type": "heading", "level": 3, "text": "CHORUS"},
                    {"type": "verseLine", "text": "My children, latest born to Cadmus old,", "indent": 0},
                    {"type": "verseLine", "text": "Why sit ye here as suppliants?", "indent": 2},
                    {"type": "speaker", "name": "Oedipus", "text": "I would give all to save the city."},
                ],
            }
        ],
    }

    tree = work_to_pretext_tree(work)
    xml_text = ET.tostring(tree.getroot(), encoding="unicode")
    ET.fromstring(xml_text)
    assert_valid_divisions_and_ids(tree)

    chapter = tree.getroot().find("./book/chapter")
    assert chapter is not None
    assert chapter.find("p/em").text == "CHORUS"
    poem = chapter.find("poem")
    assert poem is not None
    lines = poem.findall("./stanza/line")
    assert [line.text for line in lines] == [
        "My children, latest born to Cadmus old,",
        "Why sit ye here as suppliants?",
    ]
    assert lines[1].get("indent") == "2"
    speaker_emphasis = chapter.findall("p/em")[-1]
    assert speaker_emphasis.text == "Oedipus."
    assert speaker_emphasis.tail == " I would give all to save the city."
