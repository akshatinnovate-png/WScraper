import asyncio
import base64

import httpx

from wscraper.http import Fetcher
from wscraper.query import Query
from wscraper.sources import REGISTRY
from wscraper.sources.engines import _bing_target, _ddg_target

Q = Query("Akshat Sarkar")


def search(name, routes):
    def handler(req: httpx.Request) -> httpx.Response:
        for (host, path), resp in routes.items():
            if req.url.host == host and req.url.path == path:
                return resp(req) if callable(resp) else resp
        return httpx.Response(404)

    async def go():
        async with Fetcher(transport=httpx.MockTransport(handler), min_interval=0, retries=0) as f:
            return await REGISTRY[name](f, pages=1).search(Q)
    return asyncio.run(go())


def test_redirect_decoding():
    target = "https://example.com/a?b=1"
    enc = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    assert _bing_target(f"https://www.bing.com/ck/a?!&&p=x&u=a1{enc}&ntb=1") == target
    assert _ddg_target("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x") == "https://example.com/a"


def test_google_news_feed_is_a_lead_not_proof():
    rss = """<?xml version="1.0"?><rss><channel><item><title>Akshat Sarkar wins award - The Hindu</title>
    <link>https://news.google.com/rss/articles/abc</link><pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate>
    <source url="https://thehindu.com">The Hindu</source></item></channel></rss>"""
    hits = search("googlenews", {("news.google.com", "/rss/search"): httpx.Response(200, text=rss)})
    assert hits and hits[0].kind == "news" and hits[0].published.startswith("2026-10-05")
    assert hits[0].verified is None  # feed titles get checked against the article


def test_wikipedia_and_wikidata():
    wp = {"query": {"search": [{"title": "IIT Alumni", "snippet": 'by <span>Akshat Sarkar</span>', "timestamp": "2026-01-01"}]}}
    wd = {"search": [{"id": "Q1", "label": "Akshat Sarkar", "description": "engineer"}]}
    routes = {(f"{l}.wikipedia.org", "/w/api.php"): httpx.Response(200, json=wp) for l in ("en", "hi", "bn", "de", "fr", "es")}
    routes[("www.wikidata.org", "/w/api.php")] = httpx.Response(200, json=wd)
    hits = search("wikipedia", routes)
    assert len(hits) == 7 and all(h.verified for h in hits)
    assert hits[0].url == "https://en.wikipedia.org/wiki/IIT_Alumni"


def test_github_rate_limit_keeps_partial_results(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    users = {"items": [{"login": "akshatsarkar", "html_url": "https://github.com/akshatsarkar"},
                       {"login": "asarkar99", "html_url": "https://github.com/asarkar99"}]}
    hits = search("github", {
        ("api.github.com", "/search/users"): httpx.Response(200, json=users),
        ("api.github.com", "/search/repositories"): httpx.Response(403),
        ("api.github.com", "/search/issues"): httpx.Response(403),
        ("api.github.com", "/search/commits"): httpx.Response(403),
    })
    assert [h.url for h in hits] == ["https://github.com/akshatsarkar", "https://github.com/asarkar99"]
    # A login spelling the name counts; any other login waits for the verifier to read the profile.
    assert [h.verified for h in hits] == [True, None]


def test_hackernews_and_crossref():
    hn = {"nbPages": 1, "hits": [{"objectID": "1", "title": "Akshat Sarkar launches X", "author": "a",
                                  "created_at": "2026-01-01T00:00:00Z"}]}
    assert search("hackernews", {("hn.algolia.com", "/api/v1/search_by_date"): httpx.Response(200, json=hn)})[0].verified
    cr = {"message": {"items": [{"DOI": "10.1/x", "title": ["Flood risk"], "author": [{"given": "Akshat", "family": "Sarkar"}]},
                                {"DOI": "10.1/y", "title": ["Other"], "author": [{"given": "A.", "family": "Sarkar"}]}]}}
    hits = search("crossref", {("api.crossref.org", "/works"): httpx.Response(200, json=cr)})
    assert [h.verified for h in hits] == [True, None]  # initials-only author is not claimed as a match


def test_wikipedia_sends_identifying_user_agent():
    seen = []

    def handler(req):
        seen.append(req.headers["user-agent"])
        return httpx.Response(200, json={"query": {"search": []}, "search": []})

    async def go():
        async with Fetcher(transport=httpx.MockTransport(handler), min_interval=0, retries=0) as f:
            await REGISTRY["wikipedia"](f, pages=1).search(Q)
    asyncio.run(go())
    assert seen and all(ua.startswith("WScraper/") for ua in seen)


def test_reddit_uses_oauth_when_credentials_set(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "sec")
    listing = {"data": {"after": None, "children": [
        {"kind": "t3", "data": {"permalink": "/r/a/comments/1/x/", "title": "Akshat Sarkar", "created_utc": 1}}]}}

    def search_route(req):
        assert req.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json=listing)

    hits = search("reddit", {
        ("www.reddit.com", "/api/v1/access_token"): httpx.Response(200, json={"access_token": "tok"}),
        ("oauth.reddit.com", "/search"): search_route,
    })
    assert hits and hits[0].url == "https://www.reddit.com/r/a/comments/1/x/" and hits[0].verified


def test_google_news_searches_each_year_and_aliases():
    queries = []

    def feed(req):
        queries.append((req.url.params["q"], req.url.params["ceid"]))
        return httpx.Response(200, text="<rss><channel></channel></rss>")

    async def go():
        q = Query("Akshat Sarkar", extra_aliases=["अक्षत सरकार"])
        async with Fetcher(transport=httpx.MockTransport(feed), min_interval=0, retries=0) as f:
            await REGISTRY["googlenews"](f, pages=1).search(q)
    asyncio.run(go())
    texts = {t for t, _ in queries}
    assert '"Akshat Sarkar" after:2015-01-01 before:2016-01-01' in texts
    assert '"अक्षत सरकार" after:2020-01-01 before:2021-01-01' in texts
    assert ('"Sarkar, Akshat"', "IN:en") in queries


def test_gdelt_keeps_years_that_worked(monkeypatch):
    from wscraper.sources import news
    monkeypatch.setattr(news.GDELT, "interval", 0)

    def gdelt(req):
        if req.url.params["startdatetime"].startswith("2019"):
            return httpx.Response(200, text="Your search contained a phrase that is too common.")
        if req.url.params["startdatetime"].startswith("2020"):
            return httpx.Response(200, json={"articles": [{"url": "https://paper.in/a", "title": "Akshat Sarkar wins",
                                                           "seendate": "20200105T101500Z", "domain": "paper.in"}]})
        return httpx.Response(200, json={})
    hits = search("gdelt", {("api.gdeltproject.org", "/api/v2/doc/doc"): gdelt})
    assert [h.url for h in hits] == ["https://paper.in/a"] and hits[0].published == "2020-01-05T10:15:00Z"


def test_platforms_fall_back_to_duckduckgo_and_hit_facebook_posts():
    bing_queries = []

    def bing(req):
        bing_queries.append(req.url.params["q"])
        if "facebook.com/groups" in req.url.params["q"]:
            return httpx.Response(200, text='<li class="b_algo"><h2><a href="https://www.facebook.com/groups/1/posts/2">'
                                            'Akshat Sarkar</a></h2><p>Congrats Akshat Sarkar</p></li>')
        return httpx.Response(429)

    ddg_page = ('<div class="result"><a class="result__a" href="https://www.instagram.com/p/xyz/">post</a>'
                '<div class="result__snippet">Akshat Sarkar</div></div>')

    def ddg(req):
        return httpx.Response(200, text=ddg_page if "instagram.com" in req.url.params["q"] else "<html></html>")

    hits = search("platforms", {("www.bing.com", "/search"): bing, ("html.duckduckgo.com", "/html/"): ddg})
    urls = {h.key: h for h in hits}
    assert urls["https://facebook.com/groups/1/posts/2"].kind == "post"
    assert "https://instagram.com/p/xyz" in urls
    assert any(q.endswith("site:facebook.com/permalink.php") for q in bing_queries)
