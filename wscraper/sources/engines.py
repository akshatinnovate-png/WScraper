"""General web search engines: scraped (no key) and official APIs (key)."""

from __future__ import annotations

import base64
import logging
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from ..models import Mention
from ..query import Query
from .base import Source, register

log = logging.getLogger(__name__)

# Platforms without an open search API. Reached through `site:` queries on the engines above.
PLATFORM_SITES = [
    "linkedin.com", "x.com", "twitter.com", "instagram.com", "facebook.com", "threads.net",
    "tiktok.com", "youtube.com", "medium.com", "substack.com", "quora.com", "pinterest.com",
    "reddit.com", "github.com", "researchgate.net", "academia.edu", "scholar.google.com",
    "crunchbase.com", "angel.co", "wellfound.com", "devpost.com", "kaggle.com", "dev.to",
    "hashnode.com", "behance.net", "dribbble.com", "slideshare.net", "scribd.com",
    "soundcloud.com", "spotify.com", "goodreads.com", "imdb.com", "telegram.me", "t.me",
    "discord.com", "tumblr.com", "vk.com", "producthunt.com", "stackoverflow.com",
]
DOCUMENT_TYPES = ["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx"]


def _ddg_target(href: str) -> str:
    if "duckduckgo.com/l/" in href:
        return unquote(parse_qs(urlsplit(href).query).get("uddg", [href])[0])
    return "https:" + href if href.startswith("//") else href


def _bing_target(href: str) -> str:
    # Bing wraps results as /ck/a?...&u=a1<urlsafe-base64>
    if "bing.com/ck/a" in href:
        u = parse_qs(urlsplit(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            raw = u[2:]
            try:
                return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
            except (ValueError, UnicodeDecodeError):
                pass
    return href


class _ScrapedEngine(Source):
    group = "web"
    authoritative = False

    async def run_query(self, q: Query, query: str) -> list[Mention]:
        raise NotImplementedError

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for phrase in q.search_phrases():
            out += await self.run_query(q, f'"{phrase}"')
        return out


@register
class DuckDuckGo(_ScrapedEngine):
    name = "duckduckgo"
    description = "DuckDuckGo HTML results (Bing-backed index), exact-phrase"

    async def run_query(self, q: Query, query: str) -> list[Mention]:
        out: list[Mention] = []
        for page in range(self.pages):
            html = await self.fetcher.text("https://html.duckduckgo.com/html/",
                                           params={"q": query, "s": str(page * 30), "dc": str(page * 30 + 1)})
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("div.result")
            for r in rows:
                a = r.select_one("a.result__a")
                if not a or not a.get("href"):
                    continue
                sn = r.select_one(".result__snippet")
                out.append(self.hit(q, _ddg_target(a["href"]), title=a.get_text(" "),
                                    body=sn.get_text(" ") if sn else ""))
            if len(rows) < 10:
                break
        return out


@register
class Bing(_ScrapedEngine):
    name = "bing"
    description = "Bing web results, exact-phrase"

    async def run_query(self, q: Query, query: str) -> list[Mention]:
        out: list[Mention] = []
        for page in range(self.pages):
            html = await self.fetcher.text("https://www.bing.com/search",
                                           params={"q": query, "first": str(page * 10 + 1), "count": "10"})
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("li.b_algo")
            for r in rows:
                a = r.select_one("h2 a")
                if not a or not a.get("href"):
                    continue
                sn = r.select_one(".b_caption p, .b_lineclamp2, .b_algoSlug")
                out.append(self.hit(q, _bing_target(a["href"]), title=a.get_text(" "),
                                    body=sn.get_text(" ") if sn else ""))
            if not rows:
                break
        return out


@register
class Mojeek(_ScrapedEngine):
    name = "mojeek"
    description = "Mojeek, an independent crawler index (finds pages Google/Bing skip)"

    async def run_query(self, q: Query, query: str) -> list[Mention]:
        out: list[Mention] = []
        for page in range(self.pages):
            html = await self.fetcher.text("https://www.mojeek.com/search",
                                           params={"q": query, "s": str(page * 10 + 1)})
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("ul.results-standard > li")
            for r in rows:
                a = r.select_one("a.title, h2 a, a.ob")
                if not a or not a.get("href"):
                    continue
                sn = r.select_one("p.s")
                out.append(self.hit(q, a["href"], title=a.get_text(" "), body=sn.get_text(" ") if sn else ""))
            if not rows:
                break
        return out


@register
class PlatformDorks(Source):
    authoritative = False
    name = "platforms"
    group = "platform"
    description = "site: searches for LinkedIn, X, Instagram, Facebook, TikTok and ~35 other platforms, plus documents"

    async def search(self, q: Query) -> list[Mention]:
        engine = DuckDuckGo(self.fetcher, pages=1)
        out: list[Mention] = []
        queries = [f'"{q.raw}" site:{site}' for site in PLATFORM_SITES]
        queries += [f'"{q.raw}" filetype:{ext}' for ext in DOCUMENT_TYPES]
        for query in queries:
            try:
                hits = await engine.run_query(q, query)
            except Exception as exc:  # one blocked dork must not sink the rest
                log.debug("dork %r failed: %s", query, exc)
                continue
            for h in hits:
                h.found_by = [self.name]
                h.source = self.name
                if "filetype:" in query:
                    h.kind = "document"
            out += hits
        return out


@register
class BraveAPI(Source):
    authoritative = False
    name = "brave"
    env = ("BRAVE_API_KEY",)
    description = "Brave Search API: its own independent index (free tier available)"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for phrase in q.search_phrases():
            for page in range(min(self.pages, 10)):
                data = await self.fetcher.json(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": f'"{phrase}"', "count": "20", "offset": str(page)},
                    headers={"X-Subscription-Token": self.key("BRAVE_API_KEY"), "Accept": "application/json"},
                )
                results = data.get("web", {}).get("results", [])
                for r in results:
                    out.append(self.hit(q, r["url"], title=r.get("title", ""), body=r.get("description", ""),
                                        published=r.get("page_age", "")))
                if not data.get("query", {}).get("more_results_available"):
                    break
        return out


@register
class GoogleCSE(Source):
    authoritative = False
    name = "google"
    env = ("GOOGLE_API_KEY", "GOOGLE_CSE_ID")
    description = "Google Programmable Search JSON API (whole-web engine id)"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for phrase in q.search_phrases():
            for page in range(min(self.pages * 3, 10)):  # API caps at 100 results
                data = await self.fetcher.json(
                    "https://www.googleapis.com/customsearch/v1",
                    params={"key": self.key("GOOGLE_API_KEY"), "cx": self.key("GOOGLE_CSE_ID"),
                            "q": phrase, "exactTerms": phrase, "start": str(page * 10 + 1), "num": "10"},
                )
                items = data.get("items", [])
                for r in items:
                    out.append(self.hit(q, r["link"], title=r.get("title", ""), body=r.get("snippet", "")))
                if len(items) < 10:
                    break
        return out


@register
class SerpAPI(Source):
    authoritative = False
    name = "serpapi"
    env = ("SERPAPI_KEY",)
    description = "Real Google results (web + news) through SerpAPI"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for phrase in q.search_phrases():
            for tbm in ("", "nws"):
                for page in range(self.pages * 2):
                    params = {"engine": "google", "q": f'"{phrase}"', "start": str(page * 10),
                              "num": "10", "api_key": self.key("SERPAPI_KEY")}
                    if tbm:
                        params["tbm"] = tbm
                    data = await self.fetcher.json("https://serpapi.com/search.json", params=params)
                    rows = data.get("organic_results", []) + data.get("news_results", [])
                    for r in rows:
                        out.append(self.hit(q, r["link"], title=r.get("title", ""), body=r.get("snippet", ""),
                                            published=r.get("date", ""), kind="news" if tbm else "web"))
                    if not data.get("serpapi_pagination", {}).get("next"):
                        break
        return out
