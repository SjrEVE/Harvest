"""Text folding used by UI-06 search.

`fold_vietnamese` exists because SQLite's unicode61 remove_diacritics leaves "đ" alone;
these tests pin the letters that motivated it, not the general idea of accent folding.
"""

from __future__ import annotations

import pytest

from santhau_studio.domain.text import fold_for_query, fold_vietnamese


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("đường", "duong"),
        ("Đường", "duong"),
        ("Điện thoại", "dien thoai"),
        ("Đại học Đồng Nai", "dai hoc dong nai"),
        ("cẩn trọng", "can trong"),
        ("SỬA CHỮA", "sua chua"),
        ("ơ Ơ", "o o"),
        ("Hà Nội", "ha noi"),
        ("normal ascii", "normal ascii"),
        ("", ""),
        (None, ""),
    ],
)
def test_fold_vietnamese_maps_query_text_to_ascii(text: str | None, expected: str) -> None:
    assert fold_vietnamese(text) == expected


def test_folding_is_idempotent() -> None:
    once = fold_vietnamese("Đường sắt đô thị")
    assert fold_vietnamese(once) == once


def test_non_latin_scripts_survive_folding() -> None:
    """A source page can carry other scripts; folding must not silently drop them."""
    assert fold_vietnamese("合同 123") == "合同 123"


def test_query_folding_wraps_each_token_as_a_phrase() -> None:
    assert fold_for_query("sửa chữa đường") == '"sua" "chua" "duong"'


def test_query_folding_escapes_quotes_and_operators() -> None:
    # A literal quote doubles inside the phrase, so the phrase itself stays closed.
    assert fold_for_query('gói "A"') == '"goi" """a"""'
    assert fold_for_query("NEAR") == '"near"'
    assert fold_for_query("   ") == ""


def test_query_and_stored_text_fold_identically() -> None:
    """The property the index depends on: same function on both sides, so matching is symmetric."""
    stored = "Mua sắm thiết bị công nghệ thông tin"
    for typed in ("mua sam thiet bi", "MUA SẮM THIẾT BỊ", "Mua sắm thiết bị"):
        assert fold_vietnamese(typed) in fold_vietnamese(stored)
