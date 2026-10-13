# ghtrend — GitHub Trending 采集、多角度解读生成与自动发布

生成走**真实 OpenAI 兼容 LLM**（默认路径，`--mock-llm` 仅为离线测试保留）；
发布按 sau（social-auto-upload）的 UX 提供 per-platform `login / check / publish`
与命名账号持久化登录态，`run` 一条命令自动完成 采集→生成→发布。

## 快速开始

```bash
pip install -e ".[dev]"        # 基础（采集/生成/测试）
pip install -e ".[publish]"    # 真实发布（Playwright）
playwright install chromium

# 配置 LLM key（只走环境变量，不入库；任选其一）
export GHTREND_LLM_API_KEY=sk-xxx   # 或 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY
export GHTREND_GITHUB_TOKEN=ghp_xxx # 可选，enrich 提升配额（未设置会自动降级：raw README + trending 快照兜底）

# 首次登录掘金（一次性，人工扫码；登录态存 profiles/juejin/<account>/，已 gitignore）
python -m ghtrend.cli juejin login --account main

# 自动全链路：fetch → 归档选题 → enrich → 真实 LLM 生成 → 发布（默认 draft）
python -m ghtrend.cli run --account main

# sau 风格手动三件套
python -m ghtrend.cli juejin check   --account main
python -m ghtrend.cli juejin publish --account main --latest            # 默认 draft
python -m ghtrend.cli juejin publish --account main --latest --mode publish   # 真实发布

# 离线测试 / dry-run（不触网、不调 LLM）
python -m ghtrend.pipeline --mock-llm --limit 1
pytest
```

发布模式：config `publish.mode: draft|publish`（默认 `draft`，只进草稿箱，人工确认后
在掘金后台点发布）；CLI `--mode publish` 可单次覆盖为真实发布。两种模式都会在
发布弹窗勾选“AI 生成内容”声明（GB 45438-2025）。

## 定时自动发布（cron / launchd）

```cron
# crontab -e：每天 09:30 自动采集+生成+发布（掘金 draft）
# key/token 放 ~/.ghtrend.env（chmod 600），不进 crontab、不进 git：
#   GHTREND_LLM_API_KEY=sk-xxx
#   GHTREND_GITHUB_TOKEN=ghp_xxx
30 9 * * * set -a; . "$HOME/.ghtrend.env"; set +a; cd /path/to/github-trending && .venv/bin/python -m ghtrend.cli run --account main >> run.log 2>&1
```

```bash
# macOS launchd 等价物（~/Library/LaunchAgents/dev.ghtrend.run.plist）：
# 用 launchctl load 加载；ProgramArguments 同上，StartCalendarInterval Hour=9 Minute=30
```

`run` 的 cron 安全性：幂等（publish_log.json 记录已发布组合，重跑跳过）、每日预算
由 `limits.daily_articles` 硬约束、平台间隔强制 ≥300s、单平台失败隔离不拖垮其他平台、
有失败则退出码 1（可接告警）。

## 账号风控与安全（F4/F5，务必阅读）

- **账号风险**：新号频繁建/删草稿、高频发布会触发掘金 `err_no:1012 账户受限`
  （冷却约 24h）。默认 draft + 间隔 ≥5 分钟 + 每日预算就是为此；不要调低间隔。
- **凭据安全**：LLM key / GitHub token 只走环境变量；浏览器登录态（cookie）只存
  `profiles/`（gitignored）；两者都不会出现在日志与 git 中。
- **AI 合规**：每篇文章尾部携带 GB 45438-2025 AI 生成内容声明，发布时勾选平台
  AI 声明项；不建议勾选平台“原创”声明（内容含对他人 README 的整理）。
- **审慎发布**：默认只进草稿箱。`--mode publish` 真实发布前请人工过一遍内容。

## 配置

`config.yaml`（环境变量可覆盖关键项）：

| 配置 | 说明 | 环境变量覆盖 |
| --- | --- | --- |
| `languages` / `periods` | 抓取的语言与周期（daily/weekly/monthly） | — |
| `angles` | 角度 prompt profile（入门介绍/架构精讲/成功故事/用途想象，可扩展） | — |
| `limits` | 每日文章数、select_limit、抓取间隔、README 截断 | — |
| `llm` | OpenAI 兼容端点（默认 DeepSeek；key 只走环境变量） | `GHTREND_LLM_API_KEY`（或 `DEEPSEEK_API_KEY`/`DASHSCOPE_API_KEY`/`OPENAI_API_KEY`）、`GHTREND_LLM_BASE_URL`/`GHTREND_LLM_MODEL` |
| `enrich` | GitHub REST；限流时自动降级（raw README + trending 快照兜底） | token 走 `GHTREND_GITHUB_TOKEN`（未设置则未认证限额运行） |
| `publish` | 发布平台/模式/间隔/掘金编辑器配置 | — |

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

## 多平台发布 adapter

统一 `Article` 模型 → 每平台变体（标题字数/标签数/摘要差异化，规避一稿多发判重）→
adapter 接口。三层就绪状态：

- **juejin（真实可用）**：Playwright headed 持久化上下文，`login`（人工扫码一次）/
  `check`（登录态检测）/ `publish`（draft|publish 双模式，弹窗自动选分类、标签、
  勾选 AI 声明）。
- **toutiao（stub）**：同一 adapter 接口占位，DOM 自动化下一轮接入。
- **zhihu / csdn**：仅 dryrun payload（`artifacts/payloads/`），CLI 会明确拒绝。

掘金实现要点（风控对策，F4）：headed 真实浏览器 + 持久化 profile（非 headless）；
默认只进草稿箱；平台间隔 ≥5 分钟；单平台失败隔离。页面选择器集中在
`ghtrend/publish.py`，掘金改版时只需更新该文件（`config.yaml` 可改编辑器 URL、
分类、标签数、AI 声明开关）。

## 项目结构

```
ghtrend/
  scrape.py     trending 抓取 + 解析（解析逻辑单文件隔离，改版只修这里）
  archive.py    SQLite 归档 + 选题（去重/角度轮换）
  enrich.py     GitHub REST 元数据 + README（raw 回退）+ 顶层树
  generate.py   角度 prompt profile + 真实 LLM 调用（--mock-llm 仅测试）
  adapt.py      Article 模型 + 平台变体 + dryrun payload
  publish.py    sau 风格真实发布：JuejinAdapter（login/check/publish）+ 账号 profile
  cli.py        python -m ghtrend.cli：<platform> login|check|publish / run
  pipeline.py   python -m ghtrend.pipeline：六步可单步重跑
  fixtures/trending_sample.html   真实页面快照（解析回归测试用）
tests/          pytest（全离线；浏览器依赖的用例在未登录时干净跳过/降级）
```
