"""GitHub Trending 抓取与解析。

GitHub 无 trending 官方 API / RSS（调研结论 F1），唯一正路是 HTML 抓取。
解析逻辑全部隔离在本模块的 `parse_trending_html` 中；GitHub 改版时只需
修复这里，并更新 `ghtrend/fixtures/trending_sample.html` 回归用例。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from .models import TrendingRepo

_FIXTURE = Path(__file__).parent / "fixtures" / "trending_sample.html"

_NUM_RE = re.compile(r"[\d,]+")


def _parse_int(text: str) -> int:
    m = _NUM_RE.search(text or "")
    return int(m.group(0).replace(",", "")) if m else 0


def _clean_repo_href(href: str) -> str:
    """'/owner/repo' -> 'owner/repo'（去掉可能的结尾斜杠与 extra path）。"""
    parts = [p for p in href.split("/") if p]
    return "/".join(parts[:2])


def parse_repo_card(article: Any, language_query: str, period: str, captured_at: str) -> TrendingRepo | None:
    """解析单个 `article.Box-row`。字段缺失容忍（GitHub 改版常见）。"""
    a = article.select_one("h2 a[href^='/']")
    if a is None:
        return None
    repo = _clean_repo_href(a["href"])

    desc_el = article.select_one("p")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""

    stars = 0
    stars_el = article.select_one("a[href$='/stargazers']")
    if stars_el:
        stars = _parse_int(stars_el.get_text(" ", strip=True))

    period_stars = 0
    period_el = article.find(string=re.compile(r"stars (today|this week|this month)"))
    if period_el:
        period_stars = _parse_int(str(period_el))

    lang_el = article.select_one("span[itemprop='programmingLanguage']")
    language = lang_el.get_text(strip=True) if lang_el else ""

    return TrendingRepo(
        repo=repo,
        url=f"https://github.com/{repo}",
        description=description,
        stars=stars,
        period_stars=period_stars,
        language=language,
        language_query=language_query,
        period=period,
        captured_at=captured_at,
    )


def parse_trending_html(html: str, language_query: str, period: str, captured_at: str | None = None) -> list[TrendingRepo]:
    """解析 trending 页面 HTML，返回 repo 列表（纯函数，可测）。"""
    if captured_at is None:
        captured_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    soup = BeautifulSoup(html, "html.parser")
    repos: list[TrendingRepo] = []
    for article in soup.select("article.Box-row"):
        item = parse_repo_card(article, language_query, period, captured_at)
        if item:
            repos.append(item)
    return repos


def trending_url(base_url: str, language: str, period: str) -> str:
    base = base_url.rstrip("/")
    lang = "" if language in ("", "all") else "/" + language
    return f"{base}{lang}?since={period}"


def load_offline_fixture() -> str:
    """离线模式下使用的真实页面快照（gh:python daily，两行样本）。"""
    return _FIXTURE.read_text(encoding="utf-8")


def fetch_trending(
    language: str,
    period: str,
    *,
    base_url: str = "https://github.com/trending",
    user_agent: str = "ghtrend-bot/0.1",
    timeout: float = 20.0,
    offline: bool = False,
) -> str:
    """抓取 trending 页面 HTML；offline=True 时直接返回内置 fixture。"""
    if offline:
        return load_offline_fixture()
    url = trending_url(base_url, language, period)
    resp = requests.get(
        url,
        headers={"User-Agent": user_agent, "Accept": "text/html"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def write_snapshot(
    repos: list[TrendingRepo],
    out_dir: str | Path,
    language: str,
    period: str,
    captured_at: str,
) -> Path:
    """把一次抓取写成 JSON 快照：data/snapshots/{ts}-{lang}-{period}.json。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = captured_at.replace(":", "").replace("+00:00", "Z")
    path = out / f"{ts}-{language or 'all'}-{period}.json"
    payload = {
        "language": language,
        "period": period,
        "captured_at": captured_at,
        "repos": [r.__dict__ for r in repos],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_snapshot(path: str | Path) -> list[TrendingRepo]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [TrendingRepo(**r) for r in payload["repos"]]


def polite_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
