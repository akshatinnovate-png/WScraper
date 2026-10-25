"""Code hosting, encyclopedias, academic indexes and web archives."""

from __future__ import annotations

import feedparser

from ..http import BOT_UA
from ..models import Mention
from ..query import Query
from .base import Source, register, strip_tags

WIKIPEDIA_LANGS = ("en", "hi", "bn", "de", "fr", "es")


@register
class Wikipedia(Source):
    name = "wikipedia"
    group = "reference"
    description = "Full text of Wikipedia (6 languages) and Wikidata entities"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for lang in WIKIPEDIA_LANGS:
            for page in range(self.pages):
                data = await self.fetcher.json(f"https://{lang}.wikipedia.org/w/api.php", params={
                    "action": "query", "list": "search", "srsearch": f'"{q.raw}"', "srwhat": "text",
                    "srlimit": "50", "sroffset": str(page * 50), "format": "json"}, ua=BOT_UA)
                rows = data.get("query", {}).get("search", [])
                for r in rows:
                    title = r["title"]
                    out.append(self.hit(q, f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                                        title=title, body=strip_tags(r.get("snippet", "")), kind="reference",
                                        published=r.get("timestamp", "")))
                if "continue" not in data:
                    break
        data = await self.fetcher.json("https://www.wikidata.org/w/api.php", params={
            "action": "wbsearchentities", "search": q.raw, "language": "en", "limit": "50", "format": "json"},
            ua=BOT_UA)
        for e in data.get("search", []):
            out.append(self.hit(q, f"https://www.wikidata.org/wiki/{e['id']}", title=e.get("label", ""),
                                body=f"{e.get('label', '')} {e.get('description', '')}", kind="reference"))
        return out


@register
class GitHub(Source):
    name = "github"
    group = "code"
    description = "GitHub users, repos, issues/PRs/comments, commits; code search with GITHUB_TOKEN"

    async def search(self, q: Query) -> list[Mention]:
        token = self.key("GITHUB_TOKEN")
        hdrs = {"Accept": "application/vnd.github+json"}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        searches = [
            ("users", f'"{q.raw}" in:name', "profile"),
            ("repositories", f'"{q.raw}"', "code"),
            ("issues", f'"{q.raw}"', "post"),
            ("commits", f'"{q.raw}"', "code"),
        ]
        if token:
            searches.append(("code", f'"{q.raw}"', "code"))
        out: list[Mention] = []
        for endpoint, query, kind in searches:
            for page in range(1, self.pages + 1):
                r = await self.fetcher.get(f"https://api.github.com/search/{endpoint}", headers=hdrs,
                                           params={"q": query, "per_page": "100", "page": str(page)})
                if r.status_code in (401, 403, 422):
                    break  # rate-limited or needs auth; keep what we have
                r.raise_for_status()
                items = r.json().get("items", [])
                for it in items:
                    url = it.get("html_url", "")
                    if endpoint == "users":
                        title, body = it.get("login", ""), ""  # display name isn't returned; verifier opens the profile
                    elif endpoint == "commits":
                        title, body = it.get("repository", {}).get("full_name", ""), it.get("commit", {}).get("message", "")
                    elif endpoint == "code":
                        title, body = it.get("repository", {}).get("full_name", ""), it.get("path", "")
                    else:
                        title, body = it.get("full_name") or it.get("title", ""), it.get("description") or it.get("body") or ""
                    m = self.hit(q, url, title=title, body=body or "", kind=kind)
                    if endpoint == "code":
                        m.verified, m.verify_note = None, ""
                    out.append(m)
                if len(items) < 100:
                    break
        return out


@register
class Crossref(Source):
    name = "crossref"
    group = "academic"
    description = "Crossref: 150M+ scholarly works by author name"

    async def search(self, q: Query) -> list[Mention]:
        data = await self.fetcher.json("https://api.crossref.org/works", params={
            "query.author": q.raw, "rows": str(min(1000, 100 * self.pages)), "select": "DOI,title,author,URL,created"})
        out = []
        for it in data.get("message", {}).get("items", []):
            authors = "; ".join(f"{a.get('given', '')} {a.get('family', '')}" for a in it.get("author", []))
            out.append(self.hit(q, it.get("URL") or f"https://doi.org/{it['DOI']}",
                                title=" ".join(it.get("title", [])), body=authors, kind="paper",
                                published=it.get("created", {}).get("date-time", "")))
        return out


@register
class OpenAlex(Source):
    name = "openalex"
    group = "academic"
    description = "OpenAlex: works whose full text/abstract mention the name, plus author records"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        authors = await self.fetcher.json("https://api.openalex.org/authors", params={
            "search": q.raw, "per-page": "50"})
        for a in authors.get("results", []):
            inst = ", ".join(i.get("display_name", "") for i in a.get("last_known_institutions") or [])
            out.append(self.hit(q, a["id"], title=a.get("display_name", ""),
                                body=f"{a.get('display_name', '')} {inst}", kind="profile"))
        for page in range(1, self.pages + 1):
            works = await self.fetcher.json("https://api.openalex.org/works", params={
                "search": f'"{q.raw}"', "per-page": "100", "page": str(page)})
            rows = works.get("results", [])
            for w in rows:
                names = "; ".join(x["author"].get("display_name", "") for x in w.get("authorships", []))
                out.append(self.hit(q, w.get("doi") or w["id"], title=w.get("title") or "", body=names,
                                    kind="paper", published=w.get("publication_date", "")))
            if len(rows) < 100:
                break
        return out


@register
class ArXiv(Source):
    name = "arxiv"
    group = "academic"
    description = "arXiv preprints by author and full metadata"

    async def search(self, q: Query) -> list[Mention]:
        xml = await self.fetcher.text("https://export.arxiv.org/api/query", params={
            "search_query": f'au:"{q.raw}" OR all:"{q.raw}"', "max_results": str(100 * self.pages)})
        out = []
        for e in feedparser.parse(xml).entries:
            names = "; ".join(a.get("name", "") for a in e.get("authors", []))
            out.append(self.hit(q, e.link, title=e.get("title", ""), body=f"{names}\n{e.get('summary', '')}",
                                kind="paper", published=e.get("published", "")))
        return out


@register
class InternetArchive(Source):
    name = "archive"
    group = "archive"
    description = "Internet Archive items (books, uploads, video, audio) mentioning the name"

    async def search(self, q: Query) -> list[Mention]:
        data = await self.fetcher.json("https://archive.org/advancedsearch.php", params={
            "q": f'"{q.raw}"', "fl[]": ["identifier", "title", "description", "creator", "date"],
            "rows": str(100 * self.pages), "output": "json"})
        out = []
        for d in data.get("response", {}).get("docs", []):
            desc = d.get("description", "")
            desc = " ".join(desc) if isinstance(desc, list) else desc
            creator = d.get("creator", "")
            creator = "; ".join(creator) if isinstance(creator, list) else creator
            out.append(self.hit(q, f"https://archive.org/details/{d['identifier']}", title=d.get("title", ""),
                                body=f"{creator}\n{desc}", kind="archive", author=creator,
                                published=d.get("date", "")))
        return out
