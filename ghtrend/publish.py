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
    url: str = ""   # 草稿箱或已发布文章的真实 URL（可空，部分平台无法拿到）


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

    def publish(self, article: Article, mode: str = "draft", review: bool = False) -> PublishOutcome:
        self._review = review
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
            ctx = self._open_context(p, headless=(False if review else self.headless))
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not self._is_logged_in(page):
                    raise NotLoggedInError("登录态已失效，请重新 login")
                detail = self._submit(page, payload)
                final_url = page.url
            finally:
                ctx.close()
        return PublishOutcome(self.platform, self.account, True, mode, detail, url=final_url)

    def publish_draft(self, url: str, article: Article | None = None,
                      review: bool = False) -> PublishOutcome:
        """从已保存的草稿直接发布（不重新填内容，打开草稿页点发布）。

        仅支持保存后 URL 含草稿 ID 的平台（juejin / zhihu）；
        toutiao/csdn 保存后 URL 不变，无法定位草稿，子类不覆写 _publish_existing
        即走基类的 NotImplementedError 手动引导。

        review=True 时强制有头浏览器、在最终发布按钮前暂停等你手动点击。
        """
        self._review = review
        if not self._has_profile():
            raise NotLoggedInError(
                f"{self.platform} 账号 {self.account} 未登录：先运行 "
                f"ghtrend {self.platform} login --account {self.account}"
            )
        _require_playwright()
        from playwright.sync_api import sync_playwright

        payload = None
        if article is not None:
            payload = build_publish_payload(
                article, self.platform, "publish",
                max_tags=self.max_tags, category=self.category,
                ai_declaration=self.ai_declaration,
            )
        with sync_playwright() as p:
            ctx = self._open_context(p, headless=(False if review else self.headless))
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not self._is_logged_in(page):
                    raise NotLoggedInError("登录态已失效，请重新 login")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                detail = self._publish_existing(page, payload)
                final_url = page.url
            finally:
                ctx.close()
        return PublishOutcome(self.platform, self.account, True, "publish", detail, url=final_url)

    def _await_user_review(self, page, hint: str) -> str:
        """暂停：浏览器保持打开，等用户在页面上手动点发布；按回车继续记录。"""
        print(f"\n[{self.platform}] ⏸ 人工复核：{hint}")
        print(f"    浏览器保持打开，请在页面上确认并点击发布按钮。")
        try:
            input(f"    完成后按回车关闭浏览器并记录结果  » ")
        except (EOFError, KeyboardInterrupt):
            pass
        return "published (manual review)"

    def _publish_existing(self, page, payload: dict | None) -> str:
        """在已导航到草稿编辑页后点击发布。支持的平台覆写；默认手动引导。"""
        raise NotImplementedError(
            f"{self.platform} 保存草稿后 URL 不含草稿 ID，无法从草稿直接发布；"
            f"请到 {PLATFORM_MANAGE_URLS.get(self.platform, self.home_url)} 手动点发布，"
            f"或重新 ghtrend {self.platform} publish --mode publish"
        )

    # ---- 子类实现：填充编辑器 + 发布弹窗 ----

    @abstractmethod
    def _submit(self, page, payload: dict) -> str:
        """打开编辑器、填标题/正文、处理 publish 弹窗。返回描述字符串。"""

    def delete_article(self, url: str) -> str:
        """删除一篇草稿/已发布文章。返回描述字符串（子类可覆写）。

        用 headed context（危险操作，让用户看到确认弹窗更安全）。
        """
        if not self._has_profile():
            raise NotLoggedInError(
                f"{self.platform} 账号 {self.account} 未登录：先运行 "
                f"ghtrend {self.platform} login --account {self.account}"
            )
        _require_playwright()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = self._open_context(p, headless=False)  # headed：用户能看到确认弹窗
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not self._is_logged_in(page):
                    raise NotLoggedInError("登录态已失效，请重新 login")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                return self._do_delete(page, url)
            finally:
                ctx.close()

    def _do_delete(self, page, url: str) -> str:
        """在已导航到目标页面后执行删除。子类必须覆写。"""
        raise NotImplementedError(f"{self.platform} delete 尚未实现，请手动到管理页删除")


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

        return self._publish_dialog(page, payload)

    def _publish_dialog(self, page, payload: dict) -> str:
        """掘金发布弹窗：分类 → 标签 → 摘要 → 确定并发布。"""
        page.locator("button", has_text="发布").first.click()
        page.wait_for_timeout(2000)

        # 分类
        if payload.get("category"):
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
        for tag in payload.get("tags", []):
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

        if getattr(self, "_review", False):
            return self._await_user_review(page, "掘金发布弹窗已填好分类/标签/摘要，请点「确定并发布」")
        page.locator("button", has_text="确定并发布").first.click()
        page.wait_for_timeout(3000)
        return "published"

    def _publish_existing(self, page, payload: dict | None) -> str:
        """草稿编辑页：内容已在，等编辑器就绪后直接走发布弹窗。"""
        page.wait_for_selector(".title-input", timeout=30000)
        page.wait_for_timeout(1000)
        return self._publish_dialog(page, payload or {"category": self.category, "tags": [], "summary": ""})

    def _do_delete(self, page, url: str) -> str:
        """掘金：直接在草稿编辑器页找删除按钮 → 确认弹窗。"""
        # url 已经是草稿编辑器页（juejin.cn/editor/drafts/<id>），找删除按钮
        for sel in [
            "button:has-text('删除')",
            "a:has-text('删除')",
            "[class*=delete]",
            "[class*=more] button:has-text('删除')",
        ]:
            btn = page.locator(sel).first
            if btn.count() > 0:
                btn.click()
                page.wait_for_timeout(1000)
                break
        else:
            # 兜底：进创作中心文章管理
            page.goto("https://juejin.cn/creator/content/article",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            # 切到草稿 tab
            for tab_text in ["草稿箱", "草稿"]:
                tab = page.locator(f"a:has-text('{tab_text}'), button:has-text('{tab_text}')")
                if tab.count() > 0:
                    tab.first.click()
                    page.wait_for_timeout(2000)
                    break
            btn = page.locator("button:has-text('删除'), a:has-text('删除')").first
            if btn.count() == 0:
                raise PublishError("掘金找不到删除按钮")
            btn.click()
            page.wait_for_timeout(1000)
        # 确认弹窗
        confirm = page.locator("button:has-text('确定'), button:has-text('确认删除'), "
                               "button:has-text('确认'), [class*=confirm] button")
        if confirm.count() > 0:
            confirm.first.click()
            page.wait_for_timeout(2000)
        return "draft deleted"


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

    def _do_delete(self, page, url: str) -> str:
        """头条：进管理页草稿 tab → 每行删除按钮 → 确认弹窗。"""
        page.goto("https://mp.toutiao.com/profile_v4/manage/draft",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        # 每行的删除按钮是 a.op-button
        del_btn = page.locator("a.op-button:has-text('删除'), a:has-text('删除')").first
        if del_btn.count() == 0:
            raise PublishError("头条草稿列表为空，找不到删除按钮")
        del_btn.click()
        page.wait_for_timeout(800)
        confirm = page.locator("button:has-text('确定'), button:has-text('确认')")
        if confirm.count() > 0:
            confirm.first.click()
            page.wait_for_timeout(2000)
        return "draft deleted"


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

    def _do_delete(self, page, url: str) -> str:
        """CSDN 暂未接入：提示手动删除。"""
        raise NotImplementedError(
            "CSDN 删除暂未自动化，请手动到 "
            "https://mp.csdn.net/mp_blog/manage/article 删除"
        )


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

    def _publish_existing(self, page, payload: dict | None) -> str:
        """知乎草稿编辑页（zhuanlan.zhihu.com/p/<id>/edit）：内容已在，直接点发布。"""
        page.wait_for_timeout(2000)
        if getattr(self, "_review", False):
            return self._await_user_review(page, "知乎草稿已打开，请在页面上点「发布」按钮")
        pub = page.locator("button:has-text('发布'), button:has-text('发布文章')")
        if pub.count() == 0:
            raise PublishError("知乎草稿页找不到发布按钮")
        pub.first.click()
        page.wait_for_timeout(2000)
        confirm = page.locator("button:has-text('确认发布'), button:has-text('确定发布'), button:has-text('发布')")
        if confirm.count() > 0:
            confirm.first.click()
            page.wait_for_timeout(3000)
        return "published"

    def _do_delete(self, page, url: str) -> str:
        """知乎暂未接入：提示手动删除。"""
        raise NotImplementedError(
            "知乎删除暂未自动化，请手动到知乎创作中心删除草稿"
        )


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
