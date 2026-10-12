# ghtrend — GitHub Trending 采集、多角度解读生成与多平台发布工具

v1 为**全离线 dry-run 流水线**：抓取 GitHub Trending → SQLite 归档选题（去重 + 角度轮换）→
GitHub REST enrich → 按角度 profile 生成解读文章（mermaid 架构图 + AI 生成内容声明）→
多平台发布变体（仅落盘，不真实发布）。

## 快速开始

```bash
pip install -e ".[dev]"

# 全离线端到端（抓取用内置真实页面快照，生成用 canned LLM 响应）
python -m ghtrend.pipeline --mock-llm --limit 1

# 单步重跑（每步幂等）
python -m ghtrend.pipeline --step fetch
python -m ghtrend.pipeline --step select --limit 2
python -m ghtrend.pipeline --step generate --mock-llm
python -m ghtrend.pipeline --step adapt

# 真实运行：真实抓取 + 真实 LLM（需先配端点）
python -m ghtrend.pipeline --limit 1
```

测试：

```bash
pytest          # 全部离线，无需网络
```

## 配置

`config.yaml`（环境变量可覆盖关键项）：

| 配置 | 说明 | 环境变量覆盖 |
| --- | --- | --- |
| `languages` / `periods` | 抓取的语言与周期（daily/weekly/monthly） | — |
| `angles` | 角度 prompt profile（入门介绍/架构精讲/成功故事/用途想象，可扩展） | — |
| `limits` | 每日文章数、select_limit、抓取间隔、README 截断 | — |
| `llm` | OpenAI 兼容端点 | `GHTREND_LLM_BASE_URL` / `GHTREND_LLM_API_KEY` / `GHTREND_LLM_MODEL` |
| `enrich` | GitHub REST | token 走 `GHTREND_GITHUB_TOKEN`（未设置则未认证限额运行） |

## 数据与产物

```
config.yaml               语言/周期/角度/限额
data/snapshots/*.json     每次抓取的原始快照
archive.db                归档 (repo, first_seen, last_seen, times_trending, covered_angles, article_url)
artifacts/selection.json  选题结果（repo × angle）
artifacts/enriched/*.json GitHub REST 元数据 + README 缓存
artifacts/{date}/*.md     生成文章（frontmatter + 正文 + mermaid + AI 声明 + 来源链接）
artifacts/payloads/{platform}/*.json   平台就绪 payload（dryrun adapter 落盘）
```

流水线各步通过磁盘文件传递状态，因此每一步都可独立重跑；成文后会把已用角度回写归档，
下轮选题自动过滤已覆盖的 repo+angle 组合并轮换角度。

## 合规（GB 45438-2025）

每篇文章尾部固定携带 AI 生成内容声明（`ghtrend/generate.py` 的 `AI_DISCLOSURE`），
来源仓库链接固定携带；发布 payload 标记 `ai_disclosure: true`，默认 `draft_only: true`。

## 多平台发布 adapter（v1 仅脚手架）

统一 `Article` 模型 → 每平台变体（标题字数/标签数/摘要差异化，规避一稿多发判重）→
`PublishAdapter` 接口。本轮只实现 `DryRunAdapter`（payload 落盘）。

**掘金草稿 adapter 接入计划**（下一轮）：

1. 浏览器自动化采用 CDP 接管**真实 Chrome（headed）**，持久化用户 profile，不用 headless
   （社区实测 headless 易触发风控/验证码）；
2. **只进草稿箱，不自动点发布**——发布前必须人工确认；
3. 同平台两次操作间隔 **≥5 分钟**，单平台失败隔离，不拖垮全链路；
4. 谨慎处理账户风控：新号频繁建/删草稿、高频调用会触发 `err_no:1012 账户受限`（冷却约 24h）；
5. 头条/知乎/CSDN 渐进接入（知乎需 CDP + Draft.js 逐字输入，CSDN 需 CKEditor 源码模式）。

## 项目结构

```
ghtrend/
  scrape.py     trending 抓取 + 解析（解析逻辑单文件隔离，改版只修这里）
  archive.py    SQLite 归档 + 选题（去重/角度轮换）
  enrich.py     GitHub REST 元数据 + README + 顶层树
  generate.py   角度 prompt profile + OpenAI 兼容调用 + --mock-llm
  adapt.py      Article 模型 + 平台变体 + adapter 接口 + dryrun adapter
  pipeline.py   CLI：fetch → archive → select → enrich → generate → adapt
  fixtures/trending_sample.html   真实页面快照（解析回归测试用）
tests/          pytest（全离线）
```
