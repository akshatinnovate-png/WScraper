"""Turn a user query into search variants and an exact-mention matcher."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


# Indic scripts (Devanagari through Malayalam) write vowels and conjuncts with combining marks, which
# Python's \w does not count as letters. Treat them as part of the word, except the danda punctuation.
_INDIC = "\u0900-\u0963\u0966-\u0DFF"
_TOKEN = re.compile(rf"(?:[^\W_]|[{_INDIC}])+")
_LETTER = rf"(?:[^\W_]|[{_INDIC}])"


def _fold(text: str) -> str:
    """Lowercase and strip Latin accents so "Sárkar" matches "Sarkar".

    Only marks on Latin letters are dropped: in Hindi or Bengali the marks are the vowels themselves,
    and stripping them would make अक्षत (Akshat) and अक्षय (Akshay) look alike.
    """
    out, base = [], ""
    for c in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(c):
            if base and ord(base) < 0x250:
                continue
        else:
            base = c
        out.append(c)
    return "".join(out).lower()


@dataclass
class Query:
    raw: str
    extra_aliases: list[str] = field(default_factory=list)
    tokens: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.raw = " ".join(self.raw.split())
        self.tokens = _TOKEN.findall(self.raw)
        if not self.tokens:
            raise ValueError("query must contain at least one word")
        self._patterns = [self._pattern(self.tokens)]
        if len(self.tokens) >= 2:
            # "Sarkar, Akshat" / "Sarkar Akshat" (surname-first: bylines, citations, forms).
            self._patterns.append(self._pattern([self.tokens[-1], *self.tokens[:-1]]))
        for alias in self.extra_aliases:
            toks = _TOKEN.findall(alias)
            if toks:
                self._patterns.append(self._pattern(toks))

    @staticmethod
    def _pattern(tokens: list[str]) -> re.Pattern[str]:
        # Tokens may be joined by nothing (handles), spaces, dots, dashes, underscores or a comma,
        # so "akshat_sarkar", "AkshatSarkar", "akshat-sarkar" and "Sarkar, Akshat" all count.
        sep = r"[\s._\-,'’]{0,3}"
        body = sep.join(re.escape(_fold(t)) for t in tokens)
        return re.compile(rf"(?<!{_LETTER}){body}(?!{_LETTER})")

    @property
    def is_multiword(self) -> bool:
        return len(self.tokens) > 1

    def search_phrases(self) -> list[str]:
        """Exact-phrase strings to send to search backends, most specific first."""
        phrases = [self.raw]
        if self.is_multiword:
            phrases.append(f"{self.tokens[-1]}, {' '.join(self.tokens[:-1])}")
            phrases.append("".join(self.tokens))       # AkshatSarkar
            phrases.append("_".join(self.tokens).lower())  # akshat_sarkar
        phrases.extend(self.extra_aliases)
        seen, out = set(), []
        for p in phrases:
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
        return out

    def handle_guesses(self) -> list[str]:
        """Likely usernames, used for profile checks on platforms that expose public profile URLs."""
        t = [x.lower() for x in self.tokens]
        guesses = {"".join(t), "_".join(t), ".".join(t), "-".join(t)}
        if len(t) >= 2:
            guesses |= {t[0] + t[-1][0], t[0][0] + t[-1], "".join(reversed(t))}
        return sorted(g for g in guesses if len(g) >= 3)

    def find(self, text: str) -> list[tuple[int, int]]:
        """Spans in `text` (offsets into the folded text, same length for Latin scripts) that mention the query."""
        folded = _fold(text)
        spans: list[tuple[int, int]] = []
        for pat in self._patterns:
            spans.extend(m.span() for m in pat.finditer(folded))
        return sorted(set(spans))

    def matches(self, text: str) -> bool:
        return bool(text) and bool(self.find(text))

    def snippet(self, text: str, width: int = 160) -> str:
        """Context around the first mention, or "" when the text does not mention the query."""
        spans = self.find(text)
        if not spans:
            return ""
        folded_len = len(_fold(text))
        if folded_len != len(text):
            # Offsets shifted by accent folding; fall back to the folded text so the window is right.
            text = _fold(text)
        start, end = spans[0]
        lo, hi = max(0, start - width), min(len(text), end + width)
        out = " ".join(text[lo:hi].split())
        return ("…" if lo else "") + out + ("…" if hi < len(text) else "")
