"""sau 风格 CLI：python -m ghtrend.cli <platform> login|check|publish --account <name>

  python -m ghtrend.cli juejin login   --account main    # 打开浏览器扫码（一次性）
  python -m ghtrend.cli juejin check   --account main    # 报告登录态
  python -m ghtrend.cli juejin publish --account main --article artifacts/.../x.md [--mode publish]
  python -m ghtrend.cli run [--account main] [--mode draft] [--limit 1] [--platforms juejin]
                                                   # 自动：fetch→归档→选题→enrich→生成(真实LLM)→发布

设计约束（cron 安全，F4/F7）：
  - 幂等：文章已发布到某平台则记录在 artifacts/publish_log.json，重跑跳过；
  - 每日预算沿用 limits.daily_articles（select 步约束）；
  - 平台间强制 ≥5 分钟间隔（配置更小也被抬到 300s）；
  - 单平台失败隔离，不影响其他平台；有平台失败则退出码 1。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path

from . import pipeline as pipeline_mod
from .adapt import make_variant
from .config import load_config
from .models import Article
from .publish import MIN_PLATFORM_INTERVAL, NotLoggedInError, PublishError, get_adapter

PLATFORMS = ("juejin", "toutiao")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ghtrend.cli",
        description="ghtrend 发布 CLI（sau 风格）：平台 × login/check/publish，或 run 自动全链路",
    )
    p.add_argument("platform", help="平台（juejin / toutiao）或 run（自动全链路）")
    p.add_argument("action", nargs="?", choices=["login", "check", "publish"],
                   help="平台动作；platform=run 时省略")
    p.add_argument("--account", default="default", help="命名账号（对应 profiles/<platform>/<account>/）")
    p.add_argument("--article", help="publish: 要发布的文章 md 路径；省略且 --latest 时取最新一篇")
    p.add_argument("--latest", action="store_true", help="publish: 发布 artifacts 下最新文章")
    p.add_argument("--mode", choices=["draft", "publish"], help="覆盖 config publish.mode（默认 draft）")
    p.add_argument("--limit", type=int, help="run: 本次生成文章数上限")
    p.add_argument("--platforms", help="run: 逗号分隔的发布平台列表，覆盖 config publish.platforms")
    p.add_argument("--config", default=None, help="配置文件路径")
    return p


def validate_args(args: argparse.Namespace) -> str | None:
    """返回错误信息；None 表示合法。"""
    if args.platform == "run":
        if args.action:
            return "run 不需要 action（用法: python -m ghtrend.cli run [--limit N]）"
        return None
    if args.platform not in PLATFORMS:
        return f"未知平台 {args.platform}；可用: {', '.join(PLATFORMS)}"
    if not args.action:
        return f"缺少动作：python -m ghtrend.cli {args.platform} login|check|publish"
    if args.action == "publish" and not args.article and not args.latest:
        return "publish 需要 --article <md路径> 或 --latest"
    return None


def _resolve_config(args) -> tuple[dict, Path]:
    if args.config is None:
        candidates = [Path.cwd() / "config.yaml",
                      Path(__file__).resolve().parent.parent / "config.yaml"]
        args.config = next((str(c) for c in candidates if c.exists()), "config.yaml")
    return load_config(args.config), Path(args.config).resolve().parent


def _pick_article(workspace: Path, args) -> Article:
    """--article 指定，或 --latest 取最新文章（供 publish）。"""
    from . import generate as gen_mod

    if args.article:
        path = Path(args.article)
        if not path.exists():
            raise SystemExit(f"文章不存在: {path}")
        meta = gen_mod.load_article_meta(path)
        return Article(slug=path.stem, repo=meta.get("repo", ""), angle=meta.get("angle", ""),
                       title=meta.get("title", ""), description=meta.get("description", ""),
                       tags=meta.get("tags", []), body=meta.get("body", ""), source_path=str(path))
    arts = sorted((workspace / "artifacts").glob("????-??-??/*.md"))
    if not arts:
        raise SystemExit("artifacts 下没有文章；先运行 python -m ghtrend.cli run 或 pipeline")
    return _pick_article(workspace, argparse.Namespace(article=str(arts[-1]), latest=False,
                                                       account=args.account))


def _publish_log(workspace: Path) -> dict:
    path = workspace / "artifacts" / "publish_log.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"published": []}


def _log_publish(workspace: Path, platform: str, article: Article, mode: str, detail: str) -> None:
    path = workspace / "artifacts" / "publish_log.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    log = _publish_log(workspace)
    log["published"].append({
        "platform": platform,
        "article": article.slug,
        "mode": mode,
        "at": _dt.datetime.now().isoformat(timespec="seconds"),
        "detail": detail,
    })
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def _not_implemented(platform: str) -> int:
    """stub 平台（如头条）统一干净降级：提示 + exit 2，不抛 traceback。"""
    print(f"[{platform}] 头条 adapter 尚未接入" if platform == "toutiao"
          else f"[{platform}] adapter 尚未接入", file=sys.stderr)
    return 2


def cmd_publish(cfg: dict, workspace: Path, args) -> int:
    adapter = get_adapter(args.platform, args.account, cfg)
    article = _pick_article(workspace, args)
    mode = args.mode or cfg.get("publish", {}).get("mode", "draft")
    try:
        outcome = adapter.publish(article, mode=mode)
    except NotImplementedError:
        return _not_implemented(args.platform)
    except NotLoggedInError as exc:
        print(f"[{args.platform}] 未登录：{exc}", file=sys.stderr)
        return 1
    except PublishError as exc:
        print(f"[{args.platform}] 发布失败：{exc}", file=sys.stderr)
        return 1
    print(f"[{args.platform}] {mode} ok: {outcome.detail}")
    _log_publish(workspace, args.platform, article, mode, outcome.detail)
    return 0


def cmd_check(cfg: dict, workspace: Path, args) -> int:
    adapter = get_adapter(args.platform, args.account, cfg)
    try:
        ok, detail = adapter.check()
    except NotImplementedError:
        return _not_implemented(args.platform)
    print(f"[{args.platform}] {'已登录' if ok else '未登录'}: {detail}")
    return 0 if ok else 1


def cmd_login(cfg: dict, workspace: Path, args) -> int:
    adapter = get_adapter(args.platform, args.account, cfg)
    try:
        print(adapter.login())
    except NotImplementedError:
        return _not_implemented(args.platform)
    return 0


def effective_interval(cfg: dict) -> int:
    """平台发布间隔；风控下限 300s，配置更小也会被抬到下限。"""
    return max(int(cfg.get("publish", {}).get("interval_seconds", MIN_PLATFORM_INTERVAL)),
               MIN_PLATFORM_INTERVAL)


def cmd_run(cfg: dict, workspace: Path, args) -> int:
    """自动全链路：fetch → archive → select → enrich → generate(真实) → adapt → publish。"""
    steps = []
    for name, fn in (
        ("fetch", lambda: pipeline_mod.step_fetch(cfg, workspace, mock=False)),
        ("archive", lambda: pipeline_mod.step_archive(cfg, workspace)),
        ("select", lambda: pipeline_mod.step_select(cfg, workspace, args.limit or cfg["limits"]["select_limit"])),
        ("enrich", lambda: pipeline_mod.step_enrich(cfg, workspace, mock=False)),
        ("generate", lambda: pipeline_mod.step_generate(cfg, workspace, mock=False)),
        ("adapt", lambda: pipeline_mod.step_adapt(cfg, workspace)),
    ):
        print(f"==> run:{name}")
        try:
            steps.append(fn())
            print(steps[-1] + "\n")
        except Exception as exc:
            print(f"run:{name} failed: {exc}", file=sys.stderr)
            return 1

    # 发布：逐平台，间隔 ≥5 分钟，失败隔离
    platforms = (args.platforms.split(",") if args.platforms
                 else cfg.get("publish", {}).get("platforms", ["juejin"]))
    interval = effective_interval(cfg)
    mode = args.mode or cfg.get("publish", {}).get("mode", "draft")
    log = _publish_log(workspace)
    done_slugs = {(e["platform"], e["article"]) for e in log["published"]}

    articles = pipeline_mod._load_state(cfg, workspace)["articles"]
    if not articles:
        print("run: 没有待发布文章（生成步骤可能命中每日预算或缓存）")
        return 0
    failures = 0
    for i, platform in enumerate(platforms):
        if i > 0:
            print(f"[spacing] 等待 {interval}s（平台间隔风控）…", flush=True)
            time.sleep(interval)
        try:
            adapter = get_adapter(platform.strip(), args.account, cfg)
        except SystemExit as exc:
            print(f"[{platform}] 跳过: {exc}", file=sys.stderr)
            failures += 1
            continue
        for article in articles:
            if (platform.strip(), article.slug) in done_slugs:
                print(f"[{platform}] {article.slug} 已发布过，跳过")
                continue
            try:
                outcome = adapter.publish(article, mode=mode)
            except NotLoggedInError as exc:
                print(f"[{platform}] 未登录: {exc}", file=sys.stderr)
                failures += 1
                break  # 该平台整体未登录，别再试下一篇
            except Exception as exc:
                print(f"[{platform}] {article.slug} 发布失败: {exc}", file=sys.stderr)
                failures += 1
                continue
            _log_publish(workspace, platform.strip(), article, mode, outcome.detail)
            print(f"[{platform}] {article.slug} {mode} ok: {outcome.detail}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    err = validate_args(args)
    if err:
        print(f"参数错误: {err}", file=sys.stderr)
        return 2
    cfg, workspace = _resolve_config(args)

    if args.platform == "run":
        return cmd_run(cfg, workspace, args)
    if args.action == "login":
        return cmd_login(cfg, workspace, args)
    if args.action == "check":
        return cmd_check(cfg, workspace, args)
    if args.action == "publish":
        return cmd_publish(cfg, workspace, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
