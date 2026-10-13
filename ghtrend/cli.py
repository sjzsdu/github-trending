"""sau 风格 CLI：ghtrend <platform> login|check|publish --account <name>

  ghtrend juejin login   --account main    # 打开浏览器扫码（一次性）
  ghtrend juejin check   --account main    # 报告登录态
  ghtrend juejin publish --account main --article artifacts/.../x.md [--mode publish]
  ghtrend run [--account main] [--mode draft] [--limit 1] [--platforms juejin]
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
from .publish import (
    ADAPTERS, MIN_PLATFORM_INTERVAL, NotLoggedInError, PublishError, PLATFORM_MANAGE_URLS,
    get_adapter,
)

PLATFORMS = tuple(ADAPTERS.keys())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghtrend",
        description="ghtrend 发布 CLI（sau 风格）：平台 × login/check/publish，或 run 自动全链路",
    )
    p.add_argument("platform", help="平台（juejin / toutiao / csdn / zhihu）或 run（自动全链路）或 status（查看发布记录）")
    p.add_argument("action", nargs="?", choices=["login", "check", "publish", "status"],
                   help="平台动作；platform=run/status 时省略")
    p.add_argument("--account", default="default", help="命名账号（对应 profiles/<platform>/<account>/）")
    p.add_argument("--article", help="publish: 要发布的文章 md 路径；省略且 --latest 时取最新一篇")
    p.add_argument("--latest", action="store_true", help="publish: 发布 artifacts 下最新文章")
    p.add_argument("--mode", choices=["draft", "publish"], help="覆盖 config publish.mode（默认 draft）")
    p.add_argument("--limit", type=int, help="run: 本次生成文章数上限；status: 每平台显示条数（默认 5）")
    p.add_argument("--platforms", help="run: 逗号分隔的发布平台列表，覆盖 config publish.platforms")
    p.add_argument("--config", default=None, help="配置文件路径")
    return p


def validate_args(args: argparse.Namespace) -> str | None:
    """返回错误信息；None 表示合法。"""
    if args.platform in ("run", "status"):
        if args.action:
            return f"{args.platform} 不需要 action（用法: ghtrend {args.platform}）"
        return None
    if args.platform not in PLATFORMS:
        return f"未知平台 {args.platform}；可用: {', '.join(PLATFORMS)}"
    if not args.action:
        return f"缺少动作：ghtrend {args.platform} login|check|publish|status"
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
        raise SystemExit("artifacts 下没有文章；先运行 ghtrend run 或 pipeline")
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

    # 发布：逐平台，间隔 ≥5 分钟，失败隔离；只发本次 run 新生成的文章
    platforms = (args.platforms.split(",") if args.platforms
                 else cfg.get("publish", {}).get("platforms", ["juejin"]))
    interval = effective_interval(cfg)
    mode = args.mode or cfg.get("publish", {}).get("mode", "draft")
    log = _publish_log(workspace)
    done_slugs = {(e["platform"], e["article"]) for e in log["published"]}

    # 只取本次 run 涉及的 slug：从 selection.json（新选题） + 今日 md 里找
    sel_path = workspace / "artifacts" / "selection.json"
    slugs_this_run: set[str] = set()
    if sel_path.exists():
        payload = json.loads(sel_path.read_text(encoding="utf-8"))
        for t in payload.get("topics", []):
            slug_prefix = f"{_dt.date.today().isoformat()}_{t['repo'].replace('/', '-')}_"
            slugs_this_run.update(
                f.stem for f in (workspace / "artifacts").glob(f"{_dt.date.today().isoformat()}/{slug_prefix}*.md")
            )
    # 兜底：selection.json 没新选题时，今天全部未发布过的 md 都视为候选
    if not slugs_this_run:
        arts_dir = workspace / "artifacts" / _dt.date.today().isoformat()
        if arts_dir.exists():
            slugs_this_run = {f.stem for f in arts_dir.glob("*.md")} - {
                slug for (_, slug) in done_slugs
            }

    if not slugs_this_run:
        print("run: 今日没有新生成的文章（可能 daily_articles 预算已满或全被发布过）")
        return 0

    articles = pipeline_mod._load_state(cfg, workspace)["articles"]
    to_publish = [a for a in articles if a.slug in slugs_this_run]
    if not to_publish:
        print("run: 本次选题的文章找不到 md，可能 generate 步跳过了")
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
        for article in to_publish:
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


def cmd_status(cfg: dict, workspace: Path, args) -> int:
    """查看各平台登录态 + 最近发布记录 + 快捷管理页链接。"""
    limit = args.limit or 5
    # 确定平台范围
    if getattr(args, "platform", "") == "status":
        platforms = tuple(cfg.get("publish", {}).get("platforms", list(PLATFORMS)))
    elif args.platform in PLATFORMS:
        platforms = (args.platform,)
    else:
        platforms = tuple(cfg.get("publish", {}).get("platforms", list(PLATFORMS)))

    log = _publish_log(workspace)
    # 同时从 artifacts 目录取文章标题（publish_log 里只有 slug）
    slug_title: dict[str, str] = {}
    for md in (workspace / "artifacts").glob("????-??-??/*.md"):
        slug_title[md.stem] = md.stem  # 先用 slug 兜底；从 frontmatter 取真实标题
        try:
            content = md.read_text(encoding="utf-8")[:200]
            for line in content.splitlines():
                if line.startswith("title:"):
                    slug_title[md.stem] = line[6:].strip().strip("\"'")
                    break
        except Exception:
            pass

    # 先跑登录态检测（并发？不，顺序来，每平台 headless 几秒）
    login_cache: dict[str, tuple[bool, str]] = {}
    for platform in platforms:
        try:
            adapter = get_adapter(platform.strip(), args.account, cfg)
            login_cache[platform.strip()] = adapter.check()
        except Exception as exc:
            login_cache[platform.strip()] = (False, f"check failed: {exc}")

    exit_code = 0
    for platform in platforms:
        p = platform.strip()
        ok, detail = login_cache.get(p, (False, "unknown"))
        status_icon = "✅" if ok else "❌"
        print(f"\n{'='*60}")
        print(f"  {p}  {status_icon} {'已登录' if ok else '未登录'}  ({detail})")
        if p in PLATFORM_MANAGE_URLS:
            print(f"  管理页 → {PLATFORM_MANAGE_URLS[p]}")
        print(f"{'='*60}")

        entries = [e for e in log["published"] if e["platform"] == p]
        entries.sort(key=lambda e: e["at"], reverse=True)
        if not entries:
            print("  （暂无发布记录）")
            continue
        print(f"  最近 {min(limit, len(entries))} 条（共 {len(entries)} 条）：")
        for e in entries[:limit]:
            title = slug_title.get(e["article"], e["article"])
            mode_label = "📝 草稿" if e["mode"] == "draft" else "🚀 发布"
            print(f"  {e['at']}  {mode_label}  {e['article']}")
            print(f"    标题：{title[:60]}")
    print()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    err = validate_args(args)
    if err:
        print(f"参数错误: {err}", file=sys.stderr)
        return 2
    cfg, workspace = _resolve_config(args)

    if args.platform == "run":
        return cmd_run(cfg, workspace, args)
    if args.platform == "status":
        return cmd_status(cfg, workspace, args)
    if args.action == "status":
        return cmd_status(cfg, workspace, args)
    if args.action == "login":
        return cmd_login(cfg, workspace, args)
    if args.action == "check":
        return cmd_check(cfg, workspace, args)
    if args.action == "publish":
        return cmd_publish(cfg, workspace, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
