# PolicyFlow

> 一个 OpenAI 兼容的策略路由代理，给 LLM API 调用装上「什么请求用什么模型」的大脑，省了多少看得见。

PolicyFlow 是一个独立的 OpenAI 兼容代理，借鉴 [NadirClaw](https://github.com/nadirclaw/nadirclaw) 的级联验证思路。管理员通过 YAML 定义路由策略，系统自动将简单请求导向便宜模型、复杂请求保留高级模型，并通过 CLI 提供成本分析和 AI 优化建议。

**核心差异化**：策略透明（YAML 可配）、多供应商路由（不同模型走不同 API）、LLM-as-Judge 级联验证、AI 优化引擎。

## 核心流程

```
用户请求 (model: "gpt-4o")
  │
  ├── ① 智能修饰器 ── Agent检测 / 推理检测 / 会话保持 / 上下文窗口
  ├── ② 策略匹配 ──── 关键词精确匹配 + Embedding 语义匹配
  ├── ③ 路由决策 ──── 查 model→provider 映射，改写 model + 切换 base_url
  ├── ④ 级联验证 ──── 规则验证 + LLM-as-Judge（可选），失败自动升级
  └── ⑤ 成本记录 ──── SQLite 记录每次路由决策、花费、judge 反馈

CLI 工具：
  policyflow report   → 成本报告
  policyflow optimize → AI 优化建议
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

编辑 `policyflow.yaml`，配置上游 API 和策略：

```yaml
# 多供应商：不同模型可以走不同的 API 端点
providers:
  deepseek:
    base_url: https://api.deepseek.com
    api_key: "${DEEPSEEK_API_KEY}"
    models: ["deepseek-v4-flash", "deepseek-v4-pro"]

  anthropic:
    base_url: https://api.anthropic.com
    api_key: "${ANTHROPIC_API_KEY}"
    models: ["claude-sonnet-4-6", "claude-haiku-4-5"]

# 默认上游（未列在 providers 中的模型走这里）
upstream:
  base_url: http://localhost:3000
  api_key: "sk-xxx"
```

支持的 api_key 格式：直接写字符串或 `${ENV_VAR}` 引用环境变量。

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

把客户端的 API base URL 指向 PolicyFlow：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",  # 指向 PolicyFlow
    api_key="sk-xxx",
)

# 这个请求会被自动路由到便宜的 deepseek-v4-flash
response = client.chat.completions.create(
    model="gpt-4o",  # 客户端随便写，PolicyFlow 会改写
    messages=[{"role": "user", "content": "帮我翻译这段话：Hello World"}],
)
```

### Docker Compose

```bash
cp .env.example .env
# 编辑 .env，设置 UPSTREAM_BASE_URL 和 UPSTREAM_API_KEY
docker compose up -d
```

## CLI 命令

> 无需启动服务，直接运行即可。如果用了虚拟环境，先激活：`.venv\Scripts\activate`（Windows）或 `source .venv/bin/activate`（Linux/Mac）。

```bash
# 查看成本报告
python -m policyflow report
python -m policyflow report --since 7d
python -m policyflow report --by-model
python -m policyflow report --by-day

# 测试路由
python -m policyflow classify "帮我写一个排序算法"

# 导出日志
python -m policyflow export --format csv --since 7d --output report.csv
python -m policyflow export --format json

# AI 优化建议
python -m policyflow optimize --dry-run
```

## 策略配置

策略通过 `policyflow.yaml` 定义，支持 4 种匹配方式：

### 关键词匹配（即时生效，不需要 Embedding API）

```yaml
policies:
  - name: "翻译、摘要"
    match:
      keywords: ["翻译", "摘要", "改写", "润色"]
      max_input_tokens: 800    # 可选：限制输入长度
    route_to: "deepseek-v4-flash"
    cascade: true               # 启用级联验证
```

### 图片检测

```yaml
  - name: "图片理解"
    match:
      has_image: true
    route_to: "gpt-4o"
```

### Embedding 语义匹配（需要 Embedding API）

```yaml
  - name: "代码生成与审查"
    match:
      keywords: ["写代码", "debug", "重构", "单元测试"]
    route_to: "deepseek-v4-pro"
```

关键词会被 Embedding，与用户 prompt 计算余弦相似度，大于阈值 (0.75) 即命中。Embedding API 不可用时自动降级为关键词精确匹配。

### 默认路由

```yaml
  - name: "默认"
    match:
      default: true
    route_to: "deepseek-v4-pro"
```

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

## 智能修饰器

修饰器在策略匹配之前运行，可以覆盖路由决策：

| 修饰器 | 触发条件 | 动作 |
|--------|---------|------|
| **Agent 检测** | 请求包含 tools、tool-role 消息 | 强制路由到最强模型 |
| **推理检测** | prompt 含 2+ 推理标记词 | 路由到 reasoning 模型 |
| **上下文窗口** | token 估算超当前模型窗口 | 自动切到更大窗口模型 |
| **会话持久化** | 相同 `X-Session-ID` 的后续请求 | 复用首次选择的模型（TTL 30min） |

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
       - name: "日常闲聊"
         match:
           keywords: ["天气", "笑话", "你好"]
         route_to: "deepseek-v4-flash"
  ...
  📊 汇总: 执行以上建议，预计每月节省 $29.00
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
| 通义千问 | Qwen3.7-Max / 3-Plus / 3.5-Flash / 3-235B / VL-Plus |
| 智谱 | GLM-5.2 / GLM-5 / GLM-5.1 |
| 月之暗面 | Kimi K2.6 |
| 字节豆包 | Doubao 1.6 / Seed 2.0 Lite |
| 百度文心 | ERNIE 5.1 / 4.5 Turbo / Speed Pro |

报告对比公式：`实际花费` vs `如果全用 deepseek-v4-pro 的花费`。

## 项目结构

```
PolicyFlow/
├── policyflow/
│   ├── main.py          # FastAPI 入口
│   ├── config.py        # YAML 配置加载 + provider 解析
│   ├── models.py        # OpenAI 兼容数据模型
│   ├── proxy.py         # 上游转发代理（多 provider client）
│   ├── policy.py        # 策略数据模型
│   ├── classifier.py    # Embedding 分类器
│   ├── router.py        # 路由决策引擎
│   ├── modifiers.py     # 6 个智能修饰器
│   ├── cascade.py       # 级联验证器 + LLM-as-Judge
│   ├── db.py            # SQLite 日志层
│   ├── cost.py          # 33 个模型定价
│   ├── optimizer.py     # AI 优化建议引擎
│   ├── cli.py           # CLI 命令（serve/report/classify/export/optimize）
│   └── __main__.py      # python -m policyflow 入口
├── examples/
│   ├── policyflow-zh.yaml   # 中文办公场景
│   └── policyflow-dev.yaml  # 开发场景
├── policyflow.yaml      # 默认配置
├── pyproject.toml       # 打包配置
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## License

MIT
