"""Polite async HTTP: per-host concurrency and pacing, retries with backoff, robots.txt."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
BOT_UA = "WScraper/1.0 (+https://github.com/akshatinnovate-png/WScraper; mention monitor)"


class Fetcher:
    def __init__(
        self,
        *,
        per_host: int = 2,
        min_interval: float = 1.0,
        total: int = 32,
        timeout: float = 20.0,
        retries: int = 3,
        respect_robots: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
            transport=transport,
            limits=httpx.Limits(max_connections=total),
        )
        self._per_host = per_host
        self._min_interval = min_interval
        self._retries = retries
        self._respect_robots = respect_robots
        self._global = asyncio.Semaphore(total)
        self._host_sems: dict[str, asyncio.Semaphore] = {}
        self._host_last: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    def _host(self, url: str) -> str:
        return urlsplit(url).netloc.lower()

    async def _pace(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0) + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def allowed(self, url: str) -> bool:
        """robots.txt check for page fetches (API calls skip it; they are the site's sanctioned interface)."""
        if not self._respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                r = await self._client.get(origin + "/robots.txt", headers={"User-Agent": BOT_UA}, timeout=10)
                if r.status_code >= 400:
                    rp = None  # no robots.txt: everything allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is None or rp.can_fetch(BOT_UA, url)

    async def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
                  ua: str | None = None) -> httpx.Response:
        host = self._host(url)
        sem = self._host_sems.setdefault(host, asyncio.Semaphore(self._per_host))
        hdrs = dict(headers or {})
        if ua:
            hdrs["User-Agent"] = ua
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            async with self._global, sem:
                await self._pace(host)
                try:
                    resp = await self._client.get(url, params=params, headers=hdrs)
                except httpx.HTTPError as exc:
                    last_exc = exc
                    resp = None
            if resp is not None:
                if resp.status_code not in (429, 500, 502, 503, 504):
                    return resp
                retry_after = resp.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)
                last_exc = httpx.HTTPStatusError(f"{resp.status_code} from {host}", request=resp.request, response=resp)
            else:
                delay = 2 ** (attempt + 1)
            if attempt < self._retries:
                await asyncio.sleep(min(delay, 60) + random.random())
        assert last_exc is not None
        raise last_exc

    async def json(self, url: str, **kw) -> dict | list:
        r = await self.get(url, **kw)
        r.raise_for_status()
        return r.json()

    async def post_json(self, url: str, payload: dict) -> dict:
        r = await self._client.post(url, json=payload)
        r.raise_for_status()
        return r.json()

    async def post_form(self, url: str, data: dict, *, auth: tuple[str, str] | None = None,
                        ua: str | None = None) -> dict:
        r = await self._client.post(url, data=data, auth=auth, headers={"User-Agent": ua} if ua else None)
        r.raise_for_status()
        return r.json()

    async def text(self, url: str, **kw) -> str:
        r = await self.get(url, **kw)
        r.raise_for_status()
        return r.text
