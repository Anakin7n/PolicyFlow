# CLAUDE.md

完整的项目介绍、架构设计和 API 参考见 [README.md](README.md)。

## 项目简介

PolicyFlow 是一个面向 LLM API 的策略路由中间件。它部署在 one-api（或任何 OpenAI 兼容网关）前面，对进入的请求做意图分类，改写 model 字段，使简单任务走便宜模型、复杂任务走高端模型。技术栈：FastAPI + SQLite + Chart.js。

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

- **单包结构** — 所有模块在 `policyflow/` 下，不拆子包。项目 ~2000 行，拆包属于过度设计。
- **外部依赖降级** — 每个外部依赖（Embedding API、上游、SQLite）都有降级路径。即使 Embedding API 挂了，关键词匹配仍能正常工作。
- **配置优于代码** — 所有路由行为通过 YAML 控制，用户不需要改 Python 代码就能调整路由规则。
- **响应头追踪** — 每个请求返回 `X-PolicyFlow-*` 响应头，包含路由决策信息，不需要翻日志就能知道"为什么走了这个模型"。
