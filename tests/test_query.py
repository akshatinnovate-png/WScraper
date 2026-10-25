import pytest

from wscraper.models import Mention, canonical_url
from wscraper.query import Query

Q = Query("Akshat Sarkar")


@pytest.mark.parametrize("text", [
    "Interview with Akshat Sarkar on water risk",
    "AKSHAT SARKAR wins hackathon",
    "Authors: Sarkar, Akshat and Doe, J.",
    "Sarkar Akshat (2024). A paper.",
    "follow @akshat_sarkar for updates",
    "github.com/AkshatSarkar",
    "akshat-sarkar on linkedin",
    "Akshat  Sarkar\nengineer",
    "Ákshat Sárkar",
])
def test_matches_variants(text):
    assert Q.matches(text)


@pytest.mark.parametrize("text", [
    "Akshat Sarkaria spoke today",
    "Pakshat Sarkar",
    "Akshat Kumar and Rahul Sarkar",
    "Akshat",
    "",
])
def test_rejects_near_misses(text):
    assert not Q.matches(text)


def test_alias_and_phrases():
    q = Query("Akshat Sarkar", extra_aliases=["Akki Sarkar"])
    assert q.matches("Akki Sarkar posted")
    phrases = q.search_phrases()
    assert phrases[0] == "Akshat Sarkar"
    assert "Sarkar, Akshat" in phrases and "AkshatSarkar" in phrases and "Akki Sarkar" in phrases
    assert {"akshatsarkar", "akshat_sarkar", "akshat.sarkar"} <= set(q.handle_guesses())


def test_snippet_centres_on_mention():
    text = "x " * 500 + "met Akshat Sarkar today" + " y" * 500
    s = Q.snippet(text, width=20)
    assert "Akshat Sarkar" in s and s.startswith("…") and s.endswith("…")


def test_empty_query_rejected():
    with pytest.raises(ValueError):
        Query("  ,, ")


def test_canonical_url_dedupes_variants():
    a = canonical_url("http://www.twitter.com/akshat/status/1/?utm_source=x&ref=y")
    b = canonical_url("https://x.com/akshat/status/1")
    assert a == b
    assert canonical_url("https://m.reddit.com/r/a/?b=2&a=1") == canonical_url("https://reddit.com/r/a?a=1&b=2")


def test_merge_keeps_richest_fields():
    a = Mention(url="https://e.com/p", title="t", source="bing")
    b = Mention(url="https://e.com/p", title="longer title", snippet="snip", source="duckduckgo", kind="news")
    a.merge(b)
    assert a.title == "longer title" and a.kind == "news" and a.found_by == ["bing", "duckduckgo"]


HINDI = Query("Akshat Sarkar", extra_aliases=["अक्षत सरकार", "অক্ষত সরকার"])


@pytest.mark.parametrize("text,expected", [
    ("अक्षत सरकार ने कहा", True),
    ("। अक्षत सरकार।", True),
    ("অক্ষত সরকার বলেন", True),
    ("अक्षय सरकार ने कहा", False),   # Akshay, not Akshat: vowel marks must not be stripped
    ("अक्षत सरकारी", False),          # "sarkari" (government) is a different word
    ("akshat sarkar", True),
])
def test_indic_spellings(text, expected):
    assert HINDI.matches(text) is expected


def test_indic_query_tokenizes_whole_words():
    assert Query("अक्षत सरकार").tokens == ["अक्षत", "सरकार"]
