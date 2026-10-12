"""mock 生成产物形态测试：frontmatter / mermaid / AI 声明 / 来源链接。"""

from ghtrend.generate import AI_DISCLOSURE, find_angle, make_article, mock_body
from ghtrend.models import RepoDetails, SelectedTopic

CFG = {
    "angles": [
        {"id": "intro", "name": "入门介绍", "prompt": "写一篇入门介绍。"},
        {"id": "architecture", "name": "架构精讲", "prompt": "精讲架构。"},
    ],
    "limits": {"max_readme_chars": 12000},
    "llm": {"base_url": "https://example.invalid/v1", "model": "m", "api_key": ""},
}

DETAILS = RepoDetails(
    repo="open-webui/open-webui",
    url="https://github.com/open-webui/open-webui",
    description="User-friendly AI Interface",
    stars=154440,
    readme="# open-webui\n\nFeatures...",
    tree=["README.md", "src"],
)


def test_find_angle():
    assert find_angle(CFG, "intro")["name"] == "入门介绍"
    try:
        find_angle(CFG, "nope")
        raise AssertionError("should raise KeyError")
    except KeyError:
        pass


def test_mock_article_shape():
    topic = SelectedTopic(repo="open-webui/open-webui", angle="intro", angle_name="入门介绍")
    article = make_article(topic, DETAILS, CFG, mock=True)
    md = article.render_markdown()

    assert md.startswith("---\n")
    assert f"repo: {article.repo}" in md
    assert "angle: intro" in md
    assert "title: " in md
    assert "```mermaid" in md          # mermaid 架构小节
    assert "## 架构一瞥" in md
    assert AI_DISCLOSURE in md          # GB 45438-2025 AI 生成声明
    assert "https://github.com/open-webui/open-webui" in md  # 来源链接
    assert article.slug.endswith("_intro")


def test_mock_body_mentions_repo():
    body = mock_body(
        SelectedTopic(repo="a/b", angle="intro", angle_name="入门介绍"),
        DETAILS,
        CFG["angles"][0],
    )
    assert "open-webui" in body and "```mermaid" in body
