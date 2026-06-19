# PolicyFlow

> 一个 OpenAI 兼容的策略路由代理，给 LLM API 调用装上「什么请求用什么模型」的大脑，省了多少看得见。

PolicyFlow 是一个独立的 OpenAI 兼容代理，借鉴 [NadirClaw](https://github.com/nadirclaw/nadirclaw) 的级联验证思路。管理员通过 YAML 定义路由策略，系统自动将简单请求导向便宜模型、复杂请求保留高级模型，并通过 CLI 提供成本分析和 AI 优化建议。

**核心差异化**：策略透明（YAML 可配）、多供应商直连、能力感知路由（自动选最适合的模型）、LLM-as-Judge 级联验证、AI 优化引擎、Textual 全屏仪表盘。

## 核心流程

```
你的客户端（Cursor / Claude Code / Codex CLI / Aider / ChatBox / OpenAI SDK 等）
  发请求过来 →
    OpenAI 兼容协议  → POST http://localhost:8000/v1/chat/completions
    Anthropic 原生协议 → POST http://localhost:8000/v1/messages   （命令行 agent 用这个）
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
  │     关键词精确匹配命中 → Embedding 复核语境（≥0.5 才放行，挡掉"苹果"匹"苹果手机"这类歧义）
  │     未命中 → Embedding 全局语义匹配（阈值 0.5）
  │     仍未命中 → 走 default 策略
  │     → 命中后确定任务类型（如"代码生成"、"复杂推理"）
  │
  ├── ③ 路由决策：根据任务类型选模型
  │     写了 route_to → 直接用
  │     没写 route_to → 按任务类型的 8 维权重对所有可用模型打分，选最高分
  │     选定 model → 查 providers 映射 → 改写 model 字段 + 切对应 base_url
  │
  ├── ④ 级联验证（仅当策略 cascade: true）
  │     便宜模型先答 → 规则验证器 / LLM Judge 评估
  │     不通过 → 沿 escalation_chain 升级重试（最多 max_retries 次）
  │
  └── ⑤ 成本记录   SQLite 写一行：策略命中、修饰器决策、最终模型、token、
                   费用、judge 反馈 —— 供 report / optimize 命令分析

CLI 工具：
  policyflow report   → 全屏 TUI 仪表盘
  policyflow classify → 测试路由
  policyflow optimize → AI 优化建议
  policyflow export   → 导出日志
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
| `cascade` | 级联验证：验证方式、升级链条、最大重试次数 |
| `cost_tiers` | `max_cost_tier` 的分档边界（可选，省略用默认 cheap<1.0、mid<5.0） |
| `modifiers` | 四个修饰器的开关 + `strongest_model` / `reasoning_model` 目标模型（详见下方"智能修饰器"节） |
| `optimizer` | AI 优化引擎：是否启用、用哪个模型分析、最多几条建议 |

### Embedding 供应商配置

Embedding API 用于语义匹配（可选，不用也能跑）。默认配置是阿里百炼，换成其他供应商只需改 `policyflow.yaml` 中 `embedding` 段的三项：

| 供应商 | `base_url` | `model` | 对应 `.env` Key |
|--------|-----------|---------|-----------------|
| 阿里百炼（默认） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `text-embedding-v4` | `DASHSCOPE_API_KEY` |
| OpenAI | `https://api.openai.com/v1` | `text-embedding-3-small` | `OPENAI_API_KEY` |
| DeepSeek | `https://api.deepseek.com` | _(待 DeepSeek 支持)_ | `DEEPSEEK_API_KEY` |

```yaml
# policyflow.yaml 中的 embedding 段（按需改 base_url + model）
embedding:
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1   # ← 改这里
  api_key: "${EMBEDDING_API_KEY}"                                # ← .env 里填对应 key
  model: text-embedding-v4                                       # ← 改这里
  similarity_threshold: 0.5   # 全局语义匹配阈值
  verify_threshold: 0.5        # 关键词命中后复核阈值（避免歧义命中）
  timeout: 30
```

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
| **Anthropic 原生** | `http://localhost:8000` | 任意字符串 | Claude Code、Aider、OpenHands… |

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
| `keywords` | string[] | 关键词列表，命中任一即算命中。先精确子串匹配（命中后做 Embedding 复核挡掉歧义），仍未命中再走 Embedding 全局语义匹配（阈值 0.5） |
| `max_input_tokens` | int | 输入 token **不超过**这个数才命中。配 `keywords` 用来防止长文被错归 |
| `min_input_tokens` | int | 输入 token **不小于**这个数才命中。用来过滤掉太短的请求 |
| `has_image` | bool | 请求含图片才命中（多模态请求） |
| `default` | bool | 兜底标记，前面都没命中走我（每个策略集必须有且只能有一条） |

### 关键词匹配 + Embedding 复核

关键词匹配是大小写不敏感的子串匹配（OR 逻辑：数组里任一关键词出现在 prompt 里即命中）。

**关键词命中后会做一次 Embedding 复核**——把 prompt 跟该策略的关键词集合算余弦相似度，低于 `verify_threshold`（默认 0.5）则视为误命中、撤销并继续往下走 Embedding 全局匹配。这是为了挡掉「"苹果"关键词误命中"苹果手机坏了"」这种歧义场景。

```yaml
policies:
  - name: "翻译、摘要、格式化"
    match:
      keywords: ["翻译", "摘要", "润色", "纠错", "格式化"]
      max_input_tokens: 800    # 可选：限制输入长度
    route_to: "deepseek-v4-flash"
    cascade: true               # 启用级联验证
```

**复核阈值在 `embedding.verify_threshold` 配置**（默认 0.5），调高 → 关键词更容易被推翻，调低 → 关键词更被信任。Embedding API 不可达时跳过复核、直接信任关键词命中（降级路径）。

### 图片检测

```yaml
  - name: "图片理解"
    match:
      has_image: true
    route_to: "gpt-4o"
```

### Embedding 全局语义匹配（关键词都没命中时的兜底）

如果关键词阶段没命中（或被复核推翻），路由器会用 prompt 跟所有策略的关键词集合做余弦相似度比较，挑相似度最高的策略——前提是相似度 ≥ `similarity_threshold`（默认 0.5）。

```yaml
# embedding 段配置阈值
embedding:
  similarity_threshold: 0.5   # 全局匹配阈值
  verify_threshold: 0.5        # 关键词命中后的复核阈值
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

不写 `route_to`，系统自己选。根据识别出的**任务类型**（如"代码生成"、"复杂推理"），用对应的 8 维评分权重（代码/数学/推理/写作/多语言/视觉/指令遵循/Agent）对所有可用模型打分——"代码审查"比"代码生成"更看重指令遵循，"复杂推理"比"日常闲聊"更看重逻辑和数学。13 种任务类型的权重内置在 [model_profiles.py](policyflow/model_profiles.py) 里：

```yaml
  - name: "代码生成"
    match:
      keywords: ["写代码", "SQL查询", "API接口"]
    max_cost_tier: mid                               # 可选：限制预算
```

### 价格分档（max_cost_tier 工作原理）

模型按**加权平均价**（USD/百万 token）划分三档。加权公式 `(input × 3 + output) / 4`——按真实场景里 input:output ≈ 3:1 的比例计算（chat/RAG/agent 通常长 prompt 短回答），比简单算术平均更贴近实际成本。

默认分档（可在 `policyflow.yaml` 的 `cost_tiers` 段覆盖）：

| 档位 | 加权平均价 | 典型模型（默认配置下） |
|---|---|---|
| `cheap` | < $1.0 | ernie-speed-pro · deepseek-v4-flash · qwen-flash · gpt-4o-mini · deepseek-v3 · deepseek-v4-pro · **deepseek-r1**（性价比推理王） |
| `mid` | $1.0 ~ $5.0 | qwen-plus · glm-5.x · kimi-k2.6 · o3-mini · **claude-haiku-4-5** · qwen-max · gpt-4o |
| `expensive` | ≥ $5.0 | claude-sonnet-4-6 · claude-opus-4-7 · claude-opus-4-8 |

```yaml
# policyflow.yaml 顶部（可选，省略则用默认）
cost_tiers:
  cheap_max: 1.0       # < 此值算 cheap
  mid_max:   5.0       # cheap_max ≤ 此值算 mid，≥ 此值算 expensive
```

想让 claude-haiku 进 cheap 档？把 `cheap_max` 改到 2.5 即可（haiku 加权均价约 2.0）。

> ⚠️ **价格数据时效性**：[policyflow/cost.py](policyflow/cost.py) 内置的价格表收集于 **2026-06**，覆盖 33 个常用模型，已尽可能贴近各供应商当前官方报价——但 LLM 价格波动大，且部分国产模型 ID 为前瞻性命名，**实际数字可能与最新官方报价存在偏差**。
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

借鉴 NadirClaw：分类器不需要完美，先让便宜模型试试，不行再换贵的。

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
  escalation_chain:
    - "deepseek-v4-flash"
    - "deepseek-v4-pro"
    - "claude-sonnet-4-6"
```

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

## 响应头追踪

每次请求的响应头包含路由信息：

```
X-PolicyFlow-Policy: 翻译、摘要、格式化
X-PolicyFlow-Method: keyword_match
X-PolicyFlow-Score: 1.000
```

不查日志就能知道"为什么走了这个模型"。

## 成本计算

内置 33 个模型的官方定价（2026-06），成本对比基准为 DeepSeek V4 Pro（国产热门、价格适中）：

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

报告对比公式：`实际花费` vs `如果全用 deepseek-v4-pro 的花费`。金额单位为人民币（¥）。

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
│   ├── cost.py           # 33 个模型定价
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
