"""trending 页面解析回归测试（基于真实页面快照 fixture）。"""

from pathlib import Path

from bs4 import BeautifulSoup

import ghtrend.scrape as scrape_mod
from ghtrend.scrape import parse_repo_card, parse_trending_html, trending_url

FIXTURE_PATH = Path(scrape_mod.__file__).parent / "fixtures" / "trending_sample.html"


def _parse():
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    return parse_trending_html(html, "python", "daily", "2026-10-12T12:00:00+00:00")


def test_fixture_parses_two_rows():
    repos = _parse()
    assert len(repos) == 2


def test_first_row_fields():
    repos = _parse()
    r = repos[0]
    assert r.repo == "open-webui/open-webui"
    assert r.url == "https://github.com/open-webui/open-webui"
    assert r.stars == 154440
    assert r.period_stars == 142
    assert r.language == "Python"
    assert "AI Interface" in r.description
    assert r.language_query == "python"
    assert r.period == "daily"
    assert r.captured_at == "2026-10-12T12:00:00+00:00"


def test_row_without_period_stars_is_tolerated():
    """真实页面部分行没有 'N stars today' 文案，解析不得失败。"""
    repos = _parse()
    r = repos[1]
    assert r.repo == "Alishahryar1/free-claude-code"
    assert r.stars == 58313
    assert r.period_stars == 0


def test_malformed_card_is_skipped():
    soup = BeautifulSoup("<article class='Box-row'><p>no link here</p></article>", "html.parser")
    card = soup.select_one("article.Box-row")
    assert parse_repo_card(card, "python", "daily", "t") is None


def test_trending_url():
    assert trending_url("https://github.com/trending", "go", "weekly") == (
        "https://github.com/trending/go?since=weekly"
    )
    assert trending_url("https://github.com/trending/", "all", "daily") == (
        "https://github.com/trending?since=daily"
    )
