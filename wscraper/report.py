"""Write results as JSON, CSV and a self-contained, filterable HTML report."""

from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .engine import RunResult
from .models import Mention

CSV_FIELDS = ["verified", "kind", "platform", "title", "url", "snippet", "author", "published",
              "found_by", "verify_note", "first_seen"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "query"


def write_all(result: RunResult, out_dir: Path, mentions: list[Mention], new_keys: set[str]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / slug(result.query)
    paths = {"json": base.with_suffix(".json"), "csv": base.with_suffix(".csv"), "html": base.with_suffix(".html")}
    payload = {
        "query": result.query,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages_fetched": result.pages_fetched,
        "sources": [vars(s) for s in result.sources],
        "mentions": [dict(m.to_dict(), new=m.key in new_keys) for m in mentions],
    }
    paths["json"].write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    with paths["csv"].open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS + ["new"])
        w.writeheader()
        for m in mentions:
            d = m.to_dict()
            row = {k: d[k] for k in CSV_FIELDS}
            row["found_by"] = ";".join(m.found_by)
            row["new"] = m.key in new_keys
            w.writerow(row)
    paths["html"].write_text(render_html(result, mentions, new_keys), encoding="utf-8")
    return paths


def _status(m: Mention) -> tuple[str, str]:
    return {True: ("ok", "Confirmed"), None: ("maybe", "Unconfirmed"), False: ("no", "Not found")}[m.verified]


def render_html(result: RunResult, mentions: list[Mention], new_keys: set[str]) -> str:
    e = html.escape
    by_platform = Counter(m.platform for m in mentions)
    by_kind = Counter(m.kind for m in mentions)
    confirmed = sum(m.verified is True for m in mentions)
    rows = []
    for m in mentions:
        cls, label = _status(m)
        rows.append(f"""<tr data-s="{cls}" data-k="{e(m.kind)}">
<td><span class="b {cls}" title="{e(m.verify_note)}">{label}</span>{' <span class="b new">New</span>' if m.key in new_keys else ''}</td>
<td><a href="{e(m.url)}" target="_blank" rel="noopener noreferrer">{e(m.title or m.url)}</a>
<div class="u">{e(m.url)}</div><div class="sn">{e(m.snippet)}</div></td>
<td>{e(m.platform)}<div class="u">{e(m.kind)}</div></td>
<td>{e(m.published[:10])}</td><td class="u">{e(', '.join(m.found_by))}</td></tr>""")
    src_rows = "".join(
        f"<li><b>{e(s.name)}</b> — {e(s.status)}{f', {s.hits} hits' if s.status == 'ok' else ''}"
        f"{f' <span class=u>({e(s.detail)})</span>' if s.detail else ''}</li>" for s in result.sources)
    chips = "".join(f'<button data-kind="{e(k)}">{e(k)} ({n})</button>' for k, n in by_kind.most_common())
    top = ", ".join(f"{e(p)} ({n})" for p, n in by_platform.most_common(12))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mentions: {e(result.query)}</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e5e5e5;--ok:#1b7f3b;--maybe:#a66300;--no:#999;--acc:#2456d6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141414;--fg:#eee;--mut:#9a9a9a;--line:#2c2c2c;--ok:#4cc36f;--maybe:#e0a040;--no:#777;--acc:#7aa2ff}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px}}
h1{{margin:.2em 0}}.u{{color:var(--mut);font-size:12px;word-break:break-all}}.sn{{margin-top:4px}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}}
a{{color:var(--acc)}}.b{{font-size:11px;padding:2px 6px;border-radius:9px;border:1px solid currentColor;white-space:nowrap}}
.ok{{color:var(--ok)}}.maybe{{color:var(--maybe)}}.no{{color:var(--no)}}.new{{color:var(--acc)}}
input{{width:100%;padding:8px;font-size:15px;box-sizing:border-box;background:var(--bg);color:var(--fg);border:1px solid var(--line)}}
button{{margin:4px 4px 0 0;background:none;color:var(--fg);border:1px solid var(--line);border-radius:12px;padding:3px 10px;cursor:pointer}}
button.on{{border-color:var(--acc);color:var(--acc)}}
@media (max-width:700px){{td:nth-child(4),td:nth-child(5),th:nth-child(4),th:nth-child(5){{display:none}}}}
</style></head><body>
<h1>Mentions of “{e(result.query)}”</h1>
<p><b>{len(mentions)}</b> results · <b>{confirmed}</b> confirmed on the live page · {len(new_keys)} new since last run ·
{result.pages_fetched} pages checked</p>
<p class="u">Top platforms: {top}</p>
<input id="f" placeholder="Filter by any text…">
<div><button data-s="ok">Confirmed</button><button data-s="maybe">Unconfirmed</button><button data-s="no">Not found</button>{chips}</div>
<table><thead><tr><th>Status</th><th>Mention</th><th>Where</th><th>Date</th><th>Found by</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<details><summary>Sources</summary><ul>{src_rows}</ul></details>
<script>
const f=document.getElementById('f'),rows=[...document.querySelectorAll('tbody tr')];let s=null,k=null;
function apply(){{const t=f.value.toLowerCase();rows.forEach(r=>{{r.hidden=!(r.textContent.toLowerCase().includes(t)&&(!s||r.dataset.s===s)&&(!k||r.dataset.k===k))}})}}
document.querySelectorAll('button').forEach(b=>b.onclick=()=>{{
 if(b.dataset.s){{s=s===b.dataset.s?null:b.dataset.s}}else{{k=k===b.dataset.kind?null:b.dataset.kind}}
 document.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x.dataset.s?x.dataset.s===s:x.dataset.kind===k));apply()}});
f.oninput=apply;
</script></body></html>"""
