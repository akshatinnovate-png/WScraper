"""Social platforms and communities with public or keyed search APIs."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..http import BOT_UA
from ..models import Mention
from ..query import Query
from .base import Source, register, strip_tags


def _ts(epoch: float | int | None) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else ""


@register
class Reddit(Source):
    name = "reddit"
    group = "social"
    description = "Reddit posts and comments (set REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET if it gets blocked)"

    async def _endpoint(self) -> tuple[str, dict]:
        """Reddit blocks most anonymous scripts; with a free app's credentials, use the official OAuth API."""
        cid, secret = self.key("REDDIT_CLIENT_ID"), self.key("REDDIT_CLIENT_SECRET")
        if not (cid and secret):
            return "https://www.reddit.com/search.json", {}
        token = await self.fetcher.post_form(
            "https://www.reddit.com/api/v1/access_token", {"grant_type": "client_credentials"},
            auth=(cid, secret), ua=BOT_UA)
        return "https://oauth.reddit.com/search", {"Authorization": f"Bearer {token['access_token']}"}

    async def search(self, q: Query) -> list[Mention]:
        url, auth = await self._endpoint()
        out: list[Mention] = []
        for phrase in q.search_phrases()[:3]:
            for kind in ("link", "comment"):
                after = None
                for _ in range(self.pages):
                    params = {"q": f'"{phrase}"', "limit": "100", "sort": "new", "type": kind,
                              "include_over_18": "on", "raw_json": "1"}
                    if after:
                        params["after"] = after
                    data = await self.fetcher.json(url, params=params, headers=auth, ua=BOT_UA)
                    children = data.get("data", {}).get("children", [])
                    for c in children:
                        d = c.get("data", {})
                        link = "https://www.reddit.com" + d.get("permalink", "")
                        body = d.get("selftext") or d.get("body") or ""
                        out.append(self.hit(q, link, title=d.get("title") or d.get("link_title", ""), body=body,
                                            kind="post" if c.get("kind") == "t3" else "comment",
                                            author=d.get("author", ""), published=_ts(d.get("created_utc"))))
                    after = data.get("data", {}).get("after")
                    if not after:
                        break
        return out


@register
class HackerNews(Source):
    name = "hackernews"
    group = "social"
    description = "Every Hacker News story and comment (Algolia full-text index)"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for phrase in q.search_phrases()[:2]:
            for page in range(self.pages * 3):
                data = await self.fetcher.json("https://hn.algolia.com/api/v1/search_by_date", params={
                    "query": f'"{phrase}"', "tags": "(story,comment,poll,job)", "hitsPerPage": "100", "page": str(page)})
                for h in data.get("hits", []):
                    url = f"https://news.ycombinator.com/item?id={h['objectID']}"
                    body = " ".join(filter(None, [h.get("story_text"), h.get("comment_text"), h.get("url")]))
                    out.append(self.hit(q, url, title=h.get("title") or h.get("story_title") or "", body=body,
                                        kind="comment" if h.get("comment_text") else "post",
                                        author=h.get("author", ""), published=h.get("created_at", "")))
                if page + 1 >= data.get("nbPages", 0):
                    break
        return out


@register
class Bluesky(Source):
    name = "bluesky"
    group = "social"
    env = ("BSKY_HANDLE", "BSKY_APP_PASSWORD")
    description = "Bluesky post search (needs a free account + app password)"

    async def search(self, q: Query) -> list[Mention]:
        session = await self.fetcher.post_json("https://bsky.social/xrpc/com.atproto.server.createSession", {
            "identifier": self.key("BSKY_HANDLE"), "password": self.key("BSKY_APP_PASSWORD")})
        auth = {"Authorization": f"Bearer {session['accessJwt']}"}
        out: list[Mention] = []
        for phrase in q.search_phrases()[:2]:
            cursor = None
            for _ in range(self.pages * 2):
                params = {"q": f'"{phrase}"', "limit": "100", "sort": "latest"}
                if cursor:
                    params["cursor"] = cursor
                data = await self.fetcher.json("https://bsky.social/xrpc/app.bsky.feed.searchPosts",
                                               params=params, headers=auth)
                for p in data.get("posts", []):
                    handle = p["author"]["handle"]
                    rkey = p["uri"].rsplit("/", 1)[-1]
                    out.append(self.hit(q, f"https://bsky.app/profile/{handle}/post/{rkey}",
                                        title=p["author"].get("displayName", handle),
                                        body=p.get("record", {}).get("text", ""), kind="post",
                                        author=handle, published=p.get("indexedAt", "")))
                cursor = data.get("cursor")
                if not cursor:
                    break
        return out


@register
class Mastodon(Source):
    name = "mastodon"
    group = "social"
    env = ("MASTODON_INSTANCE", "MASTODON_TOKEN")
    description = "Mastodon/fediverse full-text status + account search via your instance"

    async def search(self, q: Query) -> list[Mention]:
        base = self.key("MASTODON_INSTANCE").rstrip("/")
        if not base.startswith("http"):
            base = "https://" + base
        auth = {"Authorization": f"Bearer {self.key('MASTODON_TOKEN')}"}
        out: list[Mention] = []
        for typ in ("statuses", "accounts"):
            for page in range(self.pages):
                data = await self.fetcher.json(f"{base}/api/v2/search", headers=auth, params={
                    "q": q.raw, "type": typ, "limit": "40", "offset": str(page * 40), "resolve": "true"})
                rows = data.get(typ, [])
                for s in rows:
                    if typ == "statuses":
                        out.append(self.hit(q, s["url"] or s["uri"], title=s["account"]["acct"],
                                            body=strip_tags(s.get("content", "")), kind="post",
                                            author=s["account"]["acct"], published=s.get("created_at", "")))
                    else:
                        out.append(self.hit(q, s["url"], title=s.get("display_name", ""),
                                            body=f"{s.get('acct', '')} {strip_tags(s.get('note', ''))}",
                                            kind="profile", author=s.get("acct", "")))
                if len(rows) < 40:
                    break
        return out


@register
class YouTube(Source):
    name = "youtube"
    group = "social"
    env = ("YOUTUBE_API_KEY",)
    description = "YouTube videos and channels (title/description) via Data API v3"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for typ in ("video", "channel"):
            token = None
            for _ in range(self.pages):
                params = {"part": "snippet", "q": f'"{q.raw}"', "type": typ, "maxResults": "50",
                          "key": self.key("YOUTUBE_API_KEY")}
                if token:
                    params["pageToken"] = token
                data = await self.fetcher.json("https://www.googleapis.com/youtube/v3/search", params=params)
                for it in data.get("items", []):
                    sn = it["snippet"]
                    url = (f"https://www.youtube.com/watch?v={it['id']['videoId']}" if typ == "video"
                           else f"https://www.youtube.com/channel/{it['id']['channelId']}")
                    out.append(self.hit(q, url, title=sn.get("title", ""), body=sn.get("description", ""),
                                        kind="video" if typ == "video" else "profile",
                                        author=sn.get("channelTitle", ""), published=sn.get("publishedAt", "")))
                token = data.get("nextPageToken")
                if not token:
                    break
        return out


@register
class StackExchange(Source):
    name = "stackexchange"
    group = "social"
    description = "Stack Overflow, Super User, Server Fault, Ask Ubuntu, Math SE: questions, answers, users"

    SITES = ("stackoverflow", "superuser", "serverfault", "askubuntu", "math")

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for site in self.SITES:
            data = await self.fetcher.json("https://api.stackexchange.com/2.3/search/excerpts", params={
                "q": f'"{q.raw}"', "site": site, "pagesize": "100", "order": "desc", "sort": "creation"})
            for it in data.get("items", []):
                pid = it.get("answer_id") or it.get("question_id")
                kind = "a" if it.get("item_type") == "answer" else "q"
                out.append(self.hit(q, f"https://{site}.com/{kind}/{pid}" if site != "math"
                                    else f"https://math.stackexchange.com/{kind}/{pid}",
                                    title=it.get("title", ""), body=strip_tags(it.get("body", it.get("excerpt", ""))),
                                    kind="post", published=_ts(it.get("creation_date"))))
            users = await self.fetcher.json("https://api.stackexchange.com/2.3/users", params={
                "inname": q.raw, "site": site, "pagesize": "100"})
            for u in users.get("items", []):
                out.append(self.hit(q, u["link"], title=u.get("display_name", ""),
                                    body=u.get("display_name", ""), kind="profile"))
        return out


@register
class ProfileProbe(Source):
    name = "profiles"
    group = "platform"
    description = "Checks likely usernames (akshatsarkar, akshat_sarkar, …) on platforms with public profile pages"

    async def search(self, q: Query) -> list[Mention]:
        out: list[Mention] = []
        for h in q.handle_guesses():
            probes = [
                (f"https://api.github.com/users/{h}", f"https://github.com/{h}", ("name", "bio", "login")),
                (f"https://www.reddit.com/user/{h}/about.json", f"https://www.reddit.com/user/{h}", ()),
                (f"https://dev.to/api/users/by_username?url={h}", f"https://dev.to/{h}", ("name", "summary")),
                (f"https://hacker-news.firebaseio.com/v0/user/{h}.json", f"https://news.ycombinator.com/user?id={h}", ("about",)),
                (f"https://gitlab.com/api/v4/users?username={h}", f"https://gitlab.com/{h}", ("name",)),
            ]
            for api, page, fields in probes:
                try:
                    r = await self.fetcher.get(api)
                except httpx.HTTPError:
                    continue
                if r.status_code != 200 or not r.content or r.text.strip() in ("null", "[]"):
                    continue
                data = r.json()
                if isinstance(data, list):
                    data = data[0] if data else {}
                if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
                    data = data["data"]
                text = " ".join(str(data.get(f, "")) for f in fields) if fields else ""
                m = self.hit(q, page, title=f"Profile: {h}", body=text, kind="profile")
                if not m.verified:
                    m.verify_note = "username exists; display name not confirmed"
                out.append(m)
        return out
