"""Consistent fake Thai data: the same original value always gets the same fake within one job."""
from __future__ import annotations

import random
import re

from faker import Faker

from ..detect.validators import thai_id_check_digit
from ..models import ENTITY_TYPES, SENSITIVE_TYPES

_TH_CONS = "กขคงจฉชซญฎฏฐฑฒณดตถทธนบปผพฟภมยรลวศษสหฬอฮ"
_PLATE_CONS = "กขคงจฉชซฌญฎฐฒณดตถทธนบผพฟภมยรลวศษสหฬอฮ"
_LATIN = re.compile(r"[A-Za-z]")
_MAC = re.compile(r"(?i)^[0-9a-f]{2}([:-])(?:[0-9a-f]{2}){4}[0-9a-f]{2}$")
# a fake disease or religion would put a false fact about a real person into the file: use a neutral label instead
_PLACEHOLDER = SENSITIVE_TYPES | {"GPS_LOCATION"}


def reshape(value: str, rng: random.Random) -> str:
    """Same shape, random content: digits->digits, A->A, a->a, Thai consonant->consonant."""
    out = []
    for ch in value:
        if ch.isdigit():
            out.append(str(rng.randrange(10)))
        elif "A" <= ch <= "Z":
            out.append(chr(rng.randrange(65, 91)))
        elif "a" <= ch <= "z":
            out.append(chr(rng.randrange(97, 123)))
        elif ch in _TH_CONS:
            out.append(rng.choice(_TH_CONS))
        else:
            out.append(ch)
    return "".join(out)


def _fill_digits(template: str, digits: str) -> str:
    it = iter(digits)
    return "".join(next(it, "0") if ch.isdigit() else ch for ch in template)


class FakeTH:
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.th = Faker("th_TH")
        self.en = Faker("en_US")
        s = self.rng.randrange(1 << 30)
        self.th.seed_instance(s)
        self.en.seed_instance(s + 1)
        self.n_email = 0
        self.n_social = 0

    def make(self, entity_type: str, value: str) -> str:
        r = self.rng
        latin = bool(_LATIN.search(value)) and not re.search(r"[฀-๿]", value)
        if entity_type == "PERSON":
            if latin:
                return self.en.name() if " " in value.strip() else self.en.first_name()
            return f"{self.th.first_name()} {self.th.last_name()}" if " " in value.strip() else self.th.first_name()
        if entity_type == "ORGANIZATION":
            if latin:
                return f"{self.en.last_name()} {r.choice(['Systems', 'Holdings', 'Solutions', 'Trading'])} Co., Ltd."
            return f"บริษัท {self.th.last_name()} {r.choice(['โซลูชัน', 'เทรดดิ้ง', 'โฮลดิ้งส์', 'ซิสเต็มส์'])} จำกัด"
        if entity_type == "ADDRESS":
            return self.th.address().replace("\n", " ")
        if entity_type == "EMAIL":
            self.n_email += 1
            return f"user{self.n_email:03d}@example.com"
        if entity_type == "PHONE":
            d = re.sub(r"\D", "", value)
            mobile = d.startswith(("06", "08", "09", "666", "668", "669"))
            body = (r.choice("689") if mobile else "2") + "".join(str(r.randrange(10)) for _ in range(12))
            lead = "66" if d.startswith("66") else "0"
            return _fill_digits(value, lead + body)
        if entity_type == "TH_ID_CARD":
            first12 = str(r.randrange(1, 9)) + "".join(str(r.randrange(10)) for _ in range(11))
            return _fill_digits(value, first12 + thai_id_check_digit(first12))
        if entity_type in _PLACEHOLDER:
            return f"[{ENTITY_TYPES[entity_type]['label'].split(' (')[0]}]"
        if entity_type == "URL":
            return "https://example.com/"
        if entity_type == "SOCIAL_MEDIA":
            self.n_social += 1
            if "/" in value:
                return f"https://example.com/user{self.n_social:03d}"
            return ("@" if value.startswith("@") else "") + f"user{self.n_social:03d}"
        if entity_type == "DEVICE_ID" and _MAC.match(value.strip()):
            sep = _MAC.match(value.strip()).group(1)
            return sep.join(f"{r.randrange(256):02X}" for _ in range(6))
        if entity_type == "IP_ADDRESS" and ":" in value:
            return f"2001:db8::{r.randrange(1, 0xffff):x}"  # documentation prefix, never routable
        if entity_type == "IP_ADDRESS":
            suffix = value[value.index("/"):] if "/" in value else ""
            return f"192.0.2.{r.randrange(1, 255)}{suffix}"  # TEST-NET-1, never routable
        if entity_type == "TH_LICENSE_PLATE":
            return re.sub(r"[ก-ฮ]", lambda _: r.choice(_PLATE_CONS), reshape(value, r))
        return reshape(value, r)
