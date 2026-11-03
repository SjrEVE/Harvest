"""Text folding for search (§7.2 UI-06).

FTS5's `unicode61 remove_diacritics 2` strips combining marks, so "chữa" indexes as "chua".
It does **not** fold the stroked letters: "đường" stays "đuong". Vietnamese users routinely
type without diacritics, and "đ" is everywhere in procurement text (đường, điện, đồng, Đại
học), so an index built only on remove_diacritics silently misses those queries.

Folding is applied to the *stored* text and to the *query* by the same function, which is why
the FTS columns hold ASCII rather than the original characters. Display text is never folded;
it comes from the row tables.
"""

from __future__ import annotations

import unicodedata

_EXTRA_FOLD = str.maketrans({"đ": "d", "Đ": "D", "ơ": "o", "Ơ": "O"})


def fold_vietnamese(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return without_marks.translate(_EXTRA_FOLD).casefold()


def fold_for_query(query: str) -> str:
    """Fold a user query and make it safe for FTS5 MATCH.

    Each token becomes a double-quoted phrase, so FTS operators and punctuation inside what
    the user typed ("and", "NEAR", a stray quote) are matched as text instead of reparsed as
    syntax. A literal quote is doubled, which is FTS5's escape inside a quoted phrase.
    """
    tokens = (token.replace('"', '""') for token in fold_vietnamese(query).split())
    return " ".join(f'"{token}"' for token in tokens if token)
