from __future__ import annotations

import json
from pathlib import Path

from pipeline.covers import monogram_for_author, monogram_svg
from pipeline.portraits import commons_file_title_from_url


def test_monogram_fallback_is_deterministic() -> None:
    first = monogram_for_author("Antoninus", "Marcus Aurelius")
    second = monogram_for_author("Antoninus", "Marcus Aurelius")

    assert first == second
    assert first.as_json() == {"type": "monogram", "initial": "MA", "color": first.color}
    assert first.color.startswith("#")


def test_monogram_svg_uses_same_metadata() -> None:
    svg, fallback = monogram_svg("Homer", "Homer")

    assert fallback == monogram_for_author("Homer", "Homer")
    assert fallback.initial in svg
    assert fallback.color in svg
    assert "Monogram cover for Homer" in svg


def test_commons_file_title_from_original_and_thumbnail_urls() -> None:
    original = "https://upload.wikimedia.org/wikipedia/commons/2/21/NAME.png"
    thumb = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ae/Some_File.jpg/500px-Some_File.jpg"

    assert commons_file_title_from_url(original) == "File:NAME.png"
    assert commons_file_title_from_url(thumb) == "File:Some_File.jpg"


def test_attribution_required_records_have_credit_fields() -> None:
    authors = json.loads(Path("catalog/authors.json").read_text(encoding="utf-8"))
    required = [author for author in authors if author.get("attributionRequired") is True]

    assert required
    for author in required:
        assert author.get("artist"), author["key"]
        assert author.get("license"), author["key"]
        assert author.get("licenseUrl"), author["key"]
        assert author.get("commonsFilePage"), author["key"]
