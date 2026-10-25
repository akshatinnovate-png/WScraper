"""News: Google News and Bing News feeds across editions, and the GDELT global news index."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import feedparser
import httpx

from ..models import Mention
from ..query import Query
from .base import Source, register

# Google News serves different articles per edition; local papers only show up in their own.
GOOGLE_NEWS_EDITIONS = [
    ("en-US", "US", "US:en"), ("en-IN", "IN", "IN:en"), ("en-GB", "GB", "GB:en"),
    ("en-CA", "CA", "CA:en"), ("en-AU", "AU", "AU:en"), ("hi", "IN", "IN:hi"),
    ("bn", "IN", "IN:bn"), ("en-SG", "SG", "SG:en"), ("en-AE", "AE", "AE:en"),
]
# Editions searched year by year. Each Google News query returns at most 100 articles, so one
# all-time query silently drops older coverage; per-year windows lift that cap.
DEEP_EDITIONS = [("en-IN", "IN", "IN:en"), ("en-US", "US", "US:en"), ("hi", "IN", "IN:hi")]
FIRST_YEAR = 2015


def _names(q: Query) -> list[str]:
    """The name as typed, surname-first, and every alias (e.g. Hindi/Bengali spellings)."""
    names = [q.raw, *q.extra_aliases]
    if q.is_multiword:
        names.insert(1, f"{q.tokens[-1]}, {' '.join(q.tokens[:-1])}")
    return names


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
    description = "Google News RSS: 9 editions, plus year-by-year searches back to 2015 to get past the 100-result cap"

    async def _query(self, q: Query, query: str, edition: tuple[str, str, str]) -> list[Mention]:
        hl, gl, ceid = edition
        try:
            xml = await self.fetcher.text("https://news.google.com/rss/search",
                                          params={"q": query, "hl": hl, "gl": gl, "ceid": ceid})
        except httpx.HTTPError:
            return []  # one throttled query must not discard the hundreds already collected
        return self.entries(q, xml)

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for name in _names(q):
            for edition in GOOGLE_NEWS_EDITIONS:
                out += await self._query(q, f'"{name}"', edition)
        this_year = datetime.now(timezone.utc).year
        for name in [q.raw, *q.extra_aliases]:
            for year in range(this_year, FIRST_YEAR - 1, -1):
                for edition in DEEP_EDITIONS:
                    out += await self._query(q, f'"{name}" after:{year}-01-01 before:{year + 1}-01-01', edition)
        return out


@register
class BingNews(_Feed):
    name = "bingnews"
    description = "Bing News RSS"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for phrase in _names(q):
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
    description = "GDELT DOC 2.0: global online news in 65 languages, full archive back to 2017"

    # GDELT asks for at most one request every 5 seconds.
    interval = 5.0

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        this_year = datetime.now(timezone.utc).year
        # Year windows: each request returns at most 250 articles, so one all-archive query would truncate.
        for year in range(this_year, 2016, -1):
            if year != this_year:
                await asyncio.sleep(self.interval)
            try:
                data = await self.fetcher.json("https://api.gdeltproject.org/api/v2/doc/doc", params={
                    "query": f'"{q.raw}"', "mode": "ArtList", "format": "json", "maxrecords": "250",
                    "startdatetime": f"{year}0101000000", "enddatetime": f"{year}1231235959", "sort": "DateDesc",
                })
            except (httpx.HTTPError, ValueError):  # GDELT answers errors as plain text
                continue
            out += self._articles(q, data)
        return out

    def _articles(self, q: Query, data) -> list[Mention]:
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
