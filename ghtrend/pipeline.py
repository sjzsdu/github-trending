"""流水线 CLI：fetch → archive → select → enrich → generate → adapt。

用法：
  python -m ghtrend.pipeline --mock-llm --limit 1   # 全离线端到端（fixture 抓取 + canned LLM）
  python -m ghtrend.pipeline --step fetch           # 只跑单步（每步幂等、可独立重跑）
  python -m ghtrend.pipeline --limit 2              # 真实抓取 + 真实 LLM（需配置端点）

状态传递（均为磁盘文件，因此每步可独立重跑）：
  data/snapshots/*.json -> archive.db -> artifacts/selection.json
  -> artifacts/enriched/*.json -> artifacts/{date}/*.md -> artifacts/payloads/*/*.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from . import adapt as adapt_mod
from . import archive as archive_mod
from . import enrich as enrich_mod
from . import generate as gen_mod
from . import scrape as scrape_mod
from .config import load_config
from .models import Article, RepoDetails, SelectedTopic


def _load_state(cfg: dict, workspace: Path) -> dict:
    """从磁盘恢复跨步状态（幂等重跑的关键）。"""
    state: dict = {"topics": [], "details": {}, "articles": []}
    sel_path = workspace / cfg["paths"]["artifacts"] / "selection.json"
    if sel_path.exists():
        payload = json.loads(sel_path.read_text(encoding="utf-8"))
        state["topics"] = [SelectedTopic(**t) for t in payload["topics"]]
    enriched_dir = workspace / cfg["paths"]["artifacts"] / "enriched"
    if enriched_dir.exists():
        for f in enriched_dir.glob("*.json"):
            d = RepoDetails(**json.loads(f.read_text(encoding="utf-8")))
            state["details"][d.repo] = d
    arts_dir = workspace / cfg["paths"]["artifacts"]
    if arts_dir.exists():
        for f in sorted(arts_dir.glob("????-??-??/*.md")):
            meta = gen_mod.load_article_meta(f)
            article = Article(
                slug=f.stem,
                repo=meta.get("repo", ""),
                angle=meta.get("angle", ""),
                title=meta.get("title", ""),
                description=meta.get("description", ""),
                tags=meta.get("tags", []),
                cover=meta.get("cover", ""),
                canonical=meta.get("canonical", ""),
                body=meta.get("body", ""),
                source_path=str(f),
            )
            state["articles"].append(article)
    return state


def step_fetch(cfg: dict, workspace: Path, *, mock: bool) -> str:
    """抓取各语言 × 周期，写 JSON 快照。"""
    scrape_cfg = cfg["scrape"]
    written = []
    for lang in cfg["languages"]:
        for period in cfg["periods"]:
            captured_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
            html = scrape_mod.fetch_trending(
                lang,
                period,
                base_url=scrape_cfg["base_url"],
                user_agent=scrape_cfg["user_agent"],
                timeout=scrape_cfg["timeout_seconds"],
                offline=mock,
            )
            repos = scrape_mod.parse_trending_html(html, lang, period, captured_at)
            if not repos:
                print(f"  [warn] {lang}/{period}: 0 repos parsed (page empty or selector drift)")
            path = scrape_mod.write_snapshot(repos, workspace / cfg["paths"]["snapshots"], lang, period, captured_at)
            written.append(f"{lang}/{period}: {len(repos)} repos -> {path.relative_to(workspace)}")
            scrape_mod.polite_sleep(cfg["limits"]["fetch_interval_seconds"] if not mock else 0)
    return "\n".join(written) or "nothing fetched"


def step_archive(cfg: dict, workspace: Path) -> str:
    """把全部快照 upsert 进 SQLite 归档（按 captured_at 幂等）。"""
    files, repos, new = archive_mod.archive_snapshots(
        workspace / cfg["paths"]["snapshots"], workspace / cfg["paths"]["archive_db"]
    )
    if files == 0:
        return "no snapshot found; run fetch first"
    return f"{files} snapshot(s): {repos} repos archived, {new} new"


def step_select(cfg: dict, workspace: Path, limit: int) -> str:
    """归档里选题（去重 + 角度轮换），写 artifacts/selection.json。

    limit 受 limits.daily_articles 约束：当日已生成文章数会占用每日预算，
    预算耗尽则本步不产生新选题（提高 limit 也无法突破）。
    """
    daily_budget = int(cfg["limits"].get("daily_articles", limit))
    made_today = len(list((workspace / cfg["paths"]["artifacts"]).glob(f"{_dt.date.today().isoformat()}/*.md")))
    limit = max(0, min(limit, daily_budget - made_today))
    if limit == 0:
        return (f"daily article budget exhausted ({made_today}/{daily_budget} generated today); "
                "no new topics selected")
    conn = archive_mod.connect(workspace / cfg["paths"]["archive_db"])
    try:
        topics = archive_mod.select_topics(conn, cfg["angles"], limit=limit)
    finally:
        conn.close()
    sel_path = workspace / cfg["paths"]["artifacts"] / "selection.json"
    sel_path.parent.mkdir(parents=True, exist_ok=True)
    sel_path.write_text(
        json.dumps({"selected_at": _dt.datetime.now().isoformat(timespec="seconds"),
                    "topics": [t.__dict__ for t in topics]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return f"selected {len(topics)} topic(s) -> {sel_path.relative_to(workspace)}: " + ", ".join(
        f"{t.repo}×{t.angle}" for t in topics
    )


def step_enrich(cfg: dict, workspace: Path, *, mock: bool) -> str:
    """为选中 repo 拉取 GitHub REST 元数据 + README（结果缓存，幂等）。"""
    state = _load_state(cfg, workspace)
    out_dir = workspace / cfg["paths"]["artifacts"] / "enriched"
    lines = []
    for topic in state["topics"]:
        cached = out_dir / (topic.repo.replace("/", "__") + ".json")
        if cached.exists():
            lines.append(f"{topic.repo}: cached ({cached.relative_to(workspace)})")
            continue
        if mock:
            details = enrich_mod.mock_details(topic.repo)
        else:
            details = enrich_mod.enrich_repo(
                topic.repo,
                api_base=cfg["enrich"]["api_base"],
                timeout=cfg["enrich"]["timeout_seconds"],
            )
        path = enrich_mod.save_details(details, out_dir)
        lines.append(f"{topic.repo}: {len(details.readme)} chars readme -> {path.relative_to(workspace)}"
                     + (f" (warnings: {', '.join(details.warnings)})" if details.warnings else ""))
    return "\n".join(lines) or "no topics; run select first"


def step_generate(cfg: dict, workspace: Path, *, mock: bool) -> str:
    """逐 topic 生成文章 md（已存在则跳过，幂等）。"""
    state = _load_state(cfg, workspace)
    lines = []
    for topic in state["topics"]:
        details = state["details"].get(topic.repo)
        if details is None:
            lines.append(f"{topic.repo}: skip (no enriched data; run enrich first)")
            continue
        existing = sorted((workspace / cfg["paths"]["artifacts"]).glob(f"????-??-??/*_{topic.angle}.md"))
        if any(topic.repo.replace("/", "-") in p.name for p in existing):
            lines.append(f"{topic.repo}×{topic.angle}: cached ({existing[0].relative_to(workspace)})")
            continue
        article = gen_mod.make_article(topic, details, cfg, mock=mock)
        path = gen_mod.save_article(article, workspace / cfg["paths"]["artifacts"])
        article.source_path = str(path)
        lines.append(f"{topic.repo}×{topic.angle}: -> {path.relative_to(workspace)}")
        # 成文后登记已覆盖角度（回写归档，选题去重闭环）
        conn = archive_mod.connect(workspace / cfg["paths"]["archive_db"])
        try:
            archive_mod.mark_covered(conn, topic.repo, topic.angle)
        finally:
            conn.close()
    return "\n".join(lines) or "no topics; run select first"


def step_adapt(cfg: dict, workspace: Path) -> str:
    """生成每平台变体 payload（dryrun adapter 落盘）。"""
    state = _load_state(cfg, workspace)
    lines = []
    for article in state["articles"]:
        results = adapt_mod.adapt_article(article, workspace / cfg["paths"]["payloads"])
        for r in results:
            lines.append(f"{article.slug} [{r.platform}] -> {r.detail}")
    return "\n".join(lines) or "no articles; run generate first"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ghtrend.pipeline",
        description="GitHub Trending 采集 → 归档选题 → enrich → 生成 → 平台变体（dry-run）",
    )
    parser.add_argument("--config", default=None, help="配置文件路径（缺省依次找 ./config.yaml、包内仓库根的 config.yaml）")
    parser.add_argument("--limit", type=int, default=None, help="本次最多生成的文章数（覆盖 limits.select_limit）")
    parser.add_argument("--step", choices=["fetch", "archive", "select", "enrich", "generate", "adapt"],
                        default=None, help="只跑单步（幂等，可独立重跑）；缺省跑全流程")
    parser.add_argument("--mock-llm", action="store_true",
                        help="全离线模式：抓取用内置 fixture、生成用 canned LLM 响应")
    args = parser.parse_args(argv)

    if args.config is None:
        candidates = [Path.cwd() / "config.yaml", Path(__file__).resolve().parent.parent / "config.yaml"]
        args.config = next((str(p) for p in candidates if p.exists()), "config.yaml")
    cfg = load_config(args.config)
    # workspace 以 config.yaml 所在目录为根，与调用时的 cwd 解耦
    workspace = Path(args.config).resolve().parent
    limit = args.limit if args.limit is not None else cfg["limits"]["select_limit"]

    def run(name: str, fn, *a, **kw) -> None:
        print(f"==> {name}")
        out = fn(*a, **kw)
        print((out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)) + "\n")

    try:
        if args.step in (None, "fetch"):
            run("fetch", step_fetch, cfg, workspace, mock=args.mock_llm)
        if args.step in (None, "archive"):
            run("archive", step_archive, cfg, workspace)
        if args.step in (None, "select"):
            run("select", step_select, cfg, workspace, limit)
        if args.step in (None, "enrich"):
            run("enrich", step_enrich, cfg, workspace, mock=args.mock_llm)
        if args.step in (None, "generate"):
            run("generate", step_generate, cfg, workspace, mock=args.mock_llm)
        if args.step in (None, "adapt"):
            run("adapt", step_adapt, cfg, workspace)
    except Exception as exc:  # 单步失败信息明确上抛，便于单步重跑
        print(f"pipeline failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
