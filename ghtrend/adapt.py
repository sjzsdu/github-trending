"""发布脚手架（v1 只做 dry-run）。

- 统一 Article 模型 + 每平台变体（标题/摘要/标签差异化，规避一稿多发判重，F6）；
- PublishAdapter 接口 + DryRunAdapter（payload 落盘）；
- 真实平台 adapter（首个：掘金草稿）见 README 的接入计划，本轮不做浏览器自动化。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from .models import Article, PlatformVariant, PublishResult

# 平台规格：标题字数上限 / 标签上限 / 摘要字数上限（用于变体差异化）
PLATFORM_SPECS: dict[str, dict] = {
    "juejin": {"title_max": 50, "tags_max": 5, "summary_max": 100, "suffix": "｜开源项目实战"},
    "toutiao": {"title_max": 30, "tags_max": 3, "summary_max": 80, "suffix": ""},
    "zhihu": {"title_max": 60, "tags_max": 5, "summary_max": 120, "suffix": ""},
    "csdn": {"title_max": 40, "tags_max": 5, "summary_max": 100, "suffix": ""},
}


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def make_variant(article: Article, platform: str) -> PlatformVariant:
    """按平台规格生成变体：标题加平台后缀并截断、标签取前 N、摘要取 description。"""
    spec = PLATFORM_SPECS.get(platform, {"title_max": 60, "tags_max": 5, "summary_max": 120, "suffix": ""})
    title = article.title
    if spec["suffix"] and spec["suffix"] not in title:
        # 先截断基础标题再拼平台后缀，保证后缀不被截掉
        title = truncate(title, max(1, spec["title_max"] - len(spec["suffix"]))) + spec["suffix"]
    else:
        title = truncate(title, spec["title_max"])
    return PlatformVariant(
        platform=platform,
        title=truncate(title, spec["title_max"]),
        summary=truncate(article.description, spec["summary_max"]),
        tags=article.tags[: spec["tags_max"]],
        body=article.body,
        article=article,
    )


class PublishAdapter(ABC):
    """平台发布 adapter 接口。真实实现（掘金草稿等）后续按此接入。"""

    platform: str = ""

    @abstractmethod
    def publish(self, variant: PlatformVariant) -> PublishResult: ...


class DryRunAdapter(PublishAdapter):
    """把平台就绪 payload 写到磁盘，不触网、不动账号。"""

    def __init__(self, out_dir: str | Path):
        self.out_dir = Path(out_dir)

    def publish(self, variant: PlatformVariant) -> PublishResult:
        out = self.out_dir / variant.platform
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{variant.article.slug}.json"
        payload = {
            "platform": variant.platform,
            "title": variant.title,
            "summary": variant.summary,
            "tags": variant.tags,
            "body_md": variant.body,
            "source_article": str(variant.article.source_path),
            "ai_disclosure": True,
            "draft_only": True,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return PublishResult(platform=variant.platform, ok=True, detail=str(path))


def adapt_article(article: Article, out_dir: str | Path, platforms: list[str] | None = None) -> list[PublishResult]:
    """对一篇文章跑全部平台 dry-run adapter。"""
    platforms = platforms or list(PLATFORM_SPECS)
    adapter = DryRunAdapter(out_dir)
    results = []
    for platform in platforms:
        results.append(adapter.publish(make_variant(article, platform)))
    return results
