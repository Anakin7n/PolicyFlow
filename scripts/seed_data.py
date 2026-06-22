"""Generate realistic mock data for PolicyFlow dashboard demo.

Mirrors the *real* routing pipeline as closely as a mock can:
  - Reads the active policy set + routing_mode from policyflow.yaml.
  - In capability mode, selects models via the real model_profiles scoring,
    restricted to Config.available_models (only providers with real keys) —
    exactly like the live router. Picks among the top-N by weighted-random so
    the data shows model variety instead of one deterministic winner.
  - Every request gets a hand-written, realistic prompt in its policy's domain,
    so prompt_preview / optimizer analysis look like real traffic.
  - Prices via the real cost.py table; baseline = the default policy target.
"""

import sqlite3
import random
import hashlib
import yaml
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "policyflow.db"
CONFIG_PATH = ROOT / "policyflow.yaml"

USERS = ["zhangsan", "lisi", "wangwu", "dev-team", "ai-bot"]
JUDGE_REASONS = [
    "回答不完整，缺少关键步骤", "包含不存在的API参数",
    "格式不符合要求需要JSON", "回答过于简略",
    "存在事实错误", "代码有逻辑bug",
]

# Per-policy traffic share for an office/dev team (by policy name).
WEIGHTS = {
    "翻译、摘要、格式化": 22, "日常闲聊与简单问答": 20, "知识问答与学习": 15,
    "代码生成": 10, "代码审查与调试": 8, "文本创作与写作": 8,
    "数据分析与处理": 5, "图片理解": 4,
    "复杂推理与分析": 3, "系统架构与设计": 2, "性能分析与调优": 2,
    "安全审计与分析": 1,
}

# Hand-written realistic prompts per policy — the actual requests a user
# would type. Keyed by policy name.
PROMPTS = {
    "翻译、摘要、格式化": [
        "把这段产品介绍翻译成英文，语气正式一点", "帮我把这封英文邮件译成中文",
        "这段话太啰嗦了，帮我总结成三句话", "把这篇技术文档的摘要翻译一下",
        "帮我润色这封求职邮件，显得专业些", "这段中文有几处语法错误，帮我校对改一下",
        "把这段 JSON 格式化一下方便看", "提炼一下这篇会议记录的要点",
        "中译英：我们计划在下季度推出新版本", "帮我把这段话改写得更口语化一点",
        "summarize this paragraph in two sentences", "整理一下这份排版混乱的列表",
    ],
    "日常闲聊与简单问答": [
        "你好，在吗", "今天天气怎么样", "给我讲个笑话吧",
        "推荐几本好看的科幻小说", "周末有什么放松的好建议",
        "谢谢你的帮助", "你能做什么呀", "晚安",
        "怎么样才能快速入睡", "随便聊聊吧最近有点无聊",
        "你是谁开发的", "中午吃什么好呢",
    ],
    "知识问答与学习": [
        "为什么天空是蓝色的", "解释一下什么是机器学习的过拟合",
        "量子计算的基本原理是什么", "HTTP 和 HTTPS 有什么区别",
        "讲讲区块链是怎么运作的", "TCP 三次握手的机制是什么",
        "想入门深度学习应该怎么学", "科普一下黑洞是怎么形成的",
        "Python 的 GIL 是什么原理", "解读一下相对论的核心思想",
        "数据库索引的工作原理是什么", "为什么会有闰年",
    ],
    "代码生成": [
        "写一个 Python 快速排序函数", "帮我写个正则表达式匹配邮箱",
        "用 Go 写一个并发的 web scraper", "写一个 REST API 接口处理用户登录",
        "写一段 shell 脚本批量重命名文件", "如何实现一个 LRU 缓存，给段代码",
        "写一个 SQL 查询统计每个用户的订单数", "帮我写个 React 懒加载组件",
        "写一个二分查找的实现", "用 bash 写个定时备份脚本",
    ],
    "代码审查与调试": [
        "帮我 debug 这段代码，运行报错了", "review 一下这段 SQL 有没有性能问题",
        "这个函数怎么重构更清晰", "帮我写单元测试覆盖这个方法",
        "这段代码抛了空指针异常，帮我看看", "fix bug：循环里的索引越界了",
        "这个 stacktrace 是什么原因导致的", "重构这段嵌套太深的 if 判断",
        "为什么我这个测试用例一直失败", "调试一下这段异步代码的死锁问题",
    ],
    "文本创作与写作": [
        "帮我写一篇公众号推文介绍新产品", "写个小红书种草文案，关于这款耳机",
        "起几个吸引人的标题给这篇博客", "帮我写一封正式的商务合作邮件",
        "写一首关于秋天的现代诗", "帮我润色简历里的项目经历描述",
        "写段产品广告语，突出性价比", "续写这个故事的结尾",
        "帮我写知乎回答，主题是远程办公", "写个朋友圈文案配旅行照片",
    ],
    "数据分析与处理": [
        "帮我分析这份销售数据的趋势", "用 pandas 对这个 DataFrame 做分组统计",
        "这份 CSV 有异常值，帮我找出来", "对比这两个季度的数据有什么变化",
        "帮我做个数据透视表汇总各地区销量", "这批数据怎么清洗去重",
        "分析一下用户留存率的下降原因", "用 Excel 公式算同比增长率",
        "把这些数据做成可视化图表", "groupby 之后怎么算每组的均值",
    ],
    "图片理解": [
        "这张图片里有什么内容", "识别一下图片中的文字",
        "分析这张图表反映的数据趋势", "描述一下这张照片的场景",
        "这张设计稿有什么可以改进的", "图里的流程图讲的是什么逻辑",
        "帮我看看这张截图报的什么错", "这张发票上的金额是多少",
    ],
    "复杂推理与分析": [
        "帮我证明这个数学不等式", "分析微服务和单体架构的 tradeoff",
        "做个技术选型的决策分析", "复盘一下这次线上事故的根因",
        "推导一下这个递归算法的时间复杂度", "评估这个商业方案的可行性",
        "权衡一下自研和采购第三方的利弊", "深度分析这个系统的瓶颈在哪",
    ],
    "系统架构与设计": [
        "帮我设计一个高并发的秒杀系统架构", "这个分布式系统怎么保证数据一致性",
        "设计一个支持千万用户的消息推送系统", "数据库该怎么分库分表",
        "微服务拆分的边界应该怎么定", "设计一个技术方案支撑实时数据同步",
        "高可用架构应该考虑哪些点", "给个 IM 系统的整体技术选型",
    ],
    "安全审计与分析": [
        "帮我做个威胁建模分析这个登录流程", "这段代码有没有 SQL 注入漏洞",
        "审计一下这个 OAuth 鉴权流程是否安全", "分析这个接口可能的渗透风险",
        "加密存储用户密码应该用什么方案", "这个授权逻辑有没有越权风险",
        "排查一下这个 XSS 漏洞", "评估这套系统的安全审计要点",
    ],
    "性能分析与调优": [
        "这个接口响应很慢，帮我分析瓶颈", "优化一下这条慢查询 SQL",
        "怎么给这个高延迟的服务加速", "分析一下内存占用过高的原因",
        "这段代码的性能瓶颈在哪", "数据库查询怎么调优",
        "帮我看看为什么 QPS 上不去", "如何减少这个页面的加载时间",
    ],
}

# Fallback prompts for any policy not in PROMPTS.
_FALLBACK_PROMPTS = ["帮我处理一下这个问题", "这个该怎么办", "给点建议"]


def hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_config():
    """Load Config + PolicyEngine, mirroring the live router setup."""
    from policyflow.config import Config
    from policyflow.policy import PolicyEngine
    cfg = Config(str(CONFIG_PATH))
    eng = PolicyEngine(cfg.policies_data, cfg.routing_mode)
    return cfg, eng


def _top_models(task: str, avail: list, cost_tier: str, thresholds: dict, n: int = 3):
    """Top-N (model_id, score) for a task, restricted to available models —
    same scoring as the live select_best_model, but returns several so the
    seed can weighted-random among them for realistic model variety."""
    from policyflow.model_profiles import (
        TASK_WEIGHTS, PROFILES, score_model, DEFAULT_COST_TIER_THRESHOLDS,
    )
    weights = TASK_WEIGHTS.get(task)
    if not weights:
        return []
    cands = [(m, PROFILES[m]) for m in avail if m in PROFILES]
    if cost_tier in ("cheap", "mid", "expensive"):
        th = thresholds or DEFAULT_COST_TIER_THRESHOLDS
        cmax = th.get("cheap_max", 0.5); mmax = th.get("mid_max", 1.7)
        if cost_tier == "cheap":
            cands = [(m, p) for m, p in cands if p.average_cost < cmax]
        elif cost_tier == "mid":
            cands = [(m, p) for m, p in cands if cmax <= p.average_cost < mmax]
        else:
            cands = [(m, p) for m, p in cands if p.average_cost >= mmax]
    if not cands:
        return []
    scored = sorted(((score_model(p, weights), m) for m, p in cands), reverse=True)
    return [(m, s) for s, m in scored[:n]]


def _weighted_pick(top: list):
    """Weighted-random pick from [(model, score), ...].

    Mirrors the router's 90/7/3 distribution: #1 is the primary workhorse,
    #2 and #3 get a small share for fault-tolerance and quota smoothing.
    """
    models = [m for m, _ in top]
    weights = [0.90, 0.07, 0.03][:len(top)]
    total = sum(weights)
    weights = [w / total for w in weights]
    return random.choices(models, weights=weights)[0]


def _token_ranges(policy_name: str):
    """Realistic prompt/completion token ranges by task type.

    Coding / architecture / audits are heavy (large codebases, long context).
    Translation / chat / formatting are light.  Knowledge / writing are mid.
    """
    heavy_tasks = {"代码生成", "代码审查与调试", "系统架构与设计",
                   "安全审计与分析", "复杂推理与分析"}
    mid_tasks   = {"数据分析与处理", "文本创作与写作", "知识问答与学习",
                   "性能分析与调优"}
    # light: 翻译/摘要/格式化, 日常闲聊与简单问答, 图片理解, 默认

    if policy_name in heavy_tasks:
        return (2000, 80000), (1000, 25000)
    if policy_name in mid_tasks:
        return (500, 20000), (300, 12000)
    # Light — short prompts, brief answers
    return (100, 3000), (100, 4000)


def _make_ts(now):
    """Generate a plausible timestamp within the last 30 days (skewed recent)."""
    days_ago = int(abs(random.gauss(0, 9))) % 30
    return now - timedelta(days=days_ago, hours=random.randint(0, 23),
                           minutes=random.randint(0, 59))


def _make_tokens(policy_name):
    """Return (prompt_tokens, completion_tokens) with realistic non-round values."""
    pr, cr = _token_ranges(policy_name)
    # Uniform distribution inside the range → avoids integer-multiple artifacts
    pt = random.randint(*pr) + random.randint(-73, 73)
    ct = random.randint(*cr) + random.randint(-41, 41)
    return max(pt, 4), max(ct, 1)


def seed(conn: sqlite3.Connection, n: int = 5243) -> None:
    from policyflow import cost as cost_mod

    cfg, eng = load_config()
    avail = list(cfg.available_models)
    thresholds = cfg.cost_tier_thresholds
    baseline_model = cfg.baseline_model
    pols = eng.policies
    weights = [WEIGHTS.get(p.name, 5) for p in pols]

    # Pre-compute each policy's task type + top-N model candidates once.
    plan = []
    for p in pols:
        task = p.name
        top = _top_models(task, avail, p.max_cost_tier or "", thresholds)
        fallback = p.route_to or baseline_model
        plan.append((p, task, top, fallback))

    now = datetime.now()
    rows = []

    # ── method distribution: ~85% Direct / ~12% Indirect / ~3% Failed ──
    DIRECT_METHODS  = ["keyword_match", "keyword_verified", "embedding_match"]
    DIRECT_WEIGHTS  = [55, 28, 17]  # keyword is most common
    INDIRECT_WEIGHT = 0.12
    FAILED_WEIGHT   = 0.03

    # Keep a small pool of session-carrying policies so Indirect requests
    # inherit a plausible policy_name (they "continue" a previous task).
    session_policy_stack: list[str] = []

    for i in range(n):
        roll = random.random()
        if roll < FAILED_WEIGHT:
            # Failed — no policy matched, fallback model
            p = None
            method = "fallback"
            score = round(random.uniform(0.18, 0.44), 3)
            model = baseline_model
            policy_name = "Failed"
            prompt = random.choice(_FALLBACK_PROMPTS)
            pr_tok, co_tok = random.randint(10, 200) + random.randint(-5, 5), random.randint(10, 300) + random.randint(-3, 3)

        elif roll < FAILED_WEIGHT + INDIRECT_WEIGHT:
            # Indirect (session_continuation) — continues a previous task
            method = "session_continuation"
            score = round(random.uniform(0.30, 0.52), 3)
            if session_policy_stack and random.random() < 0.8:
                policy_name = random.choice(session_policy_stack)
                p = next((pp for pp, _, _, _ in plan if pp.name == policy_name), None)
                if p is None:
                    p = random.choice(pols)
                    policy_name = p.name
            else:
                p = random.choice(pols)
                policy_name = p.name
            pr_tok, co_tok = _make_tokens(policy_name)
            idx = pols.index(p) if p else 0
            _, task, top, fallback = plan[idx]
            model = _weighted_pick(top) if top else fallback

        else:
            # Direct hit — normal routing
            idx = random.choices(range(len(pols)), weights=weights)[0]
            p, task, top, fallback = plan[idx]
            policy_name = p.name
            if cfg.routing_mode == "capability" and top:
                method = f"capability({task})"
            elif p.has_image:
                method = "image_match"
            else:
                method = random.choices(DIRECT_METHODS, weights=DIRECT_WEIGHTS)[0]

            model = _weighted_pick(top) if top else fallback
            pr_tok, co_tok = _make_tokens(p.name)
            score = 1.0 if method == "image_match" else round(random.uniform(0.58, 0.94), 3)

            # Push to session stack so Indirect requests can inherit it
            session_policy_stack.append(policy_name)
            if len(session_policy_stack) > 30:
                session_policy_stack.pop(0)

        cascade = 0
        if random.random() < 0.02:
            cascade = 1 + random.randint(0, 2)

        success = 0 if random.random() < 0.007 else 1
        jr = random.choice(JUDGE_REASONS) if cascade > 0 and random.random() < 0.3 else ""

        ts = _make_ts(now)
        prompt = random.choice(PROMPTS.get(policy_name, _FALLBACK_PROMPTS))
        cost = cost_mod.calc_cost(model, pr_tok, co_tok)
        baseline = cost_mod.calc_compared_cost(pr_tok, co_tok, baseline_model)

        rows.append((
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            random.choice(USERS),
            random.choice(["gpt-4o", "deepseek-v4-flash"]),
            model,
            policy_name,
            method,
            score,
            max(pr_tok, 1), max(co_tok, 1), max(pr_tok + co_tok, 2),
            round(cost, 6), round(baseline, 6),
            cascade, random.randint(150, 8000), success,
            hash_prompt(prompt), prompt[:500], jr,
        ))

    conn.executemany(
        """INSERT INTO requests
           (timestamp, user, original_model, routed_model, policy_name,
            method, similarity_score, prompt_tokens, completion_tokens,
            total_tokens, estimated_cost, compared_cost,
            cascade_attempts, duration_ms, success,
            prompt_hash, prompt_preview, judge_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()

    from policyflow import db
    s = db.query_summary(30)
    mq = db.query_match_quality(30)
    models_used = len({r[3] for r in rows})
    print(f"Inserted {n} requests across {len(pols)} policies, {models_used} distinct models")
    print(f"Mode: {cfg.routing_mode} | available models: {len(avail)}")
    print(f"Match: Direct={mq['Direct']} ({mq['Direct']/mq['total']*100:.0f}%)  "
          f"Indirect={mq['Indirect']} ({mq['Indirect']/mq['total']*100:.0f}%)  "
          f"Failed={mq['Failed']} ({mq['Failed']/mq['total']*100:.0f}%)")
    print(f"Actual: CNY {s['total_cost']:.2f}  Baseline: CNY {s['compared_cost']:.2f}  "
          f"Saved: CNY {s['saved_amount']:.2f} ({s['saved_pct']}%)")


if __name__ == "__main__":
    from policyflow import db
    db.DB_PATH = DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    db.init_db()
    conn.execute("DELETE FROM requests"); conn.commit()
    seed(conn)
    conn.close()
