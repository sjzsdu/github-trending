# ghtrend — GitHub Trending 采集、多角度解读生成与自动发布

生成走**真实 OpenAI 兼容 LLM**（默认路径，`--mock-llm` 仅为离线测试保留）；
发布按 sau（social-auto-upload）的 UX 提供 per-platform `login / check / publish`
与命名账号持久化登录态，`run` 一条命令自动完成 采集→生成→发布。

## 快速开始

### 1. 安装（一次性）

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)（`brew install uv` 或 `curl -LsSf https://astral.sh/uv/install.sh | sh`）。

```bash
git clone git@github.com:sjzsdu/github-trending.git
cd github-trending
uv sync --extra publish --extra dev   # 依赖 + Playwright（真实发布）+ pytest
uv run playwright install chromium
```

> `uv sync` 会把 `ghtrend` 命令装到 `.venv/bin/ghtrend`，该目录不在 PATH 上，直接敲
> `ghtrend` 会报 command not found。两种解法任选其一：
>
> ```bash
> # a) 软链到已在 PATH 的目录（推荐，一次即可）
> ln -sf "$PWD/.venv/bin/ghtrend" ~/.local/bin/ghtrend
> # b) 每次走 uv run：uv run ghtrend --help
> ```

下文示例假定已做软链；crontab 里请用绝对路径 `.venv/bin/ghtrend`。

### 2. 配置 key（一次性，只走环境变量，不入库）

```bash
echo 'GHTREND_LLM_API_KEY=sk-xxx' > ~/.ghtrend.env   # 或已有的 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY
echo 'GHTREND_GITHUB_TOKEN=ghp_xxx' >> ~/.ghtrend.env # 可选，enrich 提升 GitHub API 配额；未设置自动降级
chmod 600 ~/.ghtrend.env
set -a; . ~/.ghtrend.env; set +a   # 让当前 shell 生效（cron 模板已内置此步）
```

### 3. 各平台登录（一次性，弹出浏览器人工扫码）

**每个平台各有独立 profile**（`profiles/{platform}/{account}/`），必须分别登录一次：

```bash
ghtrend juejin  login --account main   # 掘金
ghtrend toutiao login --account main   # 头条
ghtrend csdn    login --account main   # CSDN
ghtrend zhihu   login --account main   # 知乎
```

登录态存各平台 profile 里；以后跑 `ghtrend run` 会依次向已登录平台发草稿。
未登录的平台会**干净降级**（提示"未登录，先 login"），不阻塞其他平台发布。

```bash
# 查各平台登录态
ghtrend juejin  check
ghtrend toutiao check
ghtrend csdn    check
ghtrend zhihu   check
```

浏览器打开时若已是登录状态，脚本会自动检测到头像元素并直接完成。

### 4. 日常使用

```bash
# 一条命令自动走完：采集 → 归档选题 → 生成(真实 LLM) → 全部平台发草稿
ghtrend run

# 查看各平台登录态 + 最近发布记录 + 管理页快捷链接
ghtrend status                       # 全平台汇总（headless，几秒出结果）
ghtrend status --limit 10            # 每平台显示 10 条
ghtrend juejin status                # 只看掘金
ghtrend csdn status --account main   # 指定账号

# sau 风格手动三件套
ghtrend juejin check                             # 查登录态
ghtrend juejin publish --latest                  # 发最新文章（默认 draft）
ghtrend juejin publish --latest --mode publish   # 真实直发（发布前请人工过一遍内容）

# 常用参数
ghtrend run --limit 3                    # 本次最多生成 3 篇（默认受 limits.select_limit 约束）
ghtrend run --mode publish               # 单次覆盖为真实发布
ghtrend run --platforms juejin,toutiao   # 覆盖发布平台列表
```

要点：

- 默认只进**草稿箱**（人工确认后再点发布）；要直发用 `--mode publish`。
- 每天生成数量由 config.yaml 的 `limits.daily_articles` 控制；语言/周期/角度也都在 config.yaml 改。
- 发布间隔强制 ≥5 分钟；已发布组合记录在 `artifacts/publish_log.json`，**重跑幂等不重复发**。
- 各平台草稿箱验证：`ghtrend status` 输出里有管理页快捷链接，直接点开浏览器就能看。

### 5. 离线试跑 / 测试

```bash
uv run python -m ghtrend.pipeline --mock-llm --limit 1   # 不调 LLM、不发布
uv run pytest
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
30 9 * * * set -a; . "$HOME/.ghtrend.env"; set +a; cd /path/to/github-trending && .venv/bin/ghtrend run >> run.log 2>&1
```

```bash
# macOS launchd 等价物（~/Library/LaunchAgents/dev.ghtrend.run.plist）：
# 用 launchctl load 加载；ProgramArguments 同上，StartCalendarInterval Hour=9 Minute=30
```

`run` 的 cron 安全性：幂等（publish_log.json 记录已发布组合，重跑跳过）、每日预算
由 `limits.daily_articles` 硬约束、平台间隔强制 ≥300s、单平台失败隔离不拖垮其他平台、
有失败则退出码 1（可接告警）。

## 常见问题

- **`command not found: ghtrend`**：`.venv/bin` 不在 PATH 上，见快速开始第 1 步的软链方案（或改用 `uv run ghtrend …`）。
- **`check` 提示未登录，但浏览器里明明是登录状态**：多半是平台页面改版导致登录态选择器失效。修法：`ghtrend status` 先定位是哪个平台，再到对应平台首页 F12 看头像元素 class，更新 `ghtrend/publish.py` 里该 adapter 的 `login_detector_selector`；仍不行就重新 `ghtrend <platform> login`。
- **掘金 `err_no:1012 账户受限`**：新号高频建草稿/发布会触发，冷却约 24h；保持默认 draft + ≥5 分钟间隔 + 每日预算，不要调低间隔（详见下节）。
- **`publish --latest` 报「artifacts 下没有文章」**：先跑一次 `ghtrend run`（或离线 `uv run python -m ghtrend.pipeline --mock-llm --limit 1`）生成文章。
- **如何验证各平台草稿箱里有没有文章？** `ghtrend status` 一键查看全平台登录态 + 最近发布记录 + 管理页快捷链接（直接点开浏览器就能到草稿箱）。
- **四个平台都要登录吗？** 是的，每个平台各有独立 profile（`profiles/{platform}/{account}/`），必须分别 `ghtrend <platform> login` 一次。未登录的平台会干净降级，不阻塞其他平台发布。

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
| `angles` | 角度 prompt profile（上手实测/架构精讲/成功故事/用途推演，可扩展）；选题时按项目特征自动路由：AI 类→用途推演、10000+ stars→成功故事、1000+ stars→架构精讲、新面孔→上手实测（角度已覆盖则回退轮换） | — |
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
profiles/{platform}/{account}/   浏览器登录态（cookie，已 gitignore）
```

流水线各步通过磁盘文件传递状态，因此每一步都可独立重跑；成文后会把已用角度回写归档，
下轮选题自动过滤已覆盖的 repo+angle 组合并轮换角度。

## 合规（GB 45438-2025）

每篇文章尾部固定携带 AI 生成内容声明（`ghtrend/generate.py` 的 `AI_DISCLOSURE`），
来源仓库链接固定携带；发布 payload 标记 `ai_disclosure: true`，默认 `draft_only: true`。

## 多平台发布 adapter

统一 `BaseBrowserAdapter` 基类（持久化 profile + login/check/publish 通用逻辑）→ 每平台子类
只填专属 URL + 选择器 + `_submit` 发布弹窗逻辑。

全部 4 个平台都已接入真实 DOM 自动化，`ghtrend run` 默认会向所有已登录平台依次发草稿：

| 平台 | 编辑器 URL | 管理页（草稿箱入口） | 状态 |
|---|---|---|---|
| **juejin** | `https://juejin.cn/editor/drafts/new` | `https://juejin.cn/user` | ✅ 完整（掘金 Markdown 编辑器 + 分类/标签弹窗 + AI 声明） |
| **toutiao** | `https://mp.toutiao.com/profile_v4/graphic/publish` | `https://mp.toutiao.com/profile_v4/graphic/articles` | ✅ 真实（字节系头条号创作中心） |
| **csdn** | `https://mp.csdn.net/mp_blog/creation/editor` | `https://mp.csdn.net/mp_blog/manage/article` | ✅ 真实（CSDN CKEditor 富文本） |
| **zhihu** | `https://zhuanlan.zhihu.com/write` | `https://zhuanlan.zhihu.com/` | ✅ 真实（知乎专栏富文本） |

**各平台编辑器选择器**（已实测命中，`ghtrend status` 可快速验证发布结果）：

| 平台 | 标题 | 正文 | 草稿保存 |
|---|---|---|---|
| juejin | `.title-input` | `.CodeMirror` | 自动保存 |
| toutiao | `textarea[placeholder*=标题]` | `.ProseMirror` | `button:has-text('保存草稿')` |
| csdn | `textarea#txtTitle` | `iframe.cke_wysiwyg_frame` → body | `button:has-text('保存草稿')` |
| zhihu | `textarea[placeholder*=标题]` | `.ProseMirror` / `[contenteditable='true']` | `button:has-text('存草稿')` |

**风控对策**（掘金/头条/知乎同属字节/腾讯/知乎系，识别自动化发布较严）：
- 默认 `mode: draft`，只进草稿箱；先小批量跑通再考虑直发
- 平台间间隔 ≥300s（`publish.interval_seconds`，可改但不低于风控下限）
- `publish.<platform>.headless` 默认 `false`；cron 服务器可改 `true` 但更易被识别
- 建议每个平台先 `ghtrend <platform> login`（headed）登录一次建立正常行为轨迹

**profile 目录**：各平台独立隔离，绝不共享 cookie：
```
profiles/juejin/main/
profiles/toutiao/main/
profiles/csdn/main/
profiles/zhihu/main/
```

## 项目结构

```
ghtrend/
  scrape.py     trending 抓取 + 解析（解析逻辑单文件隔离，改版只修这里）
  archive.py    SQLite 归档 + 选题（去重/角度轮换）
  enrich.py     GitHub REST 元数据 + README（raw 回退）+ 顶层树
  generate.py   角度 prompt profile + 真实 LLM 调用（--mock-llm 仅测试）
  adapt.py      Article 模型 + 平台变体 + dryrun payload
  publish.py    sau 风格真实发布：JuejinAdapter（login/check/publish）+ 账号 profile
  cli.py        ghtrend CLI 入口：<platform> login|check|publish / run（通过 [project.scripts] 暴露）
  pipeline.py   六步可单步重跑（python -m ghtrend.pipeline --mock-llm --limit N）
  fixtures/trending_sample.html   真实页面快照（解析回归测试用）
tests/          pytest（全离线；浏览器依赖的用例在未登录时干净跳过/降级）
```
