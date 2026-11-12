"""Checksum validators and helpers (pure functions, no dependencies)."""
from __future__ import annotations

import re

_NON_DIGIT = re.compile(r"\D")


def digits(text: str) -> str:
    return _NON_DIGIT.sub("", text)


def thai_id_checksum_ok(text: str) -> bool:
    """Thai national ID: 13 digits, last digit = (11 - sum(d_i * (13 - i)) % 11) % 10."""
    d = digits(text)
    if len(d) != 13 or d[0] == "0":
        return False
    total = sum(int(d[i]) * (13 - i) for i in range(12))
    return (11 - total % 11) % 10 == int(d[12])


def thai_id_check_digit(first12: str) -> str:
    total = sum(int(first12[i]) * (13 - i) for i in range(12))
    return str((11 - total % 11) % 10)


def luhn_ok(text: str) -> bool:
    d = digits(text)
    if not 12 <= len(d) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def card_prefix_ok(text: str) -> bool:
    d = digits(text)
    if len(d) == 15:
        return d[:2] in ("34", "37")
    if len(d) != 16:
        return False
    if d[0] == "4":
        return True
    p2, p4 = int(d[:2]), int(d[:4])
    return 51 <= p2 <= 55 or 2221 <= p4 <= 2720 or d[:2] in ("35", "62") or d[:4] == "6011" or d[:2] == "65"
