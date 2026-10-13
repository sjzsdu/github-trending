"""sau 风格的真实发布层：per-platform login / check / publish + 命名账号。

- 账号 = 持久化浏览器 profile 目录 profiles/{platform}/{account}/
  （含 cookie 与登录态，已 gitignore，绝不入库/入日志）；
- 所有 adapter 共享 BaseBrowserAdapter 基类：profile / 持久化 context /
  login（等待头像/用户元素出现）/ check（headless 检测）/ publish 三件套；
- 每平台只填：home URL、editor URL、登录态检测选择器（头像/用户名）、
  编辑器 title/body 选择器、发布弹窗分类/标签选择器。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .models import Article

MIN_PLATFORM_INTERVAL = 300  # 秒；风控下限


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
            "发布需要 Playwright：uv sync --extra publish && uv run playwright install chromium"
        ) from exc


# ---------------------------------------------------------------------------
# 基类：封装持久化 profile + login/check 通用逻辑
# 子类只需填 _SPEC 常量（平台专属 URL 与选择器） + 覆写 _submit / _fill_editor
# ---------------------------------------------------------------------------


class BaseBrowserAdapter(ABC):
    """所有平台 adapter 的骨架：持久化浏览器 profile + sau 三件套。"""

    platform: str = ""

    # 子类在类属性里声明默认值；config 覆盖；用于 _submit 页面操作
    home_url: str = ""
    editor_url: str = ""
    login_detector_selector: str = ""   # 登录态出现的元素（头像/用户名）
    login_button_selector: str = ""     # 未登录时出现的登录按钮（反向校验）
    title_selector: str = "input[placeholder*=标题], textarea[placeholder*=标题], input.title, input.title-input"
    body_editor_selector: str = "textarea, [contenteditable='true'], .CodeMirror, .ql-container, .ne-viewer-body"
    publish_button_selector: str = ""   # 打开发布弹窗的按钮
    confirm_publish_selector: str = ""  # 弹窗里"确定并发布"类按钮
    category_selector: str = ""         # 分类列表项
    category_fallback_text: str = ""    # 找不到配置分类时的兜底（第一个可选分类）
    tag_input_selector: str = ""        # 标签搜索框（输入关键词）
    tag_search_result_selector: str = ""  # 搜索结果项

    def __init__(self, account: str = "default", cfg: dict | None = None):
        self.account = account
        self.cfg = cfg or {}
        pub = self.cfg.get("publish", {}).get(self.platform, {})
        self.editor_url = pub.get("editor_url", self.editor_url)
        self.headless = bool(pub.get("headless", False))
        self.max_tags = int(pub.get("max_tags", 3))
        self.category = pub.get("category", self.category_fallback_text or "")
        self.ai_declaration = bool(pub.get("ai_declaration", True))

    # ---- profile / context ----

    def _profile(self) -> Path:
        return profile_dir(self.cfg, self.platform, self.account)

    def _has_profile(self) -> bool:
        p = self._profile()
        return p.exists() and any(p.iterdir())

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

    # ---- 登录态探测 ----

    def _is_logged_in(self, page) -> bool:
        try:
            page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            return False
        page.wait_for_timeout(2500)
        if self.login_button_selector and page.locator(self.login_button_selector).count() > 0:
            return False
        return bool(self.login_detector_selector) and page.locator(self.login_detector_selector).count() > 0

    # ---- sau 三件套 ----

    def login(self, timeout: int = 300) -> str:
        """有头浏览器等人工扫码；检测到登录元素即成功。"""
        _require_playwright()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = self._open_context(p, headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(self.home_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            print(f"[{self.platform}] 请在打开的浏览器窗口中登录/扫码（账号: {self.account}）……")
            try:
                page.wait_for_selector(self.login_detector_selector, timeout=timeout * 1000)
                msg = "login ok"
            except Exception:
                msg = "login timeout / not confirmed"
            finally:
                ctx.close()
        return msg

    def check(self) -> tuple[bool, str]:
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
        payload = build_publish_payload(
            article, self.platform, mode,
            max_tags=self.max_tags, category=self.category,
            ai_declaration=self.ai_declaration,
        )
        if not self._has_profile():
            raise NotLoggedInError(
                f"{self.platform} 账号 {self.account} 未登录：先运行 "
                f"ghtrend {self.platform} login --account {self.account}"
            )
        _require_playwright()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = self._open_context(p, headless=self.headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not self._is_logged_in(page):
                    raise NotLoggedInError("登录态已失效，请重新 login")
                detail = self._submit(page, payload)
            finally:
                ctx.close()
        return PublishOutcome(self.platform, self.account, True, mode, detail)

    # ---- 子类实现：填充编辑器 + 发布弹窗 ----

    @abstractmethod
    def _submit(self, page, payload: dict) -> str:
        """打开编辑器、填标题/正文、处理 publish 弹窗。返回描述字符串。"""


# ---------------------------------------------------------------------------
# 掘金
# ---------------------------------------------------------------------------


class JuejinAdapter(BaseBrowserAdapter):
    """掘金：Markdown 编辑器（CodeMirror）+ 分类/标签弹窗。"""

    platform = "juejin"
    home_url = "https://juejin.cn"
    editor_url = "https://juejin.cn/editor/drafts/new"
    login_detector_selector = ".jj-avatar"
    login_button_selector = "a:has-text('登录'), a:has-text('登录/注册')"

    def _submit(self, page, payload: dict) -> str:
        page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".title-input", timeout=30000)
        page.wait_for_timeout(1000)

        # 标题：掘金用 input.title-input
        page.locator(".title-input").fill(payload["title"])

        # 正文：CodeMirror → click + insert_text
        page.locator(".CodeMirror").first.click()
        page.keyboard.insert_text(payload["body_md"])
        page.wait_for_timeout(2000)

        if payload["mode"] == "draft":
            return "draft saved"  # 新版编辑器 auto-save，无显式按钮

        # publish：打开发布弹窗
        page.locator("button", has_text="发布").first.click()
        page.wait_for_timeout(2000)

        # 分类
        if payload["category"]:
            cat = page.locator(f".category-list .item:has-text('{payload['category']}')")
            if cat.count() == 0:
                cat = page.locator(f"[class*=category-list] .item:has-text('{payload['category']}')")
            if cat.count() > 0:
                cat.first.click()
            else:
                page.locator(".category-list .item").first.click()
            page.wait_for_timeout(500)

        # 标签：搜索式选择器
        chosen = 0
        for tag in payload["tags"]:
            if chosen >= self.max_tags:
                break
            try:
                tag_input = page.locator(".tag-input .byte-select__input").first
                tag_input.click()
                tag_input.fill(tag)
                page.wait_for_timeout(1000)
                opt = page.locator(".byte-select-dropdown__item, [class*=select-dropdown] .item")
                if opt.count() > 0:
                    opt.first.click()
                    chosen += 1
                    page.wait_for_timeout(500)
            except Exception:
                continue

        # 摘要
        summary_ta = page.locator(".summary-textarea textarea").first
        if summary_ta.count() > 0 and payload.get("summary"):
            summary_ta.fill(payload["summary"][:100])

        page.locator("button", has_text="确定并发布").first.click()
        page.wait_for_timeout(3000)
        return "published"


# ---------------------------------------------------------------------------
# 头条（字节系：https://mp.toutiao.com）
# ---------------------------------------------------------------------------


class ToutiaoAdapter(BaseBrowserAdapter):
    """头条：mp.toutiao.com 创作中心。Markdown 编辑器，自动保存草稿。"""

    platform = "toutiao"
    home_url = "https://mp.toutiao.com"
    editor_url = "https://mp.toutiao.com/profile_v4/graphic/publish"
    login_detector_selector = ".user-info, [class*=user-info], .avatar, .user"
    login_button_selector = "a:has-text('登录'), button:has-text('登录')"

    def _submit(self, page, payload: dict) -> str:
        page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # 标题：头条编辑器是富文本/Markdown 混合；先探测 title input
        # TODO: 首次用户登录后跑一次 publish 再精调 title selector
        title_el = page.locator("input[placeholder*=标题], textarea[placeholder*=标题], "
                                "[class*=title] input, .title input").first
        if title_el.count() > 0:
            title_el.fill(payload["title"])
        else:
            # 兜底：若标题在富文本区首行，则 prepend 正文
            payload["body_md"] = f"# {payload['title']}\n\n{payload['body_md']}"

        # 正文：优先 Markdown textarea / CodeMirror，再兜底 contenteditable
        body = None
        for sel in [".CodeMirror", ".bytemd textarea", ".ne-viewer-body textarea",
                    "textarea[placeholder*=正文]", "[contenteditable='true']",
                    ".ql-container", ".w-e-text-container"]:
            if page.locator(sel).count() > 0:
                body = page.locator(sel).first
                break
        if body is None:
            raise PublishError("头条正文编辑器选择器未命中，请登录后跑 publish 精调")

        body.click()
        page.keyboard.insert_text(payload["body_md"])
        page.wait_for_timeout(2000)

        if payload["mode"] == "draft":
            # 头条创作中心可能自动保存；也可能有"保存草稿"按钮
            save_btn = page.locator("button:has-text('保存草稿'), a:has-text('保存草稿')")
            if save_btn.count() > 0:
                save_btn.first.click()
                page.wait_for_timeout(2000)
            return "draft saved"

        # publish：头条发布弹窗
        pub = page.locator("button:has-text('发布'), button:has-text('发布文章')")
        if pub.count() > 0:
            pub.first.click()
            page.wait_for_timeout(2000)

        # 弹窗里分类/标签... TODO：登录后探测
        confirm = page.locator("button:has-text('确认发布'), button:has-text('确定发布'), button:has-text('发布')")
        if confirm.count() > 0:
            confirm.first.click()
            page.wait_for_timeout(3000)
        return "published"


# ---------------------------------------------------------------------------
# CSDN
# ---------------------------------------------------------------------------


class CSDNAdapter(BaseBrowserAdapter):
    """CSDN 博客创作中心：mp.csdn.net/mp_blog/creation/editor。"""

    platform = "csdn"
    home_url = "https://mp.csdn.net"
    editor_url = "https://mp.csdn.net/mp_blog/creation/editor"
    login_detector_selector = ".user-info, .avatar, [class*=user]"
    login_button_selector = "a:has-text('登录'), button:has-text('登录')"

    def _submit(self, page, payload: dict) -> str:
        page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # CSDN 标题：textarea#txtTitle（placeholder 含"标题"）
        title_el = page.locator("#txtTitle, textarea#txtTitle").first
        if title_el.count() > 0:
            title_el.fill(payload["title"])
        else:
            payload["body_md"] = f"# {payload['title']}\n\n{payload['body_md']}"

        # 正文：CKEditor 4 iframe（cke_wysiwyg_frame）→ frame_locator 进 body
        iframe_sel = "iframe.cke_wysiwyg_frame"
        if page.locator(iframe_sel).count() > 0:
            frame = page.frame_locator(iframe_sel)
            body = frame.locator("body[contenteditable='true'], [contenteditable='true']").first
            body.click()
            page.keyboard.insert_text(payload["body_md"])
            page.wait_for_timeout(2000)
        else:
            # 兜底：非 CKEditor 场景（CodeMirror/textarea/通用 contenteditable），
            # 但排除摘要 textarea（#txtSammary，placeholder 含"正文"二字会误命中且不可见）
            body = None
            for sel in [".CodeMirror", ".monaco-editor textarea", ".ne-viewer-body",
                        ".bytemd textarea", "textarea:not(#txtSammary):not(#txtTitle)",
                        "[contenteditable='true']:not(.ProseMirror-widget)",
                        ".ql-container", ".w-e-text-container"]:
                if page.locator(sel).count() > 0:
                    body = page.locator(sel).first
                    break
            if body is None:
                raise PublishError("CSDN 正文编辑器选择器未命中，请登录后跑 publish 精调")
            body.click()
            page.keyboard.insert_text(payload["body_md"])
            page.wait_for_timeout(2000)

        # 摘要：CSDN 有专门的摘要 textarea（id=el_mcm-id-... 可见）
        summary_ta = page.locator("textarea[id^=el_mcm-id-][placeholder*=摘要]").first
        if summary_ta.count() > 0 and payload.get("summary"):
            try:
                summary_ta.fill(payload["summary"][:100])
            except Exception:
                pass

        if payload["mode"] == "draft":
            save_btn = page.locator("button:has-text('保存草稿')")
            if save_btn.count() > 0:
                save_btn.first.click()
                page.wait_for_timeout(2000)
            return "draft saved"

        pub = page.locator("button:has-text('发布博客'), button:has-text('发布文章')")
        if pub.count() > 0:
            pub.first.click()
            page.wait_for_timeout(2000)

        confirm = page.locator("button:has-text('确认发布'), button:has-text('确定发布')")
        if confirm.count() > 0:
            confirm.first.click()
            page.wait_for_timeout(3000)
        return "published"


# ---------------------------------------------------------------------------
# 知乎（zhuanlan.zhihu.com）
# ---------------------------------------------------------------------------


class ZhihuAdapter(BaseBrowserAdapter):
    """知乎专栏：zhuanlan.zhihu.com/write。富文本编辑器。"""

    platform = "zhihu"
    home_url = "https://www.zhihu.com"
    editor_url = "https://zhuanlan.zhihu.com/write"
    login_detector_selector = ".AppHeader-profileAvatar, img.Avatar.AppHeader-profileAvatar"
    login_button_selector = "a:has-text('登录'), button:has-text('登录'), a:has-text('登录/注册')"

    def _submit(self, page, payload: dict) -> str:
        page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # 知乎标题
        title_el = page.locator("input[placeholder*=标题], textarea[placeholder*=标题], "
                                ".Editable input, [class*=title] input").first
        if title_el.count() > 0:
            title_el.fill(payload["title"])
        else:
            payload["body_md"] = f"# {payload['title']}\n\n{payload['body_md']}"

        # 正文
        body = None
        for sel in [".Editor-content textarea", ".Editor-content [contenteditable='true']",
                    ".CodeMirror", ".ProseMirror", "[contenteditable='true']",
                    ".ql-container", ".w-e-text-container"]:
            if page.locator(sel).count() > 0:
                body = page.locator(sel).first
                break
        if body is None:
            raise PublishError("知乎正文编辑器选择器未命中，请登录后跑 publish 精调")

        body.click()
        page.keyboard.insert_text(payload["body_md"])
        page.wait_for_timeout(2000)

        if payload["mode"] == "draft":
            save_btn = page.locator("button:has-text('存草稿'), button:has-text('保存草稿')")
            if save_btn.count() > 0:
                save_btn.first.click()
                page.wait_for_timeout(2000)
            return "draft saved"

        pub = page.locator("button:has-text('发布'), button:has-text('发布文章')")
        if pub.count() > 0:
            pub.first.click()
            page.wait_for_timeout(2000)

        confirm = page.locator("button:has-text('确认发布'), button:has-text('确定发布'), button:has-text('发布')")
        if confirm.count() > 0:
            confirm.first.click()
            page.wait_for_timeout(3000)
        return "published"


ADAPTERS = {
    "juejin": JuejinAdapter,
    "toutiao": ToutiaoAdapter,
    "csdn": CSDNAdapter,
    "zhihu": ZhihuAdapter,
}

# 各平台「草稿箱/我的文章」快捷入口 —— status 命令用来让用户点开浏览器验证
PLATFORM_MANAGE_URLS: dict[str, str] = {
    "juejin":  "https://juejin.cn/user",
    "toutiao": "https://mp.toutiao.com/profile_v4/graphic/articles",
    "csdn":    "https://mp.csdn.net/mp_blog/manage/article",
    "zhihu":   "https://zhuanlan.zhihu.com/",
}


def get_adapter(platform: str, account: str, cfg: dict):
    if platform not in ADAPTERS:
        raise SystemExit(
            f"未知平台 {platform}；可用平台: {', '.join(ADAPTERS)}"
        )
    return ADAPTERS[platform](account=account, cfg=cfg)
