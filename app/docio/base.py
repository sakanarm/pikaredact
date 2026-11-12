"""Shared document model: a Segment is backed by 'holders' (XML text/attributes, PDF chars).

cmap[i] says where character i of the segment text lives: (holder index, offset)
or None for a virtual character (tab, line break) that cannot be edited.
Applying a replacement writes the new text into the first editable character of
the span and empties the rest, so runs/formatting are kept ("character coverage").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Segment


class Holder:
    __slots__ = ("el", "attr", "preserve")

    def __init__(self, el, attr: str | None = None, preserve: str | None = None):
        self.el, self.attr, self.preserve = el, attr, preserve

    def get(self) -> str:
        return (self.el.get(self.attr) if self.attr else self.el.text) or ""

    def set(self, value: str) -> None:
        if self.attr:
            self.el.set(self.attr, value)
        else:
            self.el.text = value
            if self.preserve:
                self.el.set(self.preserve, "preserve")


@dataclass
class Unit:
    seg: Segment
    holders: list = field(default_factory=list)
    cmap: list = field(default_factory=list)
    hl: str | None = None          # highlight strategy: "w" | "a" | "cell" | None
    cells: list = field(default_factory=list)  # xlsx <c> elements shown by this unit
    numeric: bool = False          # xlsx number cell (needs conversion to text when edited)

    def add(self, holder: Holder) -> None:
        hi = len(self.holders)
        self.holders.append(holder)
        txt = holder.get()
        self.cmap.extend((hi, k) for k in range(len(txt)))
        self.seg.text += txt

    def virtual(self, ch: str) -> None:
        self.cmap.append(None)
        self.seg.text += ch

    def apply(self, edits: list[tuple[int, int, str]]) -> int:
        """Returns the number of edits that could not be placed."""
        texts = [list(h.get()) for h in self.holders]
        missed = 0
        for s, e, new in edits:
            pos = [self.cmap[p] for p in range(s, min(e, len(self.cmap))) if self.cmap[p] is not None]
            if not pos:
                missed += 1
                continue
            hi, off = pos[0]
            texts[hi][off] = new
            for hi, off in pos[1:]:
                texts[hi][off] = ""
        for h, t in zip(self.holders, texts):
            v = "".join(t)
            if v != h.get():
                h.set(v)
        return missed

    def marks_by_holder(self, spans: list[tuple[int, int]]) -> dict[int, set[int]]:
        out: dict[int, set[int]] = {}
        for s, e in spans:
            for p in range(s, min(e, len(self.cmap))):
                c = self.cmap[p]
                if c is not None:
                    out.setdefault(c[0], set()).add(c[1])
        return out


class Document:
    """Interface implemented by each format."""

    kind = "text"
    ext = ".txt"
    filename = ""
    warnings: list[str]

    @property
    def segments(self) -> list[Segment]:
        raise NotImplementedError

    def render_redacted(self, edits: dict[str, list[tuple[int, int, str]]], sanitize: bool = True) -> tuple[bytes, dict]:
        raise NotImplementedError

    def render_highlight(self, spans: dict[str, list[tuple[int, int]]]) -> tuple[bytes, dict]:
        raise NotImplementedError

    @property
    def highlight_ext(self) -> str:
        return self.ext
