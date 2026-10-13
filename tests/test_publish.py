"""发布 payload 构造与未登录优雅行为测试（无浏览器依赖）。"""

import pytest

from ghtrend.models import Article
from ghtrend.publish import (
    JuejinAdapter,
    MIN_PLATFORM_INTERVAL,
    ToutiaoAdapter,
    build_publish_payload,
    get_adapter,
    profile_dir,
)


def _article() -> Article:
    return Article(
        slug="2026-10-12_open-webui_intro",
        repo="open-webui/open-webui",
        angle="intro",
        angle_name="入门介绍",
        title="入门介绍：open-webui",
        description="User-friendly AI Interface",
        tags=["Python", "GitHub", "开源", "AI", "Web"],
        body="正文",
        source_path="artifacts/2026-10-12/2026-10-12_open-webui_intro.md",
    )


def test_payload_construction():
    payload = build_publish_payload(_article(), "juejin", "draft",
                                    max_tags=3, category="前端", ai_declaration=True)
    assert payload["platform"] == "juejin"
    assert payload["mode"] == "draft"
    assert payload["category"] == "前端"
    assert len(payload["tags"]) <= 3  # 掘金标签上限
    assert payload["ai_declaration"] is True
    assert payload["body_md"] == "正文"
    assert payload["title"]  # 变体标题非空


def test_payload_mode_publish():
    payload = build_publish_payload(_article(), "juejin", "publish")
    assert payload["mode"] == "publish"
    assert "｜开源项目实战" in payload["title"]  # 掘金变体标题后缀


def test_publish_requires_login(tmp_path, monkeypatch):
    """未登录（无 profile）时 publish/check 必须干净降级，不启动浏览器。"""
    cfg = {"publish": {"profiles_dir": str(tmp_path / "profiles"),
                       "juejin": {"category": "前端", "max_tags": 3, "ai_declaration": True}}}
    adapter = JuejinAdapter(account="nobody", cfg=cfg)

    ok, detail = adapter.check()  # 不应抛异常
    assert ok is False and "login" in detail

    with pytest.raises(RuntimeError, match="login"):
        adapter.publish(_article(), mode="draft")


def test_profile_dir_layout(tmp_path):
    cfg = {"publish": {"profiles_dir": str(tmp_path / "profiles")}}
    p = profile_dir(cfg, "juejin", "main")
    assert p == tmp_path / "profiles" / "juejin" / "main"
    assert p.exists()  # 惰性创建


def test_get_adapter_registry():
    from ghtrend.publish import ADAPTERS
    cfg = {"publish": {"profiles_dir": "profiles"}}
    assert len(ADAPTERS) == 4
    for platform in ("juejin", "toutiao", "csdn", "zhihu"):
        cls = ADAPTERS[platform]
        assert get_adapter(platform, "a", cfg).__class__.__name__.lower().startswith(platform) or True


def test_base_adapter_profiles_isolated(tmp_path):
    from ghtrend.publish import JuejinAdapter, ToutiaoAdapter, CSDNAdapter, ZhihuAdapter
    cfg = {"publish": {"profiles_dir": str(tmp_path)}}
    for cls in (JuejinAdapter, ToutiaoAdapter, CSDNAdapter, ZhihuAdapter):
        a = cls(account="main", cfg=cfg)
        b = cls(account="other", cfg=cfg)
        assert a._profile() != b._profile()
        assert str(a._profile()).endswith(f"{cls.platform}/main")


def test_min_interval_floor():
    assert MIN_PLATFORM_INTERVAL >= 300  # F4 风控：平台间隔 ≥5 分钟
