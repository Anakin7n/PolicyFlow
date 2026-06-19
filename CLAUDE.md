# CLAUDE.md

完整的项目介绍、架构设计和 API 参考见 [README.md](README.md)。

## 项目简介

PolicyFlow 是一个独立的 OpenAI 兼容策略路由代理，不依赖任何第三方中间层。对进入的请求做意图分类，改写 model 字段并切换目标 API 端点，使简单任务走便宜模型、复杂任务走高端模型。技术栈：FastAPI + SQLite + CLI (argparse)。

核心模块：
- **main.py** — FastAPI 入口，完整的 modifiers → router → cascade → log 管道
- **config.py** — YAML 配置加载，多供应商解析，环境变量替代，routing_mode 全局开关
- **proxy.py** — 多 provider httpx 客户端池，按模型懒加载
- **router.py** — 四阶段路由决策（image → keyword → embedding → default）
- **cascade.py** — 规则验证器 + LLM-as-Judge（可选），借鉴 NadirClaw
- **modifiers.py** — Agent/推理/窗口/会话 四个预路由修饰器，含模型可用性降级
- **model_profiles.py** — 30+ 模型 8 维能力评分 + 12 种任务权重矩阵，智能选模
- **classifier.py** — Embedding 分类器，支持 OpenAI/豆包多模态两种 Embedding 格式
- **optimizer.py** — AI 优化引擎，分析日志生成策略改进建议
- **cli.py** — 5 个 CLI 命令（serve/report/classify/export/optimize）
- **db.py** — SQLite 日志，schema 迁移，成本查询

## 编码准则

以下准则来自 [Andrej Karpathy 对 LLM 编码常见错误的观察](https://x.com/karpathy/status/2015883857489522876)。

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 项目约定

- **单包结构** — 所有模块在 `policyflow/` 下，不拆子包。
- **外部依赖降级** — 每个外部依赖都有降级路径：Embedding API 挂了用关键词匹配；Judge 调用失败视为 PASS；Optimizer 调用失败返回空建议。
- **配置优于代码** — 所有路由行为通过 YAML 控制（策略、级联、修饰器、optimizer），用户不需要改 Python 代码。
- **响应头追踪** — 每个请求返回 `X-PolicyFlow-*` 响应头，包含路由决策信息。
- **多供应商透明** — providers 段配置模型→API 端点映射，上游无感知。
- **向后兼容** — upstream 段始终作为默认 fallback，旧 YAML 不加 providers 也能正常工作。
