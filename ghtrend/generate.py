"""文章生成（dry-run）：OpenAI 兼容端点 + 角度 prompt profile + --mock-llm。

输出 markdown：
  - frontmatter（title/description/tags/cover/canonical）
  - 正文中含 mermaid 架构图小节
  - 尾部：GB 45438-2025 AI 生成内容声明 + repo 来源链接（调研结论 F5/F6）
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import requests

from .models import Article, RepoDetails, SelectedTopic

AI_DISCLOSURE = (
    "本文由 AI 辅助生成（含自动生成的架构图与文案），并经人工审校。"
    "依据《人工智能生成合成内容标识办法》及 GB 45438-2025 进行标识，"
    "未经许可不得移除或篡改本声明。"
)


def find_angle(cfg: dict, angle_id: str) -> dict:
    for a in cfg["angles"]:
        if a["id"] == angle_id:
            return a
    raise KeyError(f"angle profile not found: {angle_id}")


def build_messages(topic: SelectedTopic, details: RepoDetails, angle: dict, cfg: dict) -> list[dict]:
    """组装 prompt：角度 profile × repo 输入包。"""
    system = (
        "你是一名一线工程师，在个人技术专栏写复盘文章，读者是同行开发者。写作铁律：\n"
        "1. 只基于给定仓库材料写作，不编造事实与数字；材料中的命令、代码、数字必须原样引用；\n"
        "2. 开头第一句直接切入项目本身，禁止“在当今/随着…的快速发展”式套话开场；\n"
        "3. 结尾落到具体建议或明确判断，禁止“总而言之/综上所述/拭目以待”式空洞收束；\n"
        "4. 以连贯段落叙述为主，少用罗列；不出现三层嵌套列表；\n"
        "5. 说人话：长短句交替，允许口语化转折；禁用空洞形容词（强大/卓越/领先/惊艳）；\n"
        "6. 正文包含一个 `## 架构一瞥` 小节，内含一块 ```mermaid graph TD 图；\n"
        "7. 全文 900-1500 字；输出 markdown 正文，不要输出 frontmatter（由流水线统一生成）。\n"
    )
    user = (
        f"写作角度：{angle.get('name', angle['id'])}\n"
        f"角度要求：{angle['prompt'].strip()}\n\n"
        f"### 仓库材料\n{details.to_prompt_text(cfg['limits']['max_readme_chars'])}\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def mock_body(topic: SelectedTopic, details: RepoDetails, angle: dict) -> str:
    """--mock-llm 的 canned 响应：完整结构，保证离线产物形态真实。"""
    name = details.repo.split("/", 1)[1]
    return (
        f"# {angle.get('name', angle['id'])}：{name}\n\n"
        f"> mock-llm 生成的演示文章（offline dry-run），仓库：[{details.repo}]({details.url})，"
        f"当前 {details.stars} stars。\n\n"
        "## 项目是什么\n\n"
        f"{details.description}\n\n"
        "## 架构一瞥\n\n"
        "```mermaid\n"
        "graph TD\n"
        "    A[CLI 入口] --> B[核心模块]\n"
        "    B --> C[数据层]\n"
        "    B --> D[集成层]\n"
        "    D --> E[外部服务]\n"
        "```\n\n"
        "## 小结\n\n"
        "以上为 mock-llm 离线演示内容，真实生成需配置 OpenAI 兼容端点。\n"
    )


def call_llm(cfg: dict, messages: list[dict]) -> str:
    """调用 OpenAI 兼容 /chat/completions（真实生成路径）。

    key 经 config.resolve_llm_key 回退链解析；未配置时 fail-fast 并给出
    可操作提示（离线测试用 --mock-llm）。
    """
    from .config import require_llm_key

    llm = cfg["llm"]
    api_key = require_llm_key(cfg)
    resp = requests.post(
        llm["base_url"].rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": llm["model"],
            "messages": messages,
            "temperature": llm.get("temperature", 0.7),
            "max_tokens": llm.get("max_tokens", 4096),
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def make_article(
    topic: SelectedTopic,
    details: RepoDetails,
    cfg: dict,
    *,
    mock: bool = False,
) -> Article:
    """生成一篇文章对象（body 来自 LLM 或 mock）。"""
    angle = find_angle(cfg, topic.angle)
    repo_url = details.url or f"https://github.com/{details.repo}"
    if mock:
        body = mock_body(topic, details, angle)
    else:
        body = call_llm(cfg, build_messages(topic, details, angle, cfg))

    date = _dt.date.today().isoformat()
    slug = f"{date}_{details.repo.replace('/', '-')}_{topic.angle}"
    tags = [t for t in [details.language, "GitHub", "开源"] if t]
    return Article(
        slug=slug,
        repo=details.repo,
        angle=topic.angle,
        angle_name=angle.get("name", topic.angle),
        title=f"{angle.get('name', topic.angle)}：{details.repo.split('/', 1)[1]}",
        description=details.description or details.repo,
        tags=tags,
        # GitHub 自动生成的社交卡片（含 repo 名/star 数），零成本保证图文并茂
        cover=f"https://opengraph.githubassets.com/1/{details.repo}",
        canonical="",  # 自有站点出口就绪后回填
        body=(
            body.strip()
            + "\n\n---\n\n"
            + f"**来源仓库**：[{details.repo}]({repo_url})\n\n"
            + f"> {AI_DISCLOSURE}\n"
        ),
        meta={"times_trending": topic.times_trending, "mock": mock},
    )


def save_article(article: Article, artifacts_dir: str | Path) -> Path:
    """文章落盘：artifacts/{date}/{slug}.md。已存在则跳过（幂等），返回路径。"""
    out = Path(artifacts_dir) / article.slug.split("_", 1)[0]
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{article.slug}.md"
    if not path.exists():
        path.write_text(article.render_markdown(), encoding="utf-8")
    return path


def load_article_meta(path: str | Path) -> dict:
    """读取文章元数据（配合 adapt 步骤重跑；从 md 解析 frontmatter 最小实现）。"""
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---"):
        return json.loads(text)
    end = text.index("---", 3)
    fm: dict = {}
    for line in text[3:end].splitlines():
        if line.startswith("  - "):
            fm.setdefault("tags", []).append(line[4:].strip())
        elif ":" in line:
            k, v = line.split(":", 1)
            if v.strip() == "":
                continue  # 列表型键（如 tags:）由后续缩进行填充
            fm[k.strip()] = v.strip()
    fm["body"] = text[end + 3 :].strip()
    return fm
