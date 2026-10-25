"""wscraper "Akshat Sarkar" — find every public mention of a name or topic."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from . import engine
from .http import Fetcher
from .query import Query
from .report import write_all
from .sources import REGISTRY
from .store import Store


def _csv(v: str | None) -> list[str] | None:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="wscraper", description=__doc__)
    p.add_argument("query", nargs="?", help='name or topic, e.g. "Akshat Sarkar"')
    p.add_argument("-a", "--alias", action="append", default=[],
                   help="extra spelling/nickname/handle to match too (repeatable)")
    p.add_argument("-s", "--sources", help="comma-separated sources to use (default: all available)")
    p.add_argument("-x", "--exclude", help="comma-separated sources to skip")
    p.add_argument("-p", "--pages", type=int, default=3, help="result pages per source and phrase (default 3)")
    p.add_argument("--crawl-depth", type=int, default=1,
                   help="follow links whose text names the query this many hops from confirmed pages (default 1)")
    p.add_argument("--max-verify", type=int, default=2000, help="cap on pages fetched for verification")
    p.add_argument("--no-verify", action="store_true", help="skip opening pages; trust search results")
    p.add_argument("--ignore-robots", action="store_true", help="fetch pages even where robots.txt disallows")
    p.add_argument("--include-rejected", action="store_true",
                   help="also report hits whose page did not contain the name")
    p.add_argument("-o", "--out", default="results", help="output directory (default ./results)")
    p.add_argument("--db", default="results/wscraper.db", help="history database (default results/wscraper.db)")
    p.add_argument("--watch", type=float, metavar="MINUTES", help="re-run every N minutes, reporting only new mentions")
    p.add_argument("--list-sources", action="store_true", help="show sources and which API keys they need")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    if not a.list_sources and not a.query:
        p.error("a query is required")
    return a


def list_sources() -> None:
    for name, cls in sorted(REGISTRY.items(), key=lambda kv: (kv[1].group, kv[0])):
        state = "ready" if cls.available() else "needs " + " + ".join(cls.env)
        print(f"  {name:14} [{cls.group:9}] {state:38} {cls.description}")


async def once(a: argparse.Namespace, store: Store) -> int:
    q = Query(a.query, extra_aliases=a.alias)
    t0 = time.monotonic()
    async with Fetcher(respect_robots=not a.ignore_robots) as f:
        result = await engine.run(
            q, sources=_csv(a.sources), exclude=_csv(a.exclude), pages=a.pages, verify=not a.no_verify,
            crawl_depth=a.crawl_depth, max_verify=a.max_verify, fetcher=f,
            progress=lambda s: print("  " + s, file=sys.stderr),
        )
    mentions = [m for m in result.mentions if a.include_rejected or m.verified is not False]
    new = store.save(q.raw, mentions)
    new_keys = {m.key for m in new}
    paths = write_all(result, Path(a.out), mentions, new_keys)

    confirmed = sum(m.verified is True for m in mentions)
    print(f"\n{len(mentions)} mentions of {q.raw!r} ({confirmed} confirmed, {len(new)} new) "
          f"in {time.monotonic() - t0:.0f}s")
    # On repeat runs, list only what is new; on the first run everything is new.
    for m in (new if len(new) < len(mentions) else mentions)[:25]:
        mark = {True: "✓", None: "?", False: "✗"}[m.verified]
        print(f"  {mark} [{m.platform}] {m.title[:80] or m.url}\n      {m.url}")
    errors = [s for s in result.sources if s.status == "error"]
    skipped = [s for s in result.sources if s.status == "skipped"]
    if errors:
        print("\nSources that failed this run: " + ", ".join(f"{s.name} ({s.detail[:60]})" for s in errors))
    if skipped:
        print("Sources not enabled (add API keys for more coverage): " + ", ".join(s.name for s in skipped))
    print("\nReport: " + "  ".join(str(p) for p in paths.values()))
    return 0


def main(argv: list[str] | None = None) -> int:
    a = parse(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    if a.list_sources:
        list_sources()
        return 0
    store = Store(a.db)
    try:
        if not a.watch:
            return asyncio.run(once(a, store))
        while True:
            asyncio.run(once(a, store))
            print(f"\nNext run in {a.watch:g} min (Ctrl-C to stop)")
            time.sleep(a.watch * 60)
    except KeyboardInterrupt:
        return 130
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
