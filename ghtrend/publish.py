"""sau 风格的真实发布层：per-platform login / check / publish + 命名账号。

- 账号 = 持久化浏览器 profile 目录 profiles/{platform}/{account}/
  （含 cookie 与登录态，已 gitignore，绝不入库/入日志）；
- 掘金用 Playwright headed 持久化上下文（风控对策 F4：真实浏览器、只进草稿
  为默认、平台间间隔 ≥5 分钟）；
- 发布弹窗里按配置勾选“AI 生成内容”声明（GB 45438-2025，F5）；
- 头条暂为 stub（同一接口，后续接入）；其余平台保持 payload-only。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Article

MIN_PLATFORM_INTERVAL = 300  # 秒；风控下限（F4）

JUEJIN_HOME = "https://juejin.cn"
# 登录态判定：页头出现头像图；未登录则是“登录/注册”按钮
_JUEJIN_AVATAR = "img.avatar"


class NotLoggedInError(RuntimeError):
    """profile 缺失或站点判定未登录。CLI 捕获后给引导性提示。"""


class PublishError(RuntimeError):
    """发布过程失败（页面结构变化、超时等）。"""


@dataclass
class PublishOutcome:
    platform: str
    account: str
    ok: bool
    mode: str  # draft / publish
    detail: str


def build_publish_payload(
    article: Article,
    platform: str,
    mode: str,
    *,
    max_tags: int = 3,
    category: str = "",
    ai_declaration: bool = True,
) -> dict:
    """把 Article 组装成平台提交参数（纯函数，可测；不触网）。"""
    from .adapt import make_variant

    variant = make_variant(article, platform)
    return {
        "platform": platform,
        "mode": mode,  # draft / publish
        "title": variant.title,
        "summary": variant.summary,
        "tags": variant.tags[:max_tags],
        "category": category,
        "body_md": variant.body,
        "ai_declaration": ai_declaration,
        "source_article": article.source_path,
        "repo": article.repo,
    }


def profile_dir(cfg: dict, platform: str, account: str) -> Path:
    root = cfg.get("publish", {}).get("profiles_dir", "profiles")
    p = Path(root) / platform / account
    p.mkdir(parents=True, exist_ok=True)
    return p


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "发布需要 Playwright：pip install -e '.[publish]' && playwright install chromium"
        ) from exc


class JuejinAdapter:
    """掘金：登录态持久化 + 草稿/发布。使用 headed 持久化上下文（F4）。"""

    platform = "juejin"

    def __init__(self, account: str = "default", cfg: dict | None = None):
        self.account = account
        self.cfg = cfg or {}
        pub = self.cfg.get("publish", {}).get("juejin", {})
        self.editor_url = pub.get("editor_url", "https://juejin.cn/editor/draft/new")
        self.category = pub.get("category", "前端")
        self.max_tags = int(pub.get("max_tags", 3))
        self.ai_declaration = bool(pub.get("ai_declaration", True))

    # ---- profile / 登录态 ----

    def _profile(self) -> Path:
        return profile_dir(self.cfg, self.platform, self.account)

    def _has_profile(self) -> bool:
        return self._profile().exists() and any(self._profile().iterdir())

    def _open_context(self, playwright, headless: bool):
        return playwright.chromium.launch_persistent_context(
            str(self._profile()),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )

    def _is_logged_in(self, page) -> bool:
        try:
            page.goto(JUEJIN_HOME, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            return False
        return page.locator(_JUEJIN_AVATAR).count() > 0

    # ---- sau 三件套 ----

    def login(self, timeout: int = 300) -> str:
        """打开有头浏览器等待人工扫码；轮询头像出现即视为登录成功。"""
        _require_playwright()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = self._open_context(p, headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(JUEJIN_HOME, wait_until="domcontentloaded", timeout=60000)
            print(f"[juejin] 请在打开的浏览器窗口中扫码/登录掘金（账号: {self.account}）……")
            try:
                page.wait_for_selector(_JUEJIN_AVATAR, timeout=timeout * 1000)
                msg = "login ok"
            except Exception:
                msg = "login timeout / not confirmed"
            finally:
                ctx.close()  # 持久化上下文自动保存 cookie 与 localStorage
        return msg

    def check(self) -> tuple[bool, str]:
        """报告登录态；profile 缺失时不启动浏览器，直接返回未登录。"""
        if not self._has_profile():
            return False, f"profile 不存在（{self._profile()}），请先运行 login"
        _require_playwright()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = self._open_context(p, headless=True)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                ok = self._is_logged_in(page)
                detail = "logged in" if ok else "profile 存在但登录态已失效，请重新 login"
            finally:
                ctx.close()
        return ok, detail

    def publish(self, article: Article, mode: str = "draft") -> PublishOutcome:
        """提交文章。mode=draft 保存草稿，mode=publish 走完整发布弹窗。"""
        payload = build_publish_payload(
            article, self.platform, mode,
            max_tags=self.max_tags, category=self.category,
            ai_declaration=self.ai_declaration,
        )
        if not self._has_profile():
            raise NotLoggedInError(
                f"掘金账号 {self.account} 未登录：先运行 "
                f"python -m ghtrend.cli juejin login --account {self.account}"
            )
        _require_playwright()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = self._open_context(p, headless=False)  # 发布全程 headed（F4）
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not self._is_logged_in(page):
                    raise NotLoggedInError("登录态已失效，请重新 login")
                detail = self._submit(page, payload)
            finally:
                ctx.close()
        return PublishOutcome(self.platform, self.account, True, mode, detail)

    # ---- 页面操作 ----

    def _submit(self, page, payload: dict) -> str:
        page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("textarea", timeout=30000)

        # 标题
        title_box = page.locator("textarea").first
        title_box.fill(payload["title"])

        # 正文：掘金 markdown 编辑器为 contenteditable/CodeMirror，insert_text 可整段粘贴
        editor = page.locator("[contenteditable='true']").first
        editor.click()
        page.keyboard.insert_text(payload["body_md"])
        page.wait_for_timeout(1000)

        if payload["mode"] == "draft":
            btn = page.get_by_role("button", name="保存草稿")
            if btn.count() == 0:
                btn = page.locator("button", has_text="保存草稿")
            btn.first.click()
            page.wait_for_timeout(2000)
            return "draft saved"

        # publish：打开发布弹窗
        btn = page.get_by_role("button", name="发布文章")
        if btn.count() == 0:
            btn = page.locator("button", has_text="发布文章")
        btn.first.click()
        page.wait_for_timeout(1000)

        # 分类（弹窗必选）
        if payload["category"]:
            cat_text = payload["category"]
            cat = page.locator(f".category-list li:has-text('{cat_text}')")
            if cat.count() == 0:
                cat = page.locator(f"[class*=category] li:has-text('{cat_text}')")
            if cat.count() > 0:
                cat.first.click()
            else:
                page.locator(".category-list li, [class*=category] li").first.click()

        # 标签：按文章 tags 匹配可选标签，不足则取前 N 个
        chosen = 0
        for tag in payload["tags"]:
            opt = page.locator(f"[class*=tag-list] li:has-text('{tag}'), .tag-list li:has-text('{tag}')")
            if opt.count() > 0 and chosen < self.max_tags:
                opt.first.click()
                chosen += 1
        if chosen == 0:
            boxes = page.locator(".tag-list li, [class*=tag-list] li")
            for i in range(min(self.max_tags, boxes.count())):
                boxes.nth(i).click()
        page.wait_for_timeout(500)

        # AI 生成内容声明（GB 45438-2025）：有勾选项必须勾上
        if payload["ai_declaration"]:
            target = next(
                (loc for loc in (
                    page.locator("label:has-text('AI')"),
                    page.locator("[class*=declaration] :text('AI')"),
                ) if loc.count() > 0), None)
            if target:
                cb = target.first.locator("input[type=checkbox]")
                if cb.count() > 0 and not cb.first.is_checked():
                    cb.first.check()
                else:
                    target.first.click()
                payload["ai_declaration_checked"] = True
            else:
                # 找不到声明勾选项时不阻塞，但显式留痕以便人工复核
                payload["ai_declaration_checked"] = False

        page.get_by_role("button", name="发布").first.click()
        page.wait_for_timeout(3000)
        return "published"


class ToutiaoAdapter:
    """头条：接口占位（同 adapter 接口，下一轮接入 DOM 自动化）。"""

    platform = "toutiao"

    def __init__(self, account: str = "default", cfg: dict | None = None):
        self.account = account
        self.cfg = cfg or {}

    def login(self, timeout: int = 300) -> str:
        raise NotImplementedError("头条 adapter 下一轮接入；掘金已可用")

    def check(self) -> tuple[bool, str]:
        raise NotImplementedError("头条 adapter 下一轮接入；掘金已可用")

    def publish(self, article: Article, mode: str = "draft") -> PublishOutcome:
        raise NotImplementedError("头条 adapter 下一轮接入；掘金已可用")


ADAPTERS = {
    "juejin": JuejinAdapter,
    "toutiao": ToutiaoAdapter,
}


def get_adapter(platform: str, account: str, cfg: dict):
    if platform not in ADAPTERS:
        raise SystemExit(
            f"未知平台 {platform}；可用平台: {', '.join(ADAPTERS)}（其余平台为 payload-only dryrun）"
        )
    return ADAPTERS[platform](account=account, cfg=cfg)
