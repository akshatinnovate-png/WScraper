"""End-to-end run against a fake internet served by httpx.MockTransport."""

import asyncio
import json
from pathlib import Path

import httpx

from wscraper import engine
from wscraper.http import Fetcher
from wscraper.query import Query
from wscraper.report import write_all
from wscraper.store import Store

DDG_HTML = """<html><body>
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fblog.example.com%2Fpost%3Futm_source%3Dddg">Team page</a>
<div class="result__snippet">Our engineer Akshat Sarkar built it.</div></div>
<div class="result"><a class="result__a" href="https://noise.example.com/x">Unrelated</a>
<div class="result__snippet">Akshat Sarkaria is someone else</div></div>
<div class="result"><a class="result__a" href="https://gone.example.com/old">Old article</a>
<div class="result__snippet">Akshat Sarkar quoted</div></div>
<div class="result"><a class="result__a" href="https://private.example.com/p">Private</a>
<div class="result__snippet">Akshat Sarkar</div></div>
<div class="result"><a class="result__a" href="https://www.linkedin.com/in/akshat-sarkar">Akshat Sarkar - LinkedIn</a>
<div class="result__snippet">Akshat Sarkar · Student</div></div>
</body></html>"""

BLOG_HTML = """<html><head><title>Team</title><meta property="article:published_time" content="2026-05-01T10:00:00Z">
</head><body><p>Meet Akshat Sarkar, who built AquaSentinel.</p>
<a href="https://news.example.org/feature">Profile of Akshat Sarkar</a>
<a href="https://other.example.org/">unrelated link</a></body></html>"""

FEATURE_HTML = "<html><body><h1>Feature</h1><p>Akshat Sarkar, 19, says...</p></body></html>"

REDDIT = {"data": {"after": None, "children": [
    {"kind": "t3", "data": {"permalink": "/r/india/comments/abc/x/", "title": "Shoutout to Akshat Sarkar",
                            "selftext": "", "author": "u1", "created_utc": 1767225600}},
    {"kind": "t1", "data": {"permalink": "/r/india/comments/abc/x/def/", "link_title": "Thread",
                            "body": "akshat sarkar is great", "author": "u2", "created_utc": 1767312000}},
]}}

HN = {"nbPages": 1, "hits": [{"objectID": "42", "title": None, "story_title": "Show HN",
                              "comment_text": "Nice work Akshat Sarkar", "author": "pg",
                              "created_at": "2026-01-02T00:00:00Z"}]}


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if path == "/robots.txt":
        if host == "private.example.com":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        return httpx.Response(404)
    if host == "html.duckduckgo.com":
        return httpx.Response(200, text=DDG_HTML if request.url.params["s"] == "0" else "<html></html>")
    if host == "www.reddit.com":
        return httpx.Response(200, json=REDDIT)
    if host == "hn.algolia.com":
        return httpx.Response(200, json=HN)
    if host == "blog.example.com":
        return httpx.Response(200, text=BLOG_HTML, headers={"content-type": "text/html"})
    if host == "news.example.org":
        return httpx.Response(200, text=FEATURE_HTML, headers={"content-type": "text/html"})
    if host == "noise.example.com":
        return httpx.Response(200, text="<p>Akshat Sarkaria</p>", headers={"content-type": "text/html"})
    if host == "gone.example.com":
        return httpx.Response(404)
    if host == "archive.org" and path == "/wayback/available":
        return httpx.Response(200, json={"archived_snapshots": {"closest": {
            "available": True, "url": "http://web.archive.org/web/2025/https://gone.example.com/old"}}})
    return httpx.Response(500, text="unexpected " + str(request.url))


def run(**kw):
    async def go():
        async with Fetcher(transport=httpx.MockTransport(handler), min_interval=0, retries=0) as f:
            return await engine.run(Query("Akshat Sarkar"), fetcher=f, pages=2, **kw)
    return asyncio.run(go())


def by_url(result):
    return {m.key: m for m in result.mentions}


def test_full_pipeline():
    r = run(sources=["duckduckgo", "reddit", "hackernews"])
    m = by_url(r)

    blog = m["https://blog.example.com/post"]  # tracking param stripped, deduped
    assert blog.verified is True and blog.published.startswith("2026-05-01")
    assert "duckduckgo" in blog.found_by

    # Snowballed from the blog's link whose anchor text names the person.
    feature = m["https://news.example.org/feature"]
    assert feature.verified is True and feature.found_by == ["crawl"]
    assert "https://other.example.org/" not in m

    assert m["https://noise.example.com/x"].verified is False          # near-miss rejected
    gone = m["https://gone.example.com/old"]
    assert "web.archive.org" in gone.verify_note                        # deleted page recovered
    assert m["https://private.example.com/p"].verify_note.startswith("robots.txt")
    li = m["https://linkedin.com/in/akshat-sarkar"]
    assert li.verified is None and "snippet" in li.verify_note           # login wall, snippet evidence

    kinds = {x.kind for x in r.mentions if "reddit.com" in x.platform}
    assert kinds == {"post", "comment"}
    assert m["https://news.ycombinator.com/item?id=42"].verified is True

    assert {s.name: s.status for s in r.sources} == {"duckduckgo": "ok", "hackernews": "ok", "reddit": "ok"}
    # Confirmed results come first.
    assert r.mentions[0].verified is True


def test_failed_source_does_not_sink_run():
    r = run(sources=["duckduckgo", "wikipedia"], verify=False)
    status = {s.name: s.status for s in r.sources}
    assert status == {"duckduckgo": "ok", "wikipedia": "error"}
    assert r.mentions


def test_keyed_sources_are_skipped_without_keys(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    names, skipped = engine.select_sources(["youtube", "reddit"])
    assert names == ["reddit"] and skipped[0].name == "youtube"


def test_store_reports_only_new_and_reports_write(tmp_path):
    r = run(sources=["reddit"], verify=False)
    store = Store(tmp_path / "db.sqlite")
    assert len(store.save(r.query, r.mentions)) == 2
    assert store.save(r.query, r.mentions) == []
    paths = write_all(r, tmp_path / "out", r.mentions, set())
    data = json.loads(paths["json"].read_text())
    assert data["query"] == "Akshat Sarkar" and len(data["mentions"]) == 2
    assert "Akshat Sarkar" in paths["html"].read_text()
    assert paths["csv"].read_text().count("\n") == 3


def test_reports_write_on_non_utf8_locale(tmp_path):
    """Windows defaults to cp1252; reports with non-Latin text must still save (regression)."""
    import subprocess
    import sys
    script = f"""
from pathlib import Path
from wscraper.engine import RunResult
from wscraper.models import Mention
from wscraper.report import write_all
m = Mention(url="https://e.com/a", title="\\u0985\\u0995\\u09cd\\u09b7\\u09a4 \\u2026 Akshat Sarkar \\u2713", source="bing")
write_all(RunResult("Akshat Sarkar", [m]), Path({str(tmp_path)!r}), [m], set())
"""
    env = {"PATH": "", "PYTHONUTF8": "0", "LC_ALL": "C", "LANG": "C", "PYTHONPATH": str(Path(__file__).parents[1])}
    r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "অক্ষত" in (tmp_path / "akshat-sarkar.json").read_text(encoding="utf-8")
