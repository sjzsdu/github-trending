"""GitHub REST enrich：元数据 + README + 顶层目录树。

token 走环境变量 GHTREND_GITHUB_TOKEN；无 token 时优雅降级为
未认证请求（60 req/h 限额，够单机每日选题 enrich 用）。
任何一步失败都不拖垮流水线，只在 warnings 里记一笔。
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from .config import github_token
from .models import RepoDetails


def _headers(raw: bool = False) -> dict[str, str]:
    h = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    token = github_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(
    session: requests.Session, url: str, timeout: float, headers: dict[str, str] | None = None
) -> requests.Response | None:
    try:
        resp = session.get(url, headers=headers or _headers(), timeout=timeout)
        if resp.status_code == 200:
            return resp
        return None
    except requests.RequestException:
        return None


def mock_details(repo: str) -> RepoDetails:
    """离线/mock 模式的 canned 仓库详情（结构真实、数字标注为演示）。"""
    owner, name = repo.split("/", 1)
    return RepoDetails(
        repo=repo,
        url=f"https://github.com/{repo}",
        description=f"{name}: mock 演示用仓库描述（离线模式）",
        stars=12345,
        forks=678,
        open_issues=42,
        created_at="2024-01-01T00:00:00Z",
        pushed_at="2026-10-12T00:00:00Z",
        language="Python",
        homepage="",
        license="MIT License",
        topics=["mock", "demo"],
        default_branch="main",
        readme=(
            f"# {name}\n\n"
            "这是离线模式的 canned README，用于让流水线在无网络环境下端到端运行。\n\n"
            "## Features\n"
            "- feature A：示例能力一\n"
            "- feature B：示例能力二\n\n"
            "## Quick Start\n"
            f"```bash\npip install {name}\n```\n"
        ),
        tree=["README.md", "src", "tests", "pyproject.toml", "docs"],
        warnings=["offline mock data (GitHub REST not called)"],
    )


def enrich_repo(repo: str, *, api_base: str = "https://api.github.com", timeout: float = 15.0) -> RepoDetails:
    """拉取仓库元数据 + README + 顶层树。失败降级，不抛异常。"""
    details = RepoDetails(repo=repo)
    session = requests.Session()
    base = api_base.rstrip("/")

    meta = _get(session, f"{base}/repos/{repo}", timeout)
    if meta is None:
        details.warnings.append("metadata fetch failed (rate limit or network)")
        # 不 early-return：README/树走独立回退路径，尽量补齐材料
        details.default_branch = "main"
    else:
        data = meta.json()
        details.url = data.get("html_url", f"https://github.com/{repo}")
        details.description = data.get("description") or ""
        details.stars = data.get("stargazers_count", 0)
        details.forks = data.get("forks_count", 0)
        details.open_issues = data.get("open_issues_count", 0)
        details.created_at = data.get("created_at", "")
        details.pushed_at = data.get("pushed_at", "")
        details.language = data.get("language") or ""
        details.homepage = data.get("homepage") or ""
        details.topics = data.get("topics", []) or []
        details.default_branch = data.get("default_branch", "main")
        lic = data.get("license") or {}
        details.license = lic.get("spdx_id") or lic.get("name") or ""

    readme = _get(
        session, f"{base}/repos/{repo}/readme", timeout, headers=_headers(raw=True)
    )
    if readme is not None:
        details.readme = readme.text
    else:
        # API 限流/失败时走 raw 回退（独立限额，且无需认证）
        for name in ("README.md", "readme.md", "README.zh-CN.md"):
            raw = _get(session, f"https://raw.githubusercontent.com/{repo}/HEAD/{name}",
                       timeout, headers={"Accept": "text/plain"})
            if raw is not None:
                details.readme = raw.text
                break
        if not details.readme:
            details.warnings.append("readme fetch failed")

    tree = _get(session, f"{base}/repos/{repo}/git/trees/{details.default_branch}", timeout)
    if tree is not None:
        details.tree = [
            item.get("path", "")
            for item in tree.json().get("tree", [])
            if item.get("path")
        ][:50]
    else:
        details.warnings.append("tree fetch failed")

    return details


def save_details(details: RepoDetails, out_dir: str | Path) -> Path:
    """enrich 结果落盘缓存：artifacts/enriched/{owner}__{repo}.json（幂等跳过依据）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / (details.repo.replace("/", "__") + ".json")
    path.write_text(
        json.dumps(details.__dict__, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
