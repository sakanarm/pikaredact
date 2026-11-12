"""Length-preserving text normalisation.

Every substitution here is 1 char -> 1 char, so offsets found on the
normalised text are valid on the original text too. That lets us detect on
clean text but write replacements back into the untouched document.
"""
from __future__ import annotations

# Legacy Thai "shifted glyph" code points that many PDF producers emit
# (Private Use Area U+F700–U+F71A) mapped back to the real Thai characters.
_THAI_PUA = {
    0xF700: 0x0E10, 0xF701: 0x0E34, 0xF702: 0x0E35, 0xF703: 0x0E36, 0xF704: 0x0E37,
    0xF705: 0x0E48, 0xF706: 0x0E49, 0xF707: 0x0E4A, 0xF708: 0x0E4B, 0xF709: 0x0E4C,
    0xF70A: 0x0E48, 0xF70B: 0x0E49, 0xF70C: 0x0E4A, 0xF70D: 0x0E4B, 0xF70E: 0x0E4C,
    0xF70F: 0x0E0D, 0xF710: 0x0E31, 0xF711: 0x0E4D, 0xF712: 0x0E47, 0xF713: 0x0E48,
    0xF714: 0x0E49, 0xF715: 0x0E4A, 0xF716: 0x0E4B, 0xF717: 0x0E4C, 0xF718: 0x0E38,
    0xF719: 0x0E39, 0xF71A: 0x0E3A,
}

_TABLE: dict[int, int] = dict(_THAI_PUA)
# Thai digits ๐-๙ -> 0-9, full-width digits -> 0-9
for i in range(10):
    _TABLE[0x0E50 + i] = 0x30 + i
    _TABLE[0xFF10 + i] = 0x30 + i
# Invisible / special spaces -> plain space (Word inserts U+200B in Thai text)
for cp in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00A0, 0x202F, 0x2007, 0x3000):
    _TABLE[cp] = 0x20
# Hyphen look-alikes -> '-' (PDFs often emit U+00AD for a visible hyphen; Word uses U+2011 in phone numbers)
for cp in (0x00AD, 0x2010, 0x2011, 0x2012, 0x2013, 0x2212, 0xFE63, 0xFF0D):
    _TABLE[cp] = 0x2D


def normalize(text: str) -> str:
    return text.translate(_TABLE)
