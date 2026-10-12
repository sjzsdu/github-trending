"""变体生成 + dryrun adapter 测试。"""

import json

from ghtrend.adapt import PLATFORM_SPECS, DryRunAdapter, adapt_article, make_variant, truncate
from ghtrend.models import Article

LONG_TITLE = "这是一个非常非常非常长的标题，目的是超过头条平台三十个字的上限限制，看看截断逻辑是否正确工作"


def _article() -> Article:
    return Article(
        slug="2026-10-12_open-webui_intro",
        repo="open-webui/open-webui",
        angle="intro",
        angle_name="入门介绍",
        title=LONG_TITLE,
        description="面向开发者的开源 AI 界面，支持 Ollama 与 OpenAI API。",
        tags=["Python", "GitHub", "开源", "AI", "Web", "extra"],
        body="正文内容\n\n## 架构一瞥\n\n```mermaid\ngraph TD\n```\n",
    )


def test_truncate():
    assert truncate("short", 10) == "short"
    assert len(truncate(LONG_TITLE, 30)) == 30
    assert truncate(LONG_TITLE, 30).endswith("…")


def test_variant_respects_platform_spec():
    v = make_variant(_article(), "toutiao")
    spec = PLATFORM_SPECS["toutiao"]
    assert len(v.title) <= spec["title_max"]
    assert len(v.tags) <= spec["tags_max"]
    assert len(v.summary) <= spec["summary_max"]

    v2 = make_variant(_article(), "juejin")
    assert PLATFORM_SPECS["juejin"]["suffix"] in v2.title  # 掘金标题有平台后缀
    assert v2.title != v.title  # 平台间标题差异化


def test_variant_tags_differs():
    a = _article()
    v_toutiao = make_variant(a, "toutiao")
    v_juejin = make_variant(a, "juejin")
    assert len(v_toutiao.tags) < len(v_juejin.tags)


def test_dryrun_adapter_writes_payload(tmp_path):
    results = adapt_article(_article(), tmp_path, platforms=["juejin", "toutiao"])
    assert [r.platform for r in results] == ["juejin", "toutiao"]
    assert all(r.ok for r in results)
    payload = json.loads((tmp_path / "juejin" / "2026-10-12_open-webui_intro.json").read_text())
    assert payload["draft_only"] is True
    assert payload["ai_disclosure"] is True
    assert payload["title"].endswith(PLATFORM_SPECS["juejin"]["suffix"])
    assert payload["body_md"].startswith("正文内容")
    assert results[0].detail.endswith(".json")


def test_adapter_interface():
    from ghtrend.adapt import PublishAdapter

    assert issubclass(DryRunAdapter, PublishAdapter)
