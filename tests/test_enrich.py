"""enrich 失败降级测试：README 拉取抛异常时流水线不中断，只记 warning。"""

import json
from unittest.mock import patch

import pytest
import requests

from ghtrend import enrich as enrich_mod
from ghtrend.enrich import enrich_repo
from ghtrend.models import RepoDetails

META = {
    "html_url": "https://github.com/a/b",
    "description": "demo repo",
    "stargazers_count": 10,
    "forks_count": 2,
    "open_issues_count": 1,
    "created_at": "2024-01-01T00:00:00Z",
    "pushed_at": "2026-10-12T00:00:00Z",
    "language": "Python",
    "homepage": "",
    "topics": ["demo"],
    "default_branch": "main",
    "license": {"spdx_id": "MIT"},
}

TREE = {"tree": [{"path": "README.md"}, {"path": "src"}]}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FlakeyReadmeSession:
    """元数据/树正常返回，README 拉取抛连接异常（API 与 raw 回退都失败）。"""

    def get(self, url, headers=None, timeout=None):
        if "/readme" in url or "raw.githubusercontent" in url:
            raise requests.ConnectionError("connection reset by peer")
        if url.endswith("/git/trees/main"):
            return FakeResponse(TREE)
        return FakeResponse(META)


def test_readme_failure_degrades_to_warning():
    with patch.object(enrich_mod.requests, "Session", return_value=FlakeyReadmeSession()):
        details = enrich_repo("a/b")
    assert details.stars == 10  # 元数据正常
    assert details.readme == ""
    assert "readme fetch failed" in details.warnings
    assert details.tree == ["README.md", "src"]  # 后续步骤继续执行


def test_pipeline_completes_with_failing_readme(tmp_path):
    """端到端：select 出的 topic 走真实 enrich（README 抛异常）→ generate 仍产出文章。"""
    from ghtrend.models import SelectedTopic
    from ghtrend.pipeline import step_enrich, step_generate

    cfg = {
        "paths": {"artifacts": "artifacts", "archive_db": "archive.db"},
        "limits": {"max_readme_chars": 1000},
        "angles": [{"id": "intro", "name": "入门介绍", "prompt": "介绍。"}],
        "llm": {"base_url": "https://example.invalid/v1", "model": "m", "api_key": ""},
        "enrich": {"api_base": "https://api.github.com", "timeout_seconds": 1},
    }
    workspace = tmp_path
    sel_dir = workspace / "artifacts"
    sel_dir.mkdir()
    (sel_dir / "selection.json").write_text(
        json.dumps({"topics": [SelectedTopic(repo="a/b", angle="intro", angle_name="入门介绍").__dict__]}),
        encoding="utf-8",
    )

    with patch.object(enrich_mod.requests, "Session", return_value=FlakeyReadmeSession()):
        enrich_out = step_enrich(cfg, workspace, mock=False)
        assert "readme fetch failed" in enrich_out  # warning 上浮到步骤输出
        gen_out = step_generate(cfg, workspace, mock=True)  # 无异常即完成

    assert "a/b×intro" in gen_out
    articles = list((workspace / "artifacts").glob("????-??-??/*.md"))
    assert len(articles) == 1  # README 失败没有阻止成文


def test_save_details_roundtrip(tmp_path):
    details = RepoDetails(repo="x/y", readme="hi")
    path = enrich_mod.save_details(details, tmp_path)
    loaded = RepoDetails(**json.loads(path.read_text(encoding="utf-8")))
    assert loaded.repo == "x/y"
    assert loaded.readme == "hi"
