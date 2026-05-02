"""Server-side bag_signature computation (BE2-015).

Mirrors the JavaScript normalisation in `web/src/lib/bagCode.ts::bagSignature`
so that the server can independently verify a client-supplied signature.

Normalisation pipeline (order MUST match the TS implementation):
  1. JS-compatible trim — remove leading/trailing ECMAScript whitespace.
     Python's ``str.strip()`` strips more characters than JS ``.trim()``
     (notably U+001C FS, U+001D GS, U+001E RS, U+001F US which are field
     separators inside bag codes).  Using Python's strip would produce a
     different digest for bags that end with a separator, so we must use
     the exact JS whitespace set.
  2. normalizeControlPictures — replace Unicode Control Pictures (U+2400
     block) back to their ASCII counterparts.  ZXing-C++ emits these
     instead of raw control chars; every other decoder emits the raw chars.
     The JS and Python replacements share the same six codepoints, in the
     same order.
  3. SHA-256 hex digest of the UTF-8-encoded normalised string.

The TS implementation intentionally trims **once**, before
``normalizeControlPictures``.  Trimming again afterwards would diverge
from TS for any bag whose normalised tail is an ASCII space produced by
the ``␠`` → ` ` substitution (e.g. ``"FOO␠"``).  Such bags would fail
server-side recompute and trigger spurious 422 / ``bag_signature_mismatch``.

Returns None for empty / whitespace-only input — mirrors the
``if (!normalised) return null`` branch in the TS implementation.
Never raises.
"""
from __future__ import annotations

import hashlib

# ECMAScript WhiteSpace + LineTerminator characters — the characters that
# JavaScript's String.prototype.trim() removes.  This is intentionally a
# strict subset of Python's str.isspace() universe: Python also considers
# ASCII FS (0x1c), GS (0x1d), RS (0x1e), US (0x1f) as whitespace, but
# JavaScript's trim() does NOT — those are field-separator control chars
# that appear legitimately inside bag codes, and stripping them would alter
# the digest.
#
# ECMAScript spec references:
#   WhiteSpace:     TAB(09) VT(0b) FF(0c) SP(20) NBSP(a0) ZWNBSP(feff) <USP>
#   LineTerminator: LF(0a) CR(0d) LS(2028) PS(2029)
_JS_WHITESPACE: frozenset[str] = frozenset(
    "\x09\x0a\x0b\x0c\x0d"   # TAB LF VT FF CR
    "\x20"                    # SPACE
    "\xa0"                    # NO-BREAK SPACE
    "﻿"                  # ZERO-WIDTH NO-BREAK SPACE (BOM)
    " "                  # LINE SEPARATOR
    " "                  # PARAGRAPH SEPARATOR
)


def _js_trim(s: str) -> str:
    """Trim leading and trailing ECMAScript whitespace from ``s``.

    Equivalent to ``s.trim()`` in JavaScript.  Unlike Python's
    ``str.strip()``, this does NOT strip ASCII control characters
    0x1c–0x1f (FS/GS/RS/US), which are legitimate field separators in
    bag codes.
    """
    start = 0
    n = len(s)
    while start < n and s[start] in _JS_WHITESPACE:
        start += 1
    end = n
    while end > start and s[end - 1] in _JS_WHITESPACE:
        end -= 1
    return s[start:end]


# Unicode → ASCII control-picture substitution map.
# Maps Unicode Control Pictures (U+2404=␄, U+241C=␜, etc.) back to their
# ASCII counterparts.  The order here mirrors `normalizeControlPictures` in
# web/src/lib/bagCode.ts — do NOT reorder without also touching the TS side.
_CONTROL_PICTURES: list[tuple[str, str]] = [
    ("␄", "\x04"),  # ␄ → EOT (0x04)
    ("␜", "\x1c"),  # ␜ → FS  (0x1c)
    ("␝", "\x1d"),  # ␝ → GS  (0x1d)
    ("␞", "\x1e"),  # ␞ → RS  (0x1e)
    ("␟", "\x1f"),  # ␟ → US  (0x1f)
    ("␠", " "),     # ␠ → SP  (0x20)
]


def _normalise_control_pictures(s: str) -> str:
    """Replace Unicode Control Picture characters with their ASCII originals."""
    for pic, ctrl in _CONTROL_PICTURES:
        if pic in s:
            s = s.replace(pic, ctrl)
    return s


def compute_bag_signature(raw: str) -> str | None:
    """Return the SHA-256 hex digest of the normalised bag code, or None.

    ``raw`` is the raw string that came out of the scanner, exactly as passed
    to ``bagSignature()`` on the client side.  The result is always a
    64-character lower-case hex string, or ``None`` when the input is
    empty / whitespace-only after normalisation.

    Contract pins: TS ``bagSignature`` and this function MUST return the same
    digest for every raw bag string.  The fixture
    ``web/src/lib/__fixtures__/bagSignatures.json`` is the shared truth table;
    ``backend/tests/test_bag_signature_parity.py`` asserts alignment.
    """
    try:
        # Mirror the TS pipeline exactly:
        # 1. JS-trim the raw input
        # 2. Substitute control pictures → ASCII
        # 3. Empty after normalisation → no signature
        # 4. SHA-256 hex
        # NB: TS trims only once — do NOT re-trim after picture substitution,
        # otherwise ``"FOO␠"`` (and similar tail-space bags) would diverge.
        normalised = _normalise_control_pictures(_js_trim(raw or ""))
        if not normalised:
            return None
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    except Exception:  # pragma: no cover — defensive, should never happen
        return None
