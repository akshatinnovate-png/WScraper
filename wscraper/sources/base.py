"""Source plug-in contract and registry."""

from __future__ import annotations

import html
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import ClassVar

from ..http import Fetcher
from ..models import Mention
from ..query import Query

log = logging.getLogger(__name__)

REGISTRY: dict[str, type["Source"]] = {}


def register(cls: type["Source"]) -> type["Source"]:
    REGISTRY[cls.name] = cls
    return cls


def strip_tags(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


class Source(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    env: ClassVar[tuple[str, ...]] = ()   # all required for the source to run
    group: ClassVar[str] = "web"          # web | news | social | code | academic | archive | platform
    # True when the text a source returns IS the content (a Reddit post body, an HN comment), so a match
    # there confirms the mention. Search-engine and feed snippets are only leads: the page gets opened.
    authoritative: ClassVar[bool] = True

    def __init__(self, fetcher: Fetcher, pages: int = 3) -> None:
        self.fetcher = fetcher
        self.pages = max(1, pages)

    @classmethod
    def available(cls) -> bool:
        return all(os.environ.get(k) for k in cls.env)

    @classmethod
    def key(cls, name: str) -> str:
        return os.environ.get(name, "")

    @abstractmethod
    async def search(self, q: Query) -> list[Mention]:
        ...

    def hit(self, q: Query, url: str, *, title: str = "", body: str = "", **kw) -> Mention:
        """Build a Mention, pre-verifying it from the text the source returned when possible."""
        text = f"{title}\n{body}"
        found = q.matches(text)
        snippet = q.snippet(body) or q.snippet(title) or body[:300]
        m = Mention(url=url, title=strip_tags(title)[:300], snippet=strip_tags(snippet)[:500], source=self.name, **kw)
        if found and self.authoritative:
            m.verified, m.verify_note = True, f"name present in {self.name} result text"
        return m
