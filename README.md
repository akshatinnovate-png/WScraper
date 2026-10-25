# WScraper

Find every public mention of a name or topic across the web: search engines, news, social platforms, code, academia and archives. Each hit is checked against the live page.

```bash
pip install -e .
wscraper "Akshat Sarkar"
```

It searches more than 20 sources in parallel, merges duplicates, opens each page to confirm the name is really on it, and follows links on confirmed pages that name you. It writes a filterable HTML report plus JSON and CSV. Each run is saved, so the next run reports only what is **new**.

## What it searches

| Group | Sources | Key needed |
|---|---|---|
| Web search | DuckDuckGo, Bing, Mojeek (an independent index) | — |
| | Google (Programmable Search), Google via SerpAPI, Brave Search | yes |
| Platforms | `site:` searches on about 40 platforms: LinkedIn, X/Twitter, Instagram, Facebook, Threads, TikTok, YouTube, Medium, Substack, Quora, Pinterest, ResearchGate, Crunchbase, Kaggle, Devpost, Behance… plus PDF/DOC/PPT/XLS files | — |
| | Username probes (`akshatsarkar`, `akshat_sarkar`, …) on GitHub, Reddit, GitLab, dev.to and Hacker News | — |
| News | Google News (9 country/language editions incl. India, Hindi, Bengali), Bing News, GDELT (global news, 65 languages) | — |
| Social | Reddit posts and comments, Hacker News stories and comments, Stack Exchange (5 sites) | — |
| | Bluesky, Mastodon/fediverse, YouTube | yes (free) |
| Code | GitHub users, repositories, issues/PRs, commits (code search when `GITHUB_TOKEN` is set) | optional |
| Reference | Wikipedia full text (6 languages), Wikidata | — |
| Academic | Crossref, OpenAlex, arXiv | — |
| Archive | Internet Archive items; Wayback Machine copies of deleted pages | — |

Run `wscraper --list-sources` to see which are active.

## How it avoids misses *and* false hits

- **Name variants.** It searches and matches `Akshat Sarkar`, `Sarkar, Akshat` (bylines, citations), `AkshatSarkar`, `akshat_sarkar`, `akshat-sarkar` and accented spellings. Add nicknames, handles or other scripts with `-a`: `-a "अक्षत सरकार" -a "অক্ষত সরকার"`. Hindi and Bengali are matched exactly, so अक्षय (Akshay) or सरकारी (sarkari) don't count. Aliases are also searched on news and platform sites.
- **Exact matching.** `Akshat Sarkaria` and `Akshat Kumar and Rahul Sarkar` do **not** count.
- **Verification.** Search snippets are treated as leads only. Every page is opened, and a result is marked:
  - ✓ **Confirmed**: the name is on the live page, or in the post/comment text returned by the platform API.
  - ? **Unconfirmed**: the page is behind a login (LinkedIn, Instagram…), blocked by robots.txt, or rendered with JavaScript, but the search snippet shows the name.
  - ✗ **Not found**: the page was opened and the name isn't on it. These are hidden unless you pass `--include-rejected`.
- **Deleted content.** If a page now returns 404, the tool looks up the Wayback Machine and links the archived copy.
- **Snowballing.** Links on confirmed pages whose text names you are followed (`--crawl-depth`, default 1). This reaches pages no search engine ranks.

## Usage

```bash
wscraper "Akshat Sarkar"                          # everything available
wscraper "Akshat Sarkar" -a "अक्षत सरकार"          # also find Hindi mentions
wscraper "Akshat Sarkar" -a "Akki Sarkar" -p 5    # extra alias, deeper result pages
wscraper "Akshat Sarkar" -s googlenews,gdelt,bingnews   # only news
wscraper "Akshat Sarkar" -x platforms,profiles    # skip some sources
wscraper "Akshat Sarkar" --crawl-depth 2          # follow linked pages further
wscraper "Akshat Sarkar" --watch 360              # re-run every 6 h, report only new mentions
```

Results go to `results/<query>.html|json|csv`. History is kept in `results/wscraper.db`.

## API keys (optional, more coverage)

Set these as environment variables. Sources without keys are skipped, and the run says which ones were skipped.

| Variable | Unlocks | Where |
|---|---|---|
| `SERPAPI_KEY` | Real Google web + news results (the biggest single coverage gain) | serpapi.com |
| `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` | Google Programmable Search (100 results/query) | programmablesearchengine.google.com |
| `BRAVE_API_KEY` | Brave's independent index | brave.com/search/api |
| `YOUTUBE_API_KEY` | YouTube videos and channels | Google Cloud console |
| `BSKY_HANDLE` + `BSKY_APP_PASSWORD` | Bluesky posts | Bluesky → Settings → App passwords |
| `MASTODON_INSTANCE` + `MASTODON_TOKEN` | Fediverse posts | your instance → Preferences → Development |
| `GITHUB_TOKEN` | GitHub code search and higher rate limits | github.com/settings/tokens |
| `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` | Reddit, when it blocks anonymous requests (it usually does) | reddit.com/prefs/apps → create a "script" app |

## Honest limits: what no tool can reach

"Nothing missed" is the goal this tool is built around, but some content can't be reached by *any* scraper, so it's better to know where the gaps are:

- **Private or login-only content.** Private Instagram/Facebook accounts, closed groups, WhatsApp/Telegram chats, Discord servers, and LinkedIn posts that need a login. The tool finds whatever these platforms expose to search engines (public profiles and many public posts) and marks it *Unconfirmed*.
- **X/Twitter and Instagram full-text search** need paid or approved API access. Coverage there comes through search-engine `site:` queries, which find much but not all of it.
- **Pages no search engine has indexed** and that nothing links to. Snowball crawling helps but cannot find orphan pages.
- **Paywalled news.** The article is still listed, because the headline or snippet names you, but the full text can't be checked.
- **Rate limits.** Free engines throttle heavy scraping. A run that hits a limit lists the affected sources, and re-running later, or adding API keys, fills the gaps. `--watch` mode catches new mentions as they're indexed.

## Responsible use

WScraper only reads public pages. It respects `robots.txt` by default and paces requests to each host. It is meant for monitoring your own name, brand or project, or for research on public topics. Don't use it to track or harass private individuals, and follow each platform's terms.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests run the full pipeline against a mocked internet and need no network.

Adding a source is one class in `wscraper/sources/`: subclass `Source`, implement `async search(q)`, decorate it with `@register`, and set `authoritative = False` if its text is only a search snippet.
