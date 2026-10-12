"""数据模型：trending 条目、选题、仓库详情、文章。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrendingRepo:
    """从 trending 页面解析出的单个仓库条目。"""

    repo: str  # owner/name
    url: str
    description: str = ""
    stars: int = 0
    period_stars: int = 0  # "N stars today/this week/this month"
    language: str = ""
    language_query: str = ""  # 抓取时用的语言 slug
    period: str = ""  # daily / weekly / monthly
    captured_at: str = ""  # ISO8601


@dataclass
class SelectedTopic:
    """选题：一个 repo × 一个未用过的角度。"""

    repo: str
    angle: str  # angle profile id
    angle_name: str = ""
    first_seen: str = ""
    times_trending: int = 0


@dataclass
class RepoDetails:
    """GitHub REST enrich 的产物。"""

    repo: str
    url: str = ""
    description: str = ""
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    created_at: str = ""
    pushed_at: str = ""
    language: str = ""
    homepage: str = ""
    license: str = ""
    topics: list[str] = field(default_factory=list)
    default_branch: str = ""
    readme: str = ""
    tree: list[str] = field(default_factory=list)  # 顶层文件/目录名
    warnings: list[str] = field(default_factory=list)

    def to_prompt_text(self, max_readme_chars: int = 12000) -> str:
        """打包为送给 LLM 的输入文本。"""
        lines = [
            f"# 仓库: {self.repo}",
            f"- URL: {self.url}",
            f"- 描述: {self.description}",
            f"- Stars: {self.stars} | Forks: {self.forks} | Open issues: {self.open_issues}",
            f"- 创建时间: {self.created_at} | 最近推送: {self.pushed_at}",
            f"- 主语言: {self.language} | 主页: {self.homepage or '无'}",
            f"- License: {self.license or '未声明'}",
            f"- Topics: {', '.join(self.topics) or '无'}",
            f"- 顶层结构: {', '.join(self.tree) or '未知'}",
            "",
            "## README（截断）",
            self.readme[:max_readme_chars],
        ]
        if self.warnings:
            lines += ["", "## 采集警告"] + [f"- {w}" for w in self.warnings]
        return "\n".join(lines)


@dataclass
class Article:
    """统一文章模型：generate 产物 + adapt 变体的载体。"""

    slug: str  # 如 2026-10-12_owner-repo_intro
    repo: str
    angle: str
    angle_name: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    cover: str = ""
    canonical: str = ""
    body: str = ""  # markdown 正文（含 frontmatter 之外的内容）
    source_path: str = ""  # 对应 artifacts 下的 md 文件
    meta: dict[str, Any] = field(default_factory=dict)

    def render_markdown(self) -> str:
        """frontmatter + 正文 + 尾部声明的完整 md。"""
        fm = [
            "---",
            f"title: {self.title}",
            f"description: {self.description}",
            f"repo: {self.repo}",
            f"angle: {self.angle}",
            "tags:",
            *[f"  - {t}" for t in self.tags],
            f"cover: {self.cover}",
            f"canonical: {self.canonical}",
            "---",
        ]
        return "\n".join(fm) + "\n\n" + self.body.strip() + "\n"


@dataclass
class PlatformVariant:
    """面向单个平台的发布变体。"""

    platform: str
    title: str
    summary: str
    tags: list[str]
    body: str
    article: Article


@dataclass
class PublishResult:
    """adapter 发布结果。"""

    platform: str
    ok: bool
    detail: str  # dryrun: 写入路径；真实 adapter: 平台返回/错误信息
