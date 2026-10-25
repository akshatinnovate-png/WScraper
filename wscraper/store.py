"""SQLite history so repeat and watch-mode runs can tell which mentions are new."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Mention

SCHEMA = """
CREATE TABLE IF NOT EXISTS mentions (
    query      TEXT NOT NULL,
    key        TEXT NOT NULL,
    data       TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (query, key)
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def save(self, query: str, mentions: list[Mention]) -> list[Mention]:
        """Upsert mentions; return the ones never seen before for this query."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        qk = query.lower()
        new: list[Mention] = []
        with self.db:
            for m in mentions:
                row = self.db.execute("SELECT first_seen FROM mentions WHERE query=? AND key=?", (qk, m.key)).fetchone()
                if row:
                    m.first_seen = row[0]
                    self.db.execute("UPDATE mentions SET data=?, last_seen=? WHERE query=? AND key=?",
                                    (json.dumps(m.to_dict()), now, qk, m.key))
                else:
                    new.append(m)
                    self.db.execute("INSERT INTO mentions VALUES (?,?,?,?,?)",
                                    (qk, m.key, json.dumps(m.to_dict()), m.first_seen, now))
        return new

    def all(self, query: str) -> list[dict]:
        rows = self.db.execute("SELECT data FROM mentions WHERE query=? ORDER BY first_seen DESC", (query.lower(),))
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        self.db.close()
