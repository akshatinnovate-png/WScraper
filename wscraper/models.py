"""Core data types shared across sources, verification and storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only track clicks and never change page content.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "s", "si",
}


def canonical_url(url: str) -> str:
    """Normalise a URL so the same page found by two sources dedupes to one row."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"
    host = parts.netloc.lower()
    for prefix in ("www.", "m.", "mobile.", "old.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    if host == "twitter.com":
        host = "x.com"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _TRACKING_PARAMS))
    return urlunsplit((scheme, host, path, query, ""))


@dataclass
class Mention:
    url: str
    title: str = ""
    snippet: str = ""
    source: str = ""            # which connector found it, e.g. "reddit"
    platform: str = ""          # where it lives, e.g. "reddit.com"
    kind: str = "web"           # web | news | post | comment | video | paper | code | profile | archive
    author: str = ""
    published: str = ""         # ISO-8601 when known
    verified: bool | None = None  # True: name seen on the page; False: fetched, not seen; None: not checked
    verify_note: str = ""
    found_by: list[str] = field(default_factory=list)
    first_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        if not self.platform:
            self.platform = urlsplit(canonical_url(self.url)).netloc
        if self.source and self.source not in self.found_by:
            self.found_by.append(self.source)

    @property
    def key(self) -> str:
        return canonical_url(self.url)

    def merge(self, other: "Mention") -> None:
        """Fold a duplicate hit from another source into this one, keeping the richest fields."""
        for name in ("title", "snippet", "author", "published"):
            if len(getattr(other, name)) > len(getattr(self, name)):
                setattr(self, name, getattr(other, name))
        if self.kind == "web" and other.kind != "web":
            self.kind = other.kind
        for s in other.found_by:
            if s not in self.found_by:
                self.found_by.append(s)
        if other.verified is not None and self.verified is not True:
            self.verified, self.verify_note = other.verified, other.verify_note

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d
