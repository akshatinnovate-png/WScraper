"""Run every source in parallel, merge duplicates, verify, and snowball through linked pages."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from .http import Fetcher
from .models import Mention
from .query import Query
from .sources import REGISTRY
from .verify import Verifier

log = logging.getLogger(__name__)


@dataclass
class SourceReport:
    name: str
    status: str            # ok | error | skipped
    hits: int = 0
    seconds: float = 0.0
    detail: str = ""


@dataclass
class RunResult:
    query: str
    mentions: list[Mention]
    sources: list[SourceReport] = field(default_factory=list)
    pages_fetched: int = 0


def select_sources(names: list[str] | None, exclude: list[str] | None = None) -> tuple[list[str], list[SourceReport]]:
    """Sources to run, plus 'skipped' reports for those missing API keys."""
    wanted = names or list(REGISTRY)
    unknown = [n for n in wanted if n not in REGISTRY]
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(unknown)}. Try --list-sources.")
    run, skipped = [], []
    for n in wanted:
        if exclude and n in exclude:
            continue
        cls = REGISTRY[n]
        if cls.available():
            run.append(n)
        else:
            skipped.append(SourceReport(n, "skipped", detail="set " + ", ".join(cls.env)))
    return run, skipped


async def run(
    q: Query,
    *,
    sources: list[str] | None = None,
    exclude: list[str] | None = None,
    pages: int = 3,
    verify: bool = True,
    crawl_depth: int = 1,
    max_verify: int = 2000,
    concurrency: int = 16,
    fetcher: Fetcher | None = None,
    progress: Callable[[str], None] = lambda s: None,
) -> RunResult:
    names, reports = select_sources(sources, exclude)
    own = fetcher is None
    f = fetcher or Fetcher()
    try:
        merged: dict[str, Mention] = {}

        def add(ms: list[Mention]) -> list[Mention]:
            fresh = []
            for m in ms:
                if not m.url.startswith("http"):
                    continue
                if m.key in merged:
                    merged[m.key].merge(m)
                else:
                    merged[m.key] = m
                    fresh.append(m)
            return fresh

        async def one(name: str) -> None:
            t0 = time.monotonic()
            try:
                hits = await REGISTRY[name](f, pages=pages).search(q)
            except Exception as exc:  # a broken/blocked source must never sink the run
                reports.append(SourceReport(name, "error", seconds=time.monotonic() - t0,
                                            detail=f"{type(exc).__name__}: {exc}"[:200]))
                progress(f"✗ {name}: {type(exc).__name__}")
                return
            add(hits)
            reports.append(SourceReport(name, "ok", hits=len(hits), seconds=time.monotonic() - t0))
            progress(f"✓ {name}: {len(hits)} hits")

        progress(f"searching {len(names)} sources for {q.raw!r}")
        await asyncio.gather(*(one(n) for n in names))

        verifier = Verifier(f, q, crawl_depth=crawl_depth, max_pages=max_verify)
        if verify:
            frontier = list(merged.values())
            sem = asyncio.Semaphore(concurrency)
            for depth in range(crawl_depth + 1):
                if not frontier:
                    break
                progress(f"verifying {len(frontier)} pages (depth {depth})")

                async def check(m: Mention, d: int = depth) -> list[Mention]:
                    async with sem:
                        return await verifier.check(m, d)

                leads = await asyncio.gather(*(check(m) for m in frontier))
                frontier = add([lead for batch in leads for lead in batch])
                if frontier:
                    progress(f"found {len(frontier)} new linked pages")

        # Confirmed first, then unconfirmed, then rejected; newest first within each (sorts are stable).
        mentions = sorted(merged.values(), key=lambda m: m.published or "", reverse=True)
        mentions.sort(key=lambda m: {True: 0, None: 1, False: 2}[m.verified])
        return RunResult(q.raw, mentions, sorted(reports, key=lambda r: r.name), verifier.fetched)
    finally:
        if own:
            await f.__aexit__(None, None, None)
