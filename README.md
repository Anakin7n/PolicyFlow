# PolicyFlow 🐙

> 把简单请求路由到便宜模型，全自动。实际省 50-65%。

**你那金子般金贵的 Token 正在为了处理那句 “你好，在吗” 熊熊燃烧，而你的钱包在角落里默默流泪。😭**

别再把真金白银花在“刀把”上了！PolicyFlow 是一个极具性价比的智能路由。它绝不瞎“摸鱼”，而是懂得把好钢用在刀刃上：既然平价模型闭着眼睛就能给你满分答卷，何必还要浪费顶级模型的昂贵算力？
智能识别任务门槛，精准匹配“刚刚好”的算力，确保输出智商始终在线。API 成本立省 40% - 70%！用更少的钱，装最大的杯。

PolicyFlow 是一个 YAML 驱动的策略路由代理——你可以定义"什么请求用什么模型"，也可以选择系统预设好的算法自动选择最具性价比的模型。它在每次调用时自动改写 model 字段、切换供应商。不用改客户端代码，不用装中间层。

**你的 YAML。你的 Key。没有黑盒。** 策略即配置，改一行生效，不藏逻辑在代码里。供应商 API Key 直连，不做中间代理。

<p align="center">
  支持 <strong>Cursor</strong> · <strong>Claude Code</strong> · <strong>Codex CLI</strong> · <strong>Aider</strong> · <strong>ChatBox</strong> · <strong>OpenAI SDK</strong> · <strong>Anthropic 原生协议</strong> · 任意 OpenAI 兼容客户端
</p>

**一次普通办公会话，省了多少：**

```
  "帮我翻译这封邮件"          → deepseek-v4-flash      ¥0.01
  "debug 这段代码, 报错了"    → deepseek-v4-pro        ¥0.14
  "设计支付系统的数据模型"     → kimi-k2.7-code          ¥1.27
  "帮我写个 SQL 查询"         → deepseek-v4-pro        ¥0.08
  "推荐几本好看的科幻小说"       → deepseek-v4-flash      ¥0.01

  路由后实际花费 ¥1.50    如果全用 kimi-k2.7-code  ¥2.15

  单次会话省 30%。日常流量里翻译/闲聊/格式化这类轻任务占大多数
  （~70%），实际月省更高。经1000多次真实请求测试后约省 58%。
```
## “从裸调 API 到智能调度，PolicyFlow 能做什么？”

- **YAML 即策略，改一行生效。** 不写死在代码里——今天觉得"翻译"该走便宜模型，打开 yaml 把 max_cost_tier 从 mid 改成 cheap，重启即生效。
- **两种方式自由组合。** 想精确控制？写 `route_to` 把这任务钉死到一个模型，你说翻译任务用豆包，我就用豆包。想省心？算法帮你选， 8 维能力分（编码、推理、写作、多语言……），13个任务类型各有对应的八个维度权重。翻译看重写作和多语言，架构看重编码和推理，帮你选出最合适的那一个。
- **自建 Key 和平台套餐统一调度，规则透明。** 各大模型的直连 Key、各厂商Coding Plan的聚合 Key（一个 Key 下挂 Kimi/GLM/豆包/MiniMax 等多个模型）在同一条 YAML 里管理，策略统一分配到各自端点。平台 auto 的局限在于：规则不透明（无法解释为何选 A 不选 B），范围局限于套餐内模型，无法纳入自建 Key。PolicyFlow 取消了这两条边界——所有来源的模型在同一任务维度下按同一套能力评分竞争，你定策略，你来dispatch，逻辑完全可见。
**双维静默容灾。** 提供“供应商”与“模型”两层保障：支持同模型多 Provider 轮询，主Provider挂了（断连、额度耗尽）自动切备用Provider；同时每个任务支持配置 Top-3 候选模型，主模型不可用时秒切备选。双重防线兜底，全程无感，丝滑 Coding。
- **省了多少全记着。** 每条请求记入 SQLite——走了哪个策略、花了多少钱、和 baseline 比省了多少。有全屏 TUI 仪表盘，有 AI 优化引擎（分析日志出建议），有 CLI export 给你二次分析。

## 怎么用

PolicyFlow 运行在你的本地环境（默认监听 localhost:8000）。你只需把手头 AI 工具的 API Base URL 指向它，剩下的调度工作全部自动完成。不改代码、不存 Key、不碰数据。 它不属于任何第三方中介，就是一个完全透明的私有本地代理。

```
你的工具（Cursor / Claude Code / codex / …）
  └→ POST localhost:8000/v1/chat/completions
     { "model": "随便写", "messages": [...] }  

         PolicyFlow
           ├─ 看了你的消息，匹配策略
           ├─ 改写 model，切到对应供应商
           ├─ 转发请求，拿到响应
           └─ 把响应原样返回（附 X-PolicyFlow-* 头，告诉你走了哪个策略）

  响应回你的工具，和直连供应商一样。
```

## 核心流程

```
你的客户端（Cursor / Claude Code / Codex CLI / Aider / ChatBox / OpenAI SDK 等）
  发请求过来 →
    OpenAI 兼容协议  → POST http://localhost:8000/v1/chat/completions
    Anthropic 原生协议 → POST http://localhost:8000/v1/messages   （Claude Code 等 Anthropic 原生客户端用）
  请求体: { model: "gpt-4o", messages: [...], tools?: [...] }
  请求头: X-Session-ID（客户端自己生成的会话 ID，不传也行；传了的话同一 ID 30 分钟内固定走同一个模型）
  │
  ↓ PolicyFlow 收到后依次跑下面 5 步
  │
  ├── ① 智能修饰器（本地规则，0 延迟 0 费用，命中即跳过 ②③）
  │     ├─ Agent 检测   看 tools 数组 / tool_calls / system 标记 → strongest_model
  │     ├─ 会话保持     看 X-Session-ID（默认 30 min TTL）→ 复用上次模型
  │     ├─ 推理检测     扫 prompt 关键词 ≥2 个（"证明"/"系统设计"…）→ reasoning_model
  │     └─ 窗口过滤     估算 token > 当前模型窗口 → 升级到大窗口模型
  │
  ├── ② 策略匹配（按 YAML policies 从上到下扫）
  │     图片检测 → 命中即停
  │     关键词精确匹配命中 → Embedding 复核语境（≥0.25 才放行，挡掉"苹果"匹"苹果手机"这类歧义）
  │     未命中 → Embedding 全局语义匹配（阈值 0.25）
  │     仍未命中 → 走 default 策略
  │     → 命中后确定任务类型（如"代码生成"、"复杂推理"）
  │
  ├── ③ 路由决策：根据任务类型选模型
  │     策略写了 route_to → 直接用
  │     没写 route_to → 按任务类型的 8 维能力权重对所有可用模型打分，Top-3 加权随机（90/7/3）
  │     选定 model → 查 providers 映射 → 改写 model 字段 + 切对应 base_url
  │
  ├── ④ 级联验证（仅当策略 cascade: true）
  │     模型作答 → 规则验证器 / LLM Judge 评估
  │     不通过 → 沿能力评分升一档重试（最多 max_retries 次）
  │
  └── ⑤ 成本记录   SQLite 写一行：策略命中、修饰器决策、最终模型、token、
                   费用、judge 反馈 —— 供 report / optimize 命令分析

CLI 工具：
  policyflow report   → 全屏 TUI 仪表盘(成本/策略/模型/日趋势)
  policyflow classify → 测试路由("这句话会匹配到哪个策略?")
  policyflow optimize → AI 优化建议(分析日志,推荐新策略)
  policyflow export   → 导出 CSV 日志
```

## 快速开始

### 1. 安装

```bash
git clone https://github.com/Anakin7n/PolicyFlow.git
cd PolicyFlow
pip install -r requirements.txt
```

如果使用虚拟环境（推荐），先创建并激活：
```bash
python -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### 2. 配置

PolicyFlow 用两个文件分工：
- **`.env`** — 放 API Key（敏感信息，不入 git）
- **`policyflow.yaml`** — 放策略和 provider 配置，里面用 `${VAR_NAME}` 引用 `.env` 里的 Key

复制示例文件填入你自己的设置：

```bash
cp .env.example .env                # 填 API Key
cp policyflow.example.yaml policyflow.yaml   # 策略配置
```

两个文件都已加入 `.gitignore`，不会被提交到 git，放心填。

#### .env 怎么填

`.env` 是个简单的 `KEY=VALUE` 文件，每行一个 API Key。**变量名你自己起**——`policyflow.yaml` 用 `${VAR_NAME}` 语法引用即可：

```bash
# .env 示例
UPSTREAM_API_KEY=sk-your-main-key       # 必填：默认兜底 API
DEEPSEEK_API_KEY=sk-xxx                 # 想用 deepseek 时填
ANTHROPIC_API_KEY=sk-ant-xxx            # 想用 claude 时填
MY_COMPANY_TOKEN=internal-xxx           # 自己加的供应商，名字随便起
```

```yaml
# policyflow.yaml 中引用
providers:
  deepseek:
    api_key: "${DEEPSEEK_API_KEY}"      # ← 与 .env 里的变量名对应
  internal:
    api_key: "${MY_COMPANY_TOKEN}"      # ← 你自己起的名字也行
```

**唯一必填的是 `UPSTREAM_API_KEY`**——其他全是按需填（用哪个供应商就填哪个）。`.env.example` 里已经预设了主流供应商（DeepSeek、Qwen、GLM、Kimi、Doubao、Anthropic、OpenAI 等）的命名建议；要加新供应商就自己往 `.env` 添一行新变量名。

### policyflow.yaml 配置清单

打开 `policyflow.yaml`，按以下顺序改：

| 段落 | 作用 |
|------|------|
| `providers` | 配置你的模型供应商，每个供应商填 `base_url`、`api_key`（用 `${VAR}` 引用 `.env`）、模型列表。策略 `route_to` 的模型必须出现在某个 provider 里 |
| `upstream` | 最后兜底：模型找不到可用 provider 时（没声明、Key 无效、额度用完、挂了），请求发到这里并自动改写为 `fallback_model`。没配 `fallback_model` 则原样转发 |
| `embedding` | 语义匹配的 Embedding API 地址、模型、两个阈值（`similarity_threshold` 全局匹配 / `verify_threshold` 关键词复核）。不可用时自动降级 |
| `routing_mode` | 默认路由模式：`hybrid` / `capability` / `explicit`。启动菜单可临时覆盖 |
| `policies_hybrid` | hybrid 模式下的策略集，可混用写死模型和算法选模 |
| `policies_capability` | capability 模式下的策略集，全由算法选模 |
| `policies_explicit` | explicit 模式下的策略集，全写死模型 |
| `cascade` | 级联验证：验证方式、升级链条、最大重试次数。若启用 LLM 裁判，`judge_model` 必须已在 `providers` 中配了真实 Key |
| `cost_tiers` | `max_cost_tier` 的分档边界（可选，省略用默认 cheap<0.5、mid<1.7） |
| `modifiers` | 四个修饰器的开关 + `strongest_model` / `reasoning_model` 目标模型（详见下方"智能修饰器"节） |
| `optimizer` | AI 优化引擎：是否启用、用哪个模型分析、最多几条建议。所用模型必须已在 `providers` 中配了真实 Key |
| `logging` | `log_prompt_preview` 是否记录提问原文（默认 `true`，利于优化建议；隐私敏感设 `false`，详见"AI 优化引擎"节） |

### Embedding 供应商配置

Embedding API 用于语义匹配（可选，不用也能跑）。默认配置是火山引擎豆包多模态 Embedding，换成其他供应商只需改 `policyflow.yaml` 中 `embedding` 段的三项：

```yaml
# policyflow.yaml 中的 embedding 段（按需改 base_url + model）
embedding:
  base_url: https://ark.cn-beijing.volces.com/api/v3            # ← 改这里
  api_key: "${EMBEDDING_API_KEY}"                                # ← .env 里填对应 key
  model: doubao-embedding-vision-251215                          # ← 改这里
  similarity_threshold: 0.25   # 全局语义匹配阈值
  verify_threshold: 0.25       # 关键词命中后复核阈值（避免歧义命中）
  timeout: 30
```

> ⚠️ **阈值要跟着 embedding 模型的尺度走**：不同模型的余弦相似度分布差异很大——OpenAI text-embedding 系相关文本常达 0.5~0.8，而 doubao-embedding-vision 这类即使强相关也只有 0.25~0.4。默认值 0.25 是按 doubao 校准的；如果你换成 OpenAI 等模型，需相应调高（如 0.5），否则会出现大量误命中。判断方法：用 `policyflow classify "<典型问题>"` 看相似度落在什么量级。

> Embedding API 不填会怎样？路由自动降级：跳过关键词复核与全局语义匹配，仅用关键词精确匹配 + 默认策略。不影响服务运行。

支持的 api_key 格式：直接写字符串或 `${ENV_VAR}` 引用环境变量。

#### 添加自定义供应商和模型

PolicyFlow 的 `providers` 段对**供应商数量、模型命名风格无任何限制**——任何 OpenAI 兼容的 API 都能接入（Mistral、Groq、Together AI、Ollama 等）。

> 所有 Key 都在 `.env` 里填，`policyflow.yaml` 通过 `${VAR_NAME}` 引用——变量名你自己起。详见前面 [配置节](#2-配置)。

**场景 1：给现有供应商加新模型**

供应商出了新模型，直接加进 `models` 列表：

```yaml
providers:
  deepseek:
    base_url: https://api.deepseek.com
    api_key: "${DEEPSEEK_API_KEY}"
    models:
      - "deepseek-v4-flash"
      - "deepseek-v4-pro"
      - "deepseek-v5"            # ← 新加的，立刻可用
```

**场景 2：加新供应商**

任何 OpenAI 兼容 API 都能接入，三步走。

第一步：在 `providers` 下面加一段。以接入 **Anthropic Claude** 为例：

```yaml
# policyflow.yaml
providers:
  anthropic:
    base_url: https://api.anthropic.com
    api_key: "${ANTHROPIC_API_KEY}"
    models:
      - "claude-haiku-4-5"
      - "claude-sonnet-4-6"
      - "claude-opus-4-8"
```

第二步：在 `.env` 里填实际 Key：

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-your-real-key
```

第三步：在策略里引用新模型：

```yaml
- name: "复杂推理与分析"
  match:
    keywords: ["架构设计", "数学证明", "策略分析"]
  route_to: "claude-opus-4-8"      # ← 新加的，立刻可用
```

**其他常见供应商**（同样 OpenAI 兼容，照上面三步替换即可）：

| 供应商 | base_url |
|---|---|
| Mistral | `https://api.mistral.ai/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Together AI | `https://api.together.xyz/v1` |
| 本地 Ollama | `http://localhost:11434/v1`（key 填 `"ollama"` 即可） |

**完整支持需要的 4 处改动**

加 `providers` 这一处只是让模型**能路由**——但要让 PolicyFlow 的智能选模、成本统计、自动窗口升级真正认识这个新模型，需要补全 4 处：

| 步骤 | 文件 | 加什么 | 不加的代价 |
|---|---|---|---|
| 1 | `policyflow.yaml` 的 `providers` 段 | 模型 ID 加进 `models` 列表 | **必填**——不加根本路由不到 |
| 2 | [policyflow/cost.py](policyflow/cost.py) 的 `MODEL_PRICES` | `"模型ID": (input价, output价)` | 仪表盘成本按 fallback `$1/M` 估算，**金额不准** |
| 3 | [policyflow/model_profiles.py](policyflow/model_profiles.py) 的 `PROFILES` | 8 维能力评分 + 价格 + 上下文窗口 | **能力路由失效**——不写 `route_to` 的策略选不到这个模型 |
| 4 | [policyflow/modifiers.py](policyflow/modifiers.py) 的 `MODEL_WINDOWS` | `"模型ID": 窗口大小` | 长 prompt 窗口超限时不会被自动升级到这个模型 |

**强烈建议四步全做。** 否则相当于把这个模型放进一个"哑路由"——只有写死 `route_to:` 才能用到，capability 模式和评分系统全部绕开它。如果你接入的是一个比内置模型更强或更便宜的新模型，第 3 步尤其重要——评分系统看不到它就永远不会选它，这等于浪费了 PolicyFlow 最有价值的功能。

第 3 步的 8 维评分需要主观判断（参考已有模型的相对水平），你可以从一个保守的起点开始，跑一段时间用 `policyflow optimize` 看实际表现再调。

### 3. 启动

```bash
# 如果用了虚拟环境，先激活
# .venv\Scripts\activate           # Windows
# source .venv/bin/activate        # Linux/Mac

# CLI 启动
python -m policyflow serve --host 0.0.0.0 --port 8000

# 或者直接用 uvicorn
python -m uvicorn policyflow.main:app --host 0.0.0.0 --port 8000
```

### 4. 使用

PolicyFlow 启动后同时暴露两个端点。接入就是填两个参数——**协议决定 URL，Key 随便写**：

| 协议 | URL | API Key | 哪些客户端用这个协议 |
|---|---|---|---|
| **OpenAI 兼容** | `http://localhost:8000/v1` | 任意字符串 | ChatBox、Cursor、Continue、OpenAI SDK、Codex CLI… |
| **Anthropic 原生** | `http://localhost:8000` | 任意字符串 | Claude Code、Claude SDK 等 Anthropic 原生客户端 |

> URL 为什么不一样？Anthropic 客户端自动拼 `/v1/messages`，所以填根路径；OpenAI 客户端自动拼 `/v1/chat/completions`，所以填 `/v1`。PolicyFlow 不校验 API Key——转发用的是 `.env` 里的供应商真实 Key。**两种协议走同一条路由管道**，Anthropic 请求在入口自动转格式，对用户透明。

**怎么设？** 两种形式，任选其一：

```bash
# 环境变量 — 当前终端里所有 agent 都受影响
export ANTHROPIC_BASE_URL="http://localhost:8000"    # Anthropic 协议
export ANTHROPIC_API_KEY="sk-anything"
export OPENAI_BASE_URL="http://localhost:8000/v1"    # OpenAI 协议
export OPENAI_API_KEY="sk-anything"
```

```json
// 配置文件 — 只影响这一个 agent，不影响其他
// ~/.claude/settings.json
{ "anthropicBaseURL": "http://localhost:8000", "apiKey": "sk-anything" }
```

**自己写代码** 同理，改 SDK 的 `base_url`：

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="sk-anything")
```

**这次请求实际会发生什么？**（按默认 `policyflow.example.yaml` 策略追踪）：

1. PolicyFlow 收到请求，扫 `messages` 内容
2. 「帮我**翻译**这段话」命中「翻译、摘要、格式化」策略的关键词 + token<800
3. 策略 `route_to: "claude-haiku-4-5"` → 把 `model` 字段从 `gpt-4o` 改写为 `claude-haiku-4-5`
4. 查 `providers.anthropic`，请求转发到 `https://api.anthropic.com`，用 `${ANTHROPIC_API_KEY}` 鉴权
5. 响应原样返回给客户端，**响应头里带路由信息**：`X-PolicyFlow-Policy: 翻译、摘要、格式化`、`X-PolicyFlow-Method: keyword_verified`

如果你改成 `content="帮我设计一个秒杀系统的架构"`——会命中「复杂推理与分析」策略 → 路由到 `claude-opus-4-8`。每条请求按内容动态选模型，对客户端代码零侵入。

### Docker Compose

```bash
cp .env.example .env
cp policyflow.example.yaml policyflow.yaml
# 编辑 .env + policyflow.yaml，填入你的设置
docker compose up -d
```

## CLI 命令

> 无需启动服务，直接运行即可。如果用了虚拟环境，先激活：`.venv\Scripts\activate`（Windows）或 `source .venv/bin/activate`（Linux/Mac）。

### report——全屏仪表盘

Textual 响应式 TUI 仪表盘，包含六个模块卡片，支持独立滚动：

- **Stats** — 总请求数/花费/节省/级联率
- **Policy Distribution** — 各策略花费占比柱状图
- **Model Usage by Provider** — 供应商→模型两级花费分解
- **Daily Cost Comparison** — 每日实际 vs 基准对比，含节省金额
- **Recent Requests** — 最近请求明细（可滚动）
- **AI Optimization** — 内嵌优化建议

```bash
python -m policyflow report
python -m policyflow report --since 7d
```

键盘操作：`Q` 退出，`R` 刷新，`Tab` 切换焦点，`↑↓`/滚轮在模块内滚动。

### classify——测试路由

```bash
python -m policyflow classify "帮我写一个排序算法"
```

### export——导出日志

```bash
python -m policyflow export --format csv --since 7d --output report.csv
python -m policyflow export --format json
```

### optimize——AI 优化建议

```bash
python -m policyflow optimize --since 30d
```

> 提示：Windows 用户可直接双击 `scripts\launcher.bat` 一键启动仪表盘或服务。

## 一键启动

```bash
scripts\launcher.bat    # 双击或命令行运行

  [1] Dashboard   全屏仪表盘
  [2] Serve       启动代理 (0.0.0.0:8000)
  [3] Classify    测试路由
  [Q] Quit
```

## 策略配置

策略在 `policyflow.yaml` 里定义。每条策略回答两个问题：**什么请求命中它**，以及**命中后用哪个模型**。

### 模型选择：两种方式，按策略混用

命中策略后，最终用哪个模型有两种模式，每一条策略独立选择：

**方式 A：你指定模型**

```yaml
- name: "代码生成"
  match:
    keywords: ["写代码", "SQL查询", "API接口"]       # ← 仅示意；完整列表见 example.yaml
  route_to: "claude-sonnet-4-6"                      # 命中后一定用这个模型
```

**方式 B：系统帮你选**

```yaml
- name: "代码生成"
  match:
    keywords: ["写代码", "SQL查询", "API接口"]
  # 不写 route_to → 系统按"代码生成"任务类型自动算分选最优模型
```

系统会根据你配的所有模型的 8 维能力数据 + 实时价格，自动挑出最适合这个任务且价格合理的模型。比如代码任务 DeepSeek V4 Pro 代码分 0.87、价格 $0.43/百万 token，Claude Sonnet 代码分 0.88 但价格 $15/百万 token——系统会选 DeepSeek，能力几乎一样但便宜 34 倍。

两种方式可以混用：重要的策略自己锁死模型，不重要的交给系统。

### 全局开关：一键切换所有策略

`policyflow.yaml` 顶部的 `routing_mode` 可以一键覆盖所有策略的模式，不用逐条改。也可用环境变量 `POLICYFLOW_ROUTING_MODE` 覆盖，重启生效。

| 模式 | 效果 |
|------|------|
| `hybrid`（默认） | 每条策略独立决定用方式 A 还是 B，互不干扰 |
| `explicit` | 所有策略强制方式 A——每一条都必须写 `route_to` |
| `capability` | 所有策略强制方式 B——不写 `route_to`，系统自动选 |

```yaml
# policyflow.yaml 顶部
routing_mode: hybrid
```

### 匹配方式：五种触发条件

`match` 字段支持的所有键（**多个条件同时满足才命中**，AND 逻辑）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `keywords` | string[] | 关键词列表，命中任一即算命中。先精确子串匹配（命中后做 Embedding 复核挡掉歧义），仍未命中再走 Embedding 全局语义匹配（阈值 0.25） |
| `max_input_tokens` | int | 输入 token **不超过**这个数才命中。配 `keywords` 用来防止长文被错归 |
| `min_input_tokens` | int | 输入 token **不小于**这个数才命中。用来过滤掉太短的请求 |
| `has_image` | bool | 请求含图片才命中（多模态请求） |
| `default` | bool | 兜底标记，前面都没命中走我（每个策略集必须有且只能有一条） |

### 关键词匹配 + Embedding 复核

关键词匹配是大小写不敏感的子串匹配（OR 逻辑：数组里任一关键词出现在 prompt 里即命中）。

**关键词命中后会做一次 Embedding 复核**——把 prompt 跟该策略的关键词集合算余弦相似度，低于 `verify_threshold`（默认 0.25）则视为误命中、撤销并继续往下走 Embedding 全局匹配。这是为了挡掉「"苹果"关键词误命中"苹果手机坏了"」这种歧义场景。

```yaml
policies:
  - name: "翻译、摘要、格式化"
    match:
      keywords: ["翻译", "摘要", "润色", "纠错", "格式化"]
      max_input_tokens: 800    # 可选：限制输入长度
    route_to: "deepseek-v4-flash"
    cascade: true               # 启用级联验证
```

**复核阈值在 `embedding.verify_threshold` 配置**（默认 0.25），调高 → 关键词更容易被推翻，调低 → 关键词更被信任。Embedding API 不可达时跳过复核、直接信任关键词命中（降级路径）。

### 图片检测

```yaml
  - name: "图片理解"
    match:
      has_image: true
    route_to: "gpt-4o"
```

### Embedding 全局语义匹配（关键词都没命中时的兜底）

如果关键词阶段没命中（或被复核推翻），路由器会用 prompt 跟所有策略的关键词集合做余弦相似度比较，挑相似度最高的策略——前提是相似度 ≥ `similarity_threshold`（默认 0.25）。

```yaml
# embedding 段配置阈值
embedding:
  similarity_threshold: 0.25   # 全局匹配阈值
  verify_threshold: 0.25       # 关键词命中后的复核阈值
```

Embedding API 不可用时此阶段自动跳过，请求落到默认策略。这是 PolicyFlow 设计的**降级路径**之一。

### 默认路由（兜底策略）

每个策略集**必须有且只能有一条** `default: true` 的策略。前面所有规则（图片检测、关键词匹配、Embedding 语义匹配）都没命中时，路由器交给这条处理。

```yaml
  - name: "默认"
    match:
      default: true                  # ← 标记为兜底，不参与主动匹配
    route_to: "deepseek-v4-pro"      # ← 真正命中默认时用什么模型
```

**最佳实践：让默认尽可能少被命中。** 默认是兜底而不是主力——多写几条策略覆盖常见场景（闲聊、概念问答、邮件草稿…），让请求精确路由到便宜模型，比让所有"漏网"请求都流向默认更省钱。示例 yaml 的「日常闲聊与简单问答」策略就是这个思路：用关键词 + token 上限拦住短问句，分流到 `claude-haiku-4-5`。

`default: true` 的策略不参与任何主动匹配——即使你写了 `keywords` 或 `has_image` 也会被忽略。它只在所有其他策略都没命中时被启用。

### 能力感知路由（智能选模）

不写 `route_to`，系统自己选。根据识别出的**任务类型**，用对应的 8 维评分权重对所有可用模型打分——**只从填了真实 Key 的供应商中挑**，未填 Key 的不参选。13 种任务类型的权重内置在 [model_profiles.py](policyflow/model_profiles.py) 里：

```yaml
  - name: "代码生成"
    match:
      keywords: ["写代码", "SQL查询", "API接口"]
    max_cost_tier: mid                               # 可选：限制预算
```

**8 维能力评分的依据**：
- 每个模型在 8 个维度上有 0-1 分值：代码（HumanEval/SWE-bench）、数学（MATH）、推理（MMLU/GPQA）、写作（MT-Bench）、多语言（SuperCLUE/C-Eval）、视觉（MMMU）、指令遵循（IFEval）、Agent 能力（BFCL/ToolBench）
- 数据来源：官方模型卡、Chatbot Arena、SuperCLUE 等公开榜单（2026-06）；无基准的模型取同系列已知模型的保守估算
- **不需要改代码**——觉得某个模型的某项评分不准？直接编辑 [model_profiles.py](policyflow/model_profiles.py) 里对应数字，重启生效

**评分公式**：综合分 = 能力分 × 80% + 价格分 × 20%。价格分是 log 归一化后的成本效率（便宜 → 高分）。

**不是取最高分，而是 Top-3 加权随机（90/7/3）**：评分前三的模型按 90% / 7% / 3% 的权重随机分流——#1 是绝对主力，同时给 #2、#3 少量流量做容灾预热和额度平滑，避免"唯一最佳模型"一挂全挂。

### 价格分档（max_cost_tier 工作原理）

模型按**加权平均价**（USD/百万 token）划分三档。加权公式 `(input × 3 + output) / 4`——按真实场景里 input:output ≈ 3:1 的比例计算（chat/RAG/agent 通常长 prompt 短回答），比简单算术平均更贴近实际成本。

**`max_cost_tier` 是价格上限（≤），不是"仅此档"**：

| 设定 | 可选模型范围 |
|---|---|
| `cheap` | 只在 cheap 档里选 |
| `mid` | cheap + mid（≤ mid 的都可选，能力够就优先便宜的） |
| `expensive` | 全池放开（≤ expensive = 不设上限，纯按评分选） |

也就是说：上限设得越高，可选范围越大，且永远在范围内优先性价比。给难任务设 `expensive` = 让评分在全部模型里自由挑，而不是强制用最贵的。

默认分档（可在 `policyflow.yaml` 的 `cost_tiers` 段覆盖，下方是按国产模型池校准后的值）：

```yaml
# policyflow.yaml（可选，省略则用代码内置默认 cheap_max=0.5 / mid_max=1.7）
cost_tiers:
  cheap_max: 0.5       # 加权均价 < 此值算 cheap
  mid_max:   1.7       # < 此值算 mid（含 cheap），≥ 此值算 expensive
```

想让某模型进更低档？查它的加权均价，把对应 `_max` 调到其上即可。

> ⚠️ **价格数据时效性**：[policyflow/cost.py](policyflow/cost.py) 内置的价格表收集于 **2026-06**，覆盖 39 个常用模型，已尽可能贴近各供应商当前官方报价——但 LLM 价格波动大，且部分国产模型 ID 为前瞻性命名，**实际数字可能与最新官方报价存在偏差**。
>
> **如需调整**：直接编辑 [policyflow/cost.py](policyflow/cost.py) 里 `MODEL_PRICES` 字典对应的元组（`(input_price, output_price)`，单位 USD/百万 token）。生产环境请按各供应商最新报价核对，不要直接基于本仓库的费用报告做计费决策。

## 多供应商路由

PolicyFlow 支持将不同模型路由到不同的 API 供应商。无需额外的 API 集成 — DeepSeek、Qwen、Anthropic 都支持 OpenAI 兼容格式。

```
策略匹配 → 路由到 "qwen-max"
    │
    ▼
查 providers：qwen-max 属于 qwen 分组
  → base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  → api_key: 从环境变量取 ${QWEN_API_KEY}
    │
    ▼
改写请求：model 字段改为 "qwen-max"，请求发往阿里云
```

不修改上游服务的任何代码，PolicyFlow 只改两样东西：`model` 字段 + 目标 `base_url`。

### 供应商容灾（自动 fallback）

同一个模型可以同时列在多个 provider 里——**yaml 排列顺序就是优先级**。排前面的供应商先被调用；当它返回配额耗尽（402）、限流（429）、服务不可用（5xx）或连接超时等暂时性错误时，PolicyFlow 自动尝试下一个供应商：

```yaml
providers:
  volc-coding:                    # ← 优先：通过 Coding Plan 调 glm-5.2
    base_url: https://ark.cn-beijing.volces.com/api/coding/v3
    api_key: "${VOLC_CODING_KEY}"
    models:
      - "glm-5.2"

  glm:                            # ← 备用：Coding Plan 额度用完/挂了时走智谱直连
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key: "${ZHIPU_API_KEY}"
    models:
      - "glm-5.2"
```

**不需要额外配置字段**——把同一个模型写在多个 provider 下即自动启用容灾。403（权限不足）和 400（请求格式错误）不会触发切换——换供应商也解决不了。401/402/429/5xx/连接超时均会触发切换。所有 provider 都失败时，最终 fallback 到 upstream，model 按 `upstream.fallback_model` 改写（如配了的话）。


## 智能修饰器

修饰器在策略匹配之前运行，命中即覆盖路由决策、跳过策略匹配。**全部是本地规则判断，不调任何 API，0 延迟 0 费用。**

| 修饰器 | 触发条件 | 动作 |
|--------|---------|------|
| **Agent 检测** | 请求带 `tools` 数组、`tool_calls`、`role=tool` 消息，或 system prompt 含 `you are an agent` 等标记 | 强制路由到 `strongest_model` |
| **推理检测** | prompt 命中 ≥2 个推理关键词（"证明"/"逐步思考"/"系统设计"/"安全审计"/"架构决策"…完整列表见 [policyflow/modifiers.py:127-133](policyflow/modifiers.py#L127-L133)） | 路由到 `reasoning_model` |
| **上下文窗口** | 估算 token 总数超过当前模型已知窗口 | 自动切到更大窗口模型 |
| **会话持久化** | 相同 `X-Session-ID` 的后续请求 | 复用首次选择的模型（默认 TTL 30 min） |

> Agent 检测看的是**客户端发来的 OpenAI 格式 payload 结构**，不是用户输入的文字。Cursor / Claude Code 这类 coding agent 即使转发的是"今天天气怎样"，请求里也带着 `tools=[bash, edit, ...]`，照样命中。

### 配置（`policyflow.yaml` 的 `modifiers` 段）

```yaml
modifiers:
  agent_detection: true
  reasoning_detection: true
  context_window_filter: true
  session_persistence: true
  session_ttl: 1800                    # 会话保持 TTL，单位秒
  strongest_model: "claude-opus-4-8"   # Agent 检测命中后的目标
  reasoning_model: "deepseek-r1"       # 推理检测命中后的目标
```

`strongest_model` 和 `reasoning_model` 接受三种值：

| 写法 | 行为 |
|---|---|
| 具体模型名（如 `claude-opus-4-8` / `deepseek-r1` / `o3-mini`） | 直接用。如果该模型不在任何 provider 的 `models` 里，自动降级到 auto |
| `"auto"` | 从所有可用模型里按"Agent 工具调用"或"逻辑分析"任务的 8 维评分自动挑性价比最优 |
| 省略不写 | 默认 `"auto"` —— 走能力评分自动挑 |

适合做 reasoning 的候选：`claude-opus-4-8`（最强）、`deepseek-r1`（性价比之王）、`o3-mini`、`qwen-max`、`glm-5.2`。两个目标模型可以分开配——比如 Agent 用 opus（工具调用稳）、reasoning 用 deepseek-r1（推理强且便宜）。

## 级联验证

> 级联验证的设计理念源于 [NadirClaw](https://github.com/nadirclaw/nadirclaw)：回答发出后先验证质量，不通过则换更强的模型重试。PolicyFlow 在此基础上把容灾拆为三层独立机制，升级不再依赖静态链条，改为按能力评分逐档升。

PolicyFlow 有三层独立的容灾/升级机制，各司其职：

| 机制 | 触发条件 | 做什么 | 控制方 |
|---|---|---|---|
| **Provider 容灾** | 当前模型的某个供应商挂了 | 同一模型换下一个供应商 | 永远生效，无需配置 |
| **模型容灾** | capability 选中的模型所有供应商全挂 | 换综合评分 Top-2 模型 | 仅 capability 模式自动生效 |
| **质量级联** | 回答质量不达标（规则或 AI 判定） | 换纯能力评分更高一档的模型 | 策略级 `cascade: true` 控制 |

> route_to 的模型走 Provider 容灾 → upstream.fallback_model → 502

两档验证器：

**第一档：规则验证（默认，零成本）**
1. 拒绝检测：回答含 "I cannot"、"无法" 等
2. 截断检测：回答未正常结尾
3. 空答检测：回答过短 (< 10 字符)
4. JSON 检测：要求 JSON 但输出不合法

**第二档：LLM-as-Judge（可选）**

用便宜模型当裁判，深度检查回答质量——完整性、正确性、格式、幻觉。YAML 配置：

```yaml
cascade:
  enabled: true
  verifier: rule_then_llm     # rule_only | llm_judge | rule_then_llm
  judge_model: deepseek-v4-flash
  max_retries: 2              # 最多升级几次
  escalation_chain:           # 静态升级链（兜底用，见下）
    - "deepseek-v4-flash"
    - "deepseek-v4-pro"
    - "deepseek-r1"
```

> **`judge_model` 的 API Key 从哪来？** 从 `providers` 段自动查找（和路由请求走同一套 provider 容灾逻辑）。若 `judge_model` 不在任何 provider 里、或配了空 Key，会自动降级为 `upstream.fallback_model` 并用 `upstream` 的 Key 调用——裁判不会报错，但实际用的模型和你指定的是两个。因此 `judge_model` 必须在 providers 里配好真实 Key。`optimizer.model` 同理。

### 升级到哪个模型？——按能力评分逐档升

验证不通过时，PolicyFlow **优先按模型能力评分**选下一个升级目标，而不是照搬静态链：

- **纯能力排序，不看价格**。日常路由会用 20% 的价格权重来兼顾省钱，但级联升级只在便宜模型已经失败后才发生——这时诉求是"把事做对"，不是省钱。所以升级阶段价格权重归零，只比谁更能胜任当前任务类型。
- **升一档，不直接拉满**。从"能力高于当前模型"的可用模型里，选评分最接近的**下一档**，逐步试探；配合 `max_retries` 可多次升级，避免一道小坎就动用最贵的旗舰。
- **和 capability 选模同源**。无论当前模型是策略写死的（`route_to`）还是系统自选的，升级都用同一套能力评分体系，不会出现"升级反而换到更弱模型"的情况。

> `escalation_chain` 退化为**兜底**：仅在极少数策略名无法映射到已知任务类型时，才回退到这条手写链。正常情况下能力评分覆盖所有标准策略名，你不需要刻意维护这条链。

Judge 失败原因会写入数据库，供 AI 优化引擎分析——不只是知道"升级率高"，还能知道"47% 是因为编造不存在的 API 参数"。

## AI 优化引擎

`policyflow optimize` 将日志数据喂给大模型，生成具体的策略优化建议：

- 发现未匹配请求的共性，建议新增策略
- 分析级联失败原因，建议拆分或调整策略
- 给出预计每月节省金额

```bash
$ python -m policyflow optimize --since 30d

  AI 优化建议 (分析最近 30d)
  ============================================================
  ┌─ 建议 1: 新增"日常闲聊"策略 (low risk)
  ├─ 说明: 发现 823 条未匹配请求是闲聊类...预计每月节省 $4.20
  └─ YAML 片段:
       - name: "日常闲聊与简单问答"
         match:
           keywords: ["天气", "笑话", "你好"]
         route_to: "deepseek-v4-flash"
  ...
  📊 汇总: 执行以上建议，预计每月节省 ¥29.00
```

> **`optimizer.model` 的 API Key 从哪来？** 和级联裁判一样的降级链路：找不到 provider → 自动改用 `upstream.fallback_model`。因此也必须在 providers 里配好真实 Key。

### 提问原文与隐私（`logging.log_prompt_preview`）

优化引擎要给出"未匹配请求该建什么策略"的精准建议，依赖请求原文。该行为由 `policyflow.yaml` 的 `logging` 段控制：

```yaml
logging:
  log_prompt_preview: true   # 默认开：存用户提问原文前 500 字
```

- **`true`（默认）** — 记录每条请求原文的前 500 字。优化引擎能看到"这 823 条未匹配请求都在问天气/闲聊"，从而给出可直接套用的策略建议；`report` 仪表盘的最近请求列表也能显示原文。
- **`false`** — 不存原文，只存原文的哈希（`prompt_hash`，始终记录）。**成本、策略、模型、省钱等所有统计照常不受影响**；唯独优化引擎只能知道"有多少条同类请求未匹配"，说不出它们具体在问什么，建议质量下降。

> 多用户、对外服务或合规敏感场景，建议设为 `false` 保护用户隐私。

## 响应头追踪

每次请求的响应头包含路由信息：

```
X-PolicyFlow-Policy: 翻译、摘要、格式化
X-PolicyFlow-Method: keyword_match
X-PolicyFlow-Score: 1.000
```

不查日志就能知道"为什么走了这个模型"。

## 成本计算

内置 39 个模型的官方定价（2026-06）。成本对比基准（baseline）= **当前可用模型中最贵的那个**——代表"假如不路由、把所有请求都丢给手头最强的模型"的花费，路由到任何更便宜的模型都体现为节省：

| 厂商 | 模型 |
|------|------|
| Anthropic | Haiku 4.5 / Sonnet 4.6 / Opus 4.7 / Opus 4.8 |
| OpenAI | GPT-4o / GPT-4o-mini / GPT-4 Turbo / GPT-3.5 Turbo / o1 / o3-mini |
| Google | Gemini 2.5 Flash / 2.5 Pro / 2.0 Flash / 3.5 Flash / 3.1 Pro |
| DeepSeek | V4 Pro / V4 Flash / V3 / R1 |
| 通义千问 | Qwen-Max / Plus / Flash / 3-235B-A22B / VL-Plus |
| 智谱 | GLM-5.2 / GLM-5 / GLM-5.1 |
| 月之暗面 | Kimi K2.6 |
| 字节豆包 | Doubao 1.6 / Seed 2.0 Lite |
| 百度文心 | ERNIE 5.1 / 4.5 Turbo / Speed Pro |
| MiniMax | M3 / M2.7 |

报告对比公式：`实际花费` vs `如果全用 baseline 模型的花费`。金额单位为人民币（¥）。

**baseline 怎么定？** 纯按成本、与路由逻辑无关：自动取**可用模型中加权均价最贵的那个**（从 `available_models` 筛选，只含配了真实 Key 的供应商）；若没有任何可用模型，兜底 `deepseek-v4-pro`。代表"假如不路由、全用手头最强模型"的花费，所以路由到任何更便宜的模型都体现为节省，hybrid / capability / explicit 三种模式通用。

## 项目结构

```
PolicyFlow/
├── policyflow/
│   ├── __init__.py       # 包入口
│   ├── __main__.py       # python -m policyflow 入口
│   ├── main.py           # FastAPI 入口
│   ├── config.py         # YAML 配置加载 + provider 解析
│   ├── models.py         # OpenAI 兼容数据模型
│   ├── proxy.py          # 上游转发代理（多 provider client + 供应商容灾）
│   ├── anthropic_adapter.py # Anthropic Messages API ↔ OpenAI 协议适配
│   ├── policy.py         # 策略数据模型
│   ├── classifier.py     # Embedding 分类器（含关键词复核）
│   ├── router.py         # 路由决策引擎
│   ├── modifiers.py      # 4 个智能修饰器（Agent/推理/会话/窗口）
│   ├── cascade.py        # 级联验证器 + LLM-as-Judge
│   ├── db.py             # SQLite 日志层
│   ├── cost.py           # 39 个模型定价
│   ├── model_profiles.py # 模型能力评分（8维）+ 智能选模
│   ├── optimizer.py      # AI 优化建议引擎
│   ├── dashboard_tui.py  # 全屏 TUI 仪表盘（Textual）
│   └── cli.py            # CLI 命令（serve/report/classify/export/optimize）
├── examples/
│   ├── policyflow-dev.yaml     # 开发场景 — explicit 模式，编程为主
│   ├── policyflow-zh.yaml      # 中文办公 — explicit 模式，翻译/文档为主
│   └── policyflow-hybrid.yaml  # 混合模式 — 关键任务锁死，其余系统自动选
│   └── launcher.bat       # Windows 一键启动菜单
├── policyflow.example.yaml # 默认配置模板
├── pyproject.toml          # 打包配置
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## License

MIT
