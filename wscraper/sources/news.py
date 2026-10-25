"""News: Google News and Bing News feeds across editions, and the GDELT global news index."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import feedparser

from ..models import Mention
from ..query import Query
from .base import Source, register

# Google News serves different articles per edition; local papers only show up in their own.
GOOGLE_NEWS_EDITIONS = [
    ("en-US", "US", "US:en"), ("en-IN", "IN", "IN:en"), ("en-GB", "GB", "GB:en"),
    ("en-CA", "CA", "CA:en"), ("en-AU", "AU", "AU:en"), ("hi", "IN", "IN:hi"),
    ("bn", "IN", "IN:bn"), ("en-SG", "SG", "SG:en"), ("en-AE", "AE", "AE:en"),
]


def _iso(entry) -> str:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(time.mktime(st), timezone.utc).isoformat() if st else ""


class _Feed(Source):
    group = "news"
    authoritative = False

    def entries(self, q: Query, xml: str) -> list[Mention]:
        out = []
        for e in feedparser.parse(xml).entries:
            src = e.get("source", {}).get("title", "") if isinstance(e.get("source"), dict) else ""
            out.append(self.hit(q, e.link, title=e.get("title", ""), body=e.get("summary", ""),
                                kind="news", published=_iso(e), author=src))
        return out


@register
class GoogleNews(_Feed):
    name = "googlenews"
    description = "Google News RSS across 9 country/language editions"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for phrase in q.search_phrases()[:2]:
            for hl, gl, ceid in GOOGLE_NEWS_EDITIONS:
                xml = await self.fetcher.text("https://news.google.com/rss/search",
                                              params={"q": f'"{phrase}"', "hl": hl, "gl": gl, "ceid": ceid})
                out += self.entries(q, xml)
        return out


@register
class BingNews(_Feed):
    name = "bingnews"
    description = "Bing News RSS"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for phrase in q.search_phrases()[:2]:
            for page in range(self.pages):
                xml = await self.fetcher.text("https://www.bing.com/news/search",
                                              params={"q": f'"{phrase}"', "format": "rss", "first": str(page * 10 + 1)})
                got = self.entries(q, xml)
                out += got
                if not got:
                    break
        return out


@register
class GDELT(Source):
    name = "gdelt"
    group = "news"
    authoritative = False
    description = "GDELT DOC 2.0: global online news in 65 languages, rolling ~3 months"

    async def search(self, q: Query) -> list[Mention]:
        data = await self.fetcher.json("https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": f'"{q.raw}"', "mode": "ArtList", "format": "json", "maxrecords": "250",
            "timespan": "3months", "sort": "DateDesc",
        })
        out = []
        for a in data.get("articles", []) if isinstance(data, dict) else []:
            seen = a.get("seendate", "")
            published = (f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}T{seen[9:11]}:{seen[11:13]}:{seen[13:15]}Z"
                         if len(seen) >= 15 else "")
            # GDELT only returns titles, so verification will fetch the article itself.
            m = self.hit(q, a["url"], title=a.get("title", ""), kind="news", published=published,
                         author=a.get("domain", ""))
            out.append(m)
        return out
