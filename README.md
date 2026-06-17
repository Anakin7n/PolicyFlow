# PolicyFlow

> 给 one-api 装上「什么请求用什么模型」的大脑，同时让你看到省了多少钱。

PolicyFlow 是一个部署在 [one-api](https://github.com/songquanpeng/one-api) 前面的策略路由中间件。借鉴 [NadirClaw](https://github.com/nadirclaw/nadirclaw) 的级联验证思路，让管理员通过 YAML 定义路由策略，自动将简单请求导向便宜模型、复杂请求保留高级模型，并提供成本分析 Dashboard。

## 核心能力

```
用户请求 (model: "gpt-4o")
  │
  ├── ① 智能修饰器 ── Agent检测 / 推理检测 / 会话保持 / 上下文窗口
  ├── ② 策略匹配 ──── 关键词精确匹配 + Embedding 语义匹配
  ├── ③ 路由决策 ──── 改写 model → 转发上游
  ├── ④ 级联验证 ──── 便宜模型不行？自动升级到更强模型
  ├── ⑤ 成本记录 ──── SQLite 记录每次路由决策和实际花费
  └── ⑥ Dashboard ──  /dashboard 查看成本分析和优化建议
```

## 快速开始

### 1. 克隆

```bash
git clone https://github.com/Anakin7n/PolicyFlow.git
cd PolicyFlow
```

### 2. 配置

编辑 `policyflow.yaml`，设置上游地址（one-api 或其他 OpenAI 兼容 API）：

```yaml
upstream:
  base_url: http://localhost:3000   # one-api 地址
  api_key: "sk-xxx"                  # one-api API Key
```

### 3. 启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动
python -m uvicorn policyflow.main:app --host 0.0.0.0 --port 8000
```

打开浏览器：
- **Dashboard**: `http://localhost:8000/dashboard`
- **API Docs**: `http://localhost:8000/docs`

### 4. 使用

把客户端的 API base URL 从 `http://localhost:3000` 改为 `http://localhost:8000`，请求会自动按策略路由。

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",  # 指向 PolicyFlow
    api_key="sk-xxx",
)

# 这个请求会被自动路由到便宜的 qwen3.5-flash
response = client.chat.completions.create(
    model="gpt-4o",  # 客户端随便写，PolicyFlow 会改写
    messages=[{"role": "user", "content": "帮我翻译这段话：Hello World"}],
)
```

### Docker Compose（含 one-api）

```bash
cp .env.example .env
# 编辑 .env，设置 ONE_API_KEY

docker compose up -d
```

启动后：
- PolicyFlow: `http://localhost:8000`
- one-api: `http://localhost:3000`（首次需登录配置渠道）

## 策略配置

策略通过 `policyflow.yaml` 定义，支持 4 种匹配方式：

### 关键词匹配（即时生效，不需要 Embedding API）

```yaml
policies:
  - name: "翻译、摘要"
    match:
      keywords: ["翻译", "摘要", "改写", "润色"]
      max_input_tokens: 800    # 可选：限制输入长度
    route_to: "qwen3.5-flash"
    cascade: true               # 启用级联验证
```

### 图片检测

```yaml
  - name: "图片理解"
    match:
      has_image: true
    route_to: "qwen3-vl-plus"
```

### Embedding 语义匹配（需要 Embedding API）

```yaml
  - name: "代码生成与审查"
    match:
      keywords: ["写代码", "debug", "重构", "单元测试"]
    route_to: "claude-sonnet-4-6"
```

关键词会被 Embedding，与用户 prompt 计算余弦相似度，大于阈值 (0.75) 即命中。

### 默认路由

```yaml
  - name: "默认"
    match:
      default: true
    route_to: "deepseek-v4-flash"
```

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

```
Haiku 生成回答
  │
  ├── ✅ 通过验证（无拒绝、无截断、非空、JSON正确）→ 返回用户
  └── ❌ 验证失败 → 自动升级到 Sonnet → 重新生成
                     │
                     ├── ✅ 通过 → 返回用户
                     └── ❌ 失败 → 升级到 Opus
```

验证规则（纯启发式，~50 行代码）：
1. 拒绝检测：回答含 "I cannot"、"无法" 等
2. 截断检测：回答未正常结尾
3. 空答检测：回答过短 (< 10 字符)
4. JSON 检测：要求 JSON 但输出不合法

## Dashboard

访问 `http://localhost:8000/dashboard`：

- 📊 总览：总请求 / 总成本 / 节省金额 / 节省比例
- 📈 折线图：策略路由实际花费 vs 全用最强模型模拟花费
- 🎯 饼图：每个策略的成本占比
- 🔄 级联统计：便宜模型直接通过的比例
- 📋 最近请求列表
- 💡 自动优化建议

## 路由决策追踪

每次请求的响应头包含路由信息：

```
X-PolicyFlow-Policy: 翻译、摘要、格式化
X-PolicyFlow-Method: keyword_match
X-PolicyFlow-Score: 1.000
```

## 成本计算

内置 33 个模型的官方定价（2026-06），包括：

| 厂商 | 模型 |
|------|------|
| Anthropic | Haiku 4.5 / Sonnet 4.6 / Opus 4.7·4.8 |
| OpenAI | GPT-4o / GPT-4o-mini / o1 / o3-mini |
| Google | Gemini 2.5 Flash·Pro / 3.5 Flash / 3.1 Pro |
| DeepSeek | V4 Pro·Flash / V3 / R1 |
| 通义千问 | Qwen3.7-Max / 3-Plus / 3.5-Flash / VL |
| 智谱 | GLM-5.2 / 5 / 5.1 |
| 月之暗面 | Kimi K2.6 |
| 字节豆包 | Doubao 1.6 / Seed 2.0 Lite |
| 百度文心 | ERNIE 5.1 / 4.5 Turbo / Speed Pro |

Dashboard 自动对比「实际花费」vs「如果全用 Opus 的花费」，量化节省金额。

## 项目结构

```
PolicyFlow/
├── policyflow/
│   ├── main.py          # FastAPI 入口
│   ├── config.py        # YAML 配置加载
│   ├── models.py        # OpenAI 兼容数据模型
│   ├── proxy.py         # 上游转发代理
│   ├── policy.py        # 策略数据模型
│   ├── classifier.py    # Embedding 分类器
│   ├── router.py        # 路由决策引擎
│   ├── modifiers.py     # 6 个智能修饰器
│   ├── cascade.py       # 级联验证器
│   ├── db.py            # SQLite 日志层
│   ├── cost.py          # 33 个模型定价
│   └── dashboard.py     # Dashboard API + HTML 面板
├── examples/
│   ├── policyflow-zh.yaml   # 中文办公场景
│   └── policyflow-dev.yaml  # 开发场景
├── policyflow.yaml      # 默认配置
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## License

MIT
