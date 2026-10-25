"""Open each candidate page, confirm the name is really there, and harvest leads it links to."""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .http import Fetcher
from .models import Mention, canonical_url
from .query import Query

log = logging.getLogger(__name__)

# Pages here need a login to read, so a fetch proves nothing; the search-result snippet is the evidence.
LOGIN_WALLED = {
    "linkedin.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "threads.net",
    "tiktok.com", "quora.com", "pinterest.com", "news.google.com",
}
_DATE_META = ("article:published_time", "og:published_time", "datePublished", "date", "pubdate",
              "publish-date", "dc.date", "sailthru.date", "parsely-pub-date")
_SKIP_LINK_HOSTS = {"google.com", "bing.com", "duckduckgo.com", "facebook.com", "x.com", "twitter.com"}


def _host(url: str) -> str:
    return urlsplit(canonical_url(url)).netloc


def _walled(url: str) -> bool:
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in LOGIN_WALLED)


def extract(html: str, base_url: str) -> tuple[str, str, str, str, list[tuple[str, str]]]:
    """(title, visible text, published date, author, links) from an HTML page."""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    published = author = ""
    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or meta.get("itemprop") or "").strip()
        if not published and key in _DATE_META and meta.get("content"):
            published = meta["content"].strip()
        if not author and key in ("author", "article:author", "byl") and meta.get("content"):
            author = meta["content"].strip()
    # Meta descriptions often carry the mention on JS-heavy pages whose body is empty.
    meta_text = " ".join(m.get("content", "") for m in soup.find_all("meta")
                         if (m.get("name") or m.get("property") or "") in ("description", "og:description", "og:title", "twitter:description"))
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if href.startswith("http"):
            links.append((href, " ".join(a.get_text(" ").split())))
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return title, f"{meta_text} {text}", published, author, links


class Verifier:
    def __init__(self, fetcher: Fetcher, q: Query, *, crawl_depth: int = 1, max_pages: int = 2000) -> None:
        self.f = fetcher
        self.q = q
        self.crawl_depth = crawl_depth
        self.max_pages = max_pages
        self.fetched = 0

    async def check(self, m: Mention, depth: int = 0) -> list[Mention]:
        """Verify `m` in place; return newly discovered leads (pages it links to with the name in the anchor)."""
        if m.verified is True and depth == 0 and not self._worth_crawling(m):
            return []
        if _walled(m.url):
            if m.verified is not True:
                evidence = self.q.matches(f"{m.title} {m.snippet}")
                m.verified = None
                m.verify_note = ("login-walled page; name present in search snippet" if evidence
                                 else "login-walled page; could not confirm")
            return []
        if self.fetched >= self.max_pages:
            m.verify_note = m.verify_note or "verification budget exhausted"
            return []
        self.fetched += 1
        try:
            if not await self.f.allowed(m.url):
                if m.verified is not True:
                    m.verify_note = "robots.txt disallows fetching; " + (
                        "name present in search snippet" if self.q.matches(f"{m.title} {m.snippet}") else "not verified")
                return []
            r = await self.f.get(m.url)
        except (httpx.HTTPError, ValueError) as exc:
            m.verify_note = m.verify_note or f"fetch failed: {type(exc).__name__}"
            return []
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400:
            m.verify_note = m.verify_note or f"HTTP {r.status_code}"
            if r.status_code in (404, 410):
                snap = await self._wayback(m.url)
                if snap:
                    m.verify_note = f"deleted (HTTP {r.status_code}); archived copy: {snap}"
                elif m.verified is not True:
                    m.verified, m.verify_note = False, f"HTTP {r.status_code}: page gone, no archived copy"
            return []
        if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
            # PDFs and other binaries: trust the engine snippet.
            if m.verified is not True and self.q.matches(f"{m.title} {m.snippet}"):
                m.verified, m.verify_note = True, f"{ctype or 'binary'} document; name in search snippet"
            return []
        title, text, published, author, links = extract(r.text, str(r.url))
        m.title = m.title or title
        m.published = m.published or published
        m.author = m.author or author
        if self.q.matches(text) or self.q.matches(title):
            m.verified = True
            m.verify_note = "name found on live page"
            m.snippet = self.q.snippet(text) or m.snippet
        elif m.verified is not True:
            if self.q.matches(f"{m.title} {m.snippet}"):
                m.verified, m.verify_note = None, "in search snippet but not in fetched HTML (JS-rendered or changed)"
            else:
                m.verified, m.verify_note = False, "name not found on page"
        return self._leads(m, links, depth) if m.verified else []

    async def _wayback(self, url: str) -> str:
        """Closest Wayback Machine snapshot, so deleted posts and articles are still recoverable."""
        try:
            data = await self.f.json("https://archive.org/wayback/available", params={"url": url})
        except (httpx.HTTPError, ValueError):
            return ""
        snap = data.get("archived_snapshots", {}).get("closest", {}) if isinstance(data, dict) else {}
        return snap.get("url", "") if snap.get("available") else ""

    def _worth_crawling(self, m: Mention) -> bool:
        return self.crawl_depth > 0 and m.kind in ("web", "news", "profile", "reference", "document")

    def _leads(self, parent: Mention, links: list[tuple[str, str]], depth: int) -> list[Mention]:
        if depth >= self.crawl_depth:
            return []
        out, seen = [], set()
        for href, anchor in links:
            if not self.q.matches(anchor):
                continue
            if _host(href) in _SKIP_LINK_HOSTS or canonical_url(href) == parent.key:
                continue
            if href in seen:
                continue
            seen.add(href)
            out.append(Mention(url=href, title=anchor, snippet=f"linked from {parent.url}", source="crawl"))
        return out
