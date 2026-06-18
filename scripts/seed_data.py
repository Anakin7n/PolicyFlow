"""Generate realistic mock data for PolicyFlow dashboard demo.

Realistic request mix (office/dev team):
  ~40% 翻译/摘要  → mostly cheap flash models
  ~30% 通用问答   → cheap
  ~18% 代码生成   → mid-tier
  ~5%  图片理解   → vision models
  ~5%  复杂推理   → expensive
  ~2%  默认fallback

Model selection weighted, token sizes vary by task type.
"""

import sqlite3
import random
import hashlib
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path("policyflow.db")

# ── Realistic request profiles ──────────────────────────────────────

POLICIES = [
    {
        "name": "翻译、摘要、格式化",
        "method": "keyword_match",
        "weight": 42,
        "cascade_rate": 0.03,
        "models": [
            ("deepseek-v4-flash", 85),
            ("qwen3.5-flash", 15),
        ],
        "prompts": [
            "翻译成英文：今天天气真不错，适合出去玩", "帮我润色这封邮件",
            "把这段话总结成三句话", "帮我把JSON格式化一下",
            "翻译这篇技术文档的摘要部分", "纠错：他去了学校，我去了家",
            "扩写这段话：AI正在改变各个行业", "用更正式的语气改写这段话",
            "把这个中文摘要翻译成英文", "润色一下这篇周报",
            "帮我总结这段会议记录", "这段话有什么语法错误吗",
            "翻译成日文：谢谢你的帮助", "格式化这段XML",
            "把这篇英文新闻翻译成中文", "缩写这段话到100字以内",
        ],
        "tokens": {"prompt": (80, 600), "completion": (100, 1000)},
    },
    {
        "name": "通用问答",
        "method": "keyword_match",
        "weight": 30,
        "cascade_rate": 0.02,
        "models": [
            ("deepseek-v4-flash", 80),
            ("qwen3.5-flash", 20),
        ],
        "prompts": [
            "今天天气怎么样", "给我讲个笑话", "推荐几本好书",
            "怎么做红烧肉", "Python和Java哪个好", "帮我起个名字",
            "为什么天是蓝的", "给我写一首诗", "什么是机器学习",
            "推荐好看的电影", "怎么学好英语", "如何提高工作效率",
            "有什么好的健身建议", "Linux和Windows哪个好",
            "如何快速入睡", "帮我解释一下量子计算",
            "推荐一款好用的笔记软件", "怎么煮咖啡",
        ],
        "tokens": {"prompt": (50, 400), "completion": (80, 800)},
    },
    {
        "name": "代码生成与审查",
        "method": "keyword_match",
        "weight": 18,
        "cascade_rate": 0.08,
        "models": [
            ("deepseek-v4-flash", 50),
            ("deepseek-v4-pro", 45),
            ("claude-sonnet-4-6", 5),
        ],
        "prompts": [
            "写一个Python快速排序", "帮我debug这段代码",
            "审查这段SQL查询：SELECT * FROM users WHERE...",
            "写一个REST API接口", "重构这段代码",
            "写单元测试覆盖这个函数", "这个算法的时间复杂度是多少",
            "写一个正则表达式匹配邮箱", "帮我优化这个数据库查询",
            "用Go写一个并发的web scraper", "React组件怎么实现懒加载",
            "写一个Dockerfile部署Flask应用", "帮我解释这段Rust代码",
        ],
        "tokens": {"prompt": (150, 2000), "completion": (200, 3000)},
    },
    {
        "name": "图片理解",
        "method": "image_detect",
        "weight": 5,
        "cascade_rate": 0.02,
        "models": [
            ("qwen3-vl-plus", 85),           # cheaper vision
            ("gpt-4o", 15),                  # better vision, rare
        ],
        "prompts": [
            "这张图片里有什么内容", "识别图片中的文字",
            "分析这张图表的数据趋势", "描述这张照片的场景",
        ],
        "tokens": {"prompt": (100, 500), "completion": (100, 600)},
    },
    {
        "name": "复杂推理与分析",
        "method": "embedding_match",
        "weight": 1,
        "cascade_rate": 0.12,
        "models": [
            ("deepseek-v4-pro", 85),
            ("claude-sonnet-4-6", 15),
            ("claude-opus-4-8", 0),
        ],
        "prompts": [
            "设计一个分布式系统架构", "做技术选型分析",
            "分析微服务 vs 单体架构的tradeoff",
            "这个安全漏洞的影响范围有多大", "如何进行系统性能优化",
            "帮我证明这个数学定理", "分析这个系统的瓶颈在哪里",
        ],
        "tokens": {"prompt": (300, 3000), "completion": (300, 4000)},
    },
    {
        "name": "默认",
        "method": "default",
        "weight": 2,
        "cascade_rate": 0.01,
        "models": [
            ("deepseek-v4-flash", 80),
            ("deepseek-v4-pro", 20),
        ],
        "prompts": [
            "总结今天的新闻", "帮我写一个会议纪要",
            "解释区块链的原理", "什么是微服务架构",
            "为什么选择Go语言做后端", "如何做A/B测试",
        ],
        "tokens": {"prompt": (80, 600), "completion": (100, 1000)},
    },
]

USERS = ["zhangsan", "lisi", "wangwu", "dev-team", "ai-bot"]
JUDGE_REASONS = [
    "回答不完整，缺少关键步骤", "包含不存在的API参数",
    "格式不符合要求需要JSON", "回答过于简略",
    "存在事实错误", "代码有逻辑bug",
]

# Real prices per 1M tokens (input, output)
PRICES = {
    "deepseek-v4-flash":    (0.14, 0.28),
    "deepseek-v4-pro":      (0.435, 0.87),
    "qwen3.5-flash":        (0.10, 0.40),
    "qwen3-vl-plus":        (0.24, 1.92),
    "claude-haiku-4-5":     (1.00, 5.00),
    "claude-sonnet-4-6":    (3.00, 15.00),
    "claude-opus-4-8":      (5.00, 25.00),
    "gpt-4o":               (2.50, 10.00),
}

BASELINE = "deepseek-v4-pro"


def hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _pick_model(models: list[tuple[str, int]]) -> str:
    """Weighted random pick."""
    names, weights = zip(*models)
    return random.choices(names, weights=weights)[0]


def _cost(model: str, pt: int, ct: int) -> float:
    ip, op = PRICES.get(model, (0.50, 1.00))
    return (pt / 1_000_000) * ip + (ct / 1_000_000) * op


def seed(conn: sqlite3.Connection, n: int = 5000) -> None:
    now = datetime.now()
    rows = []

    for _ in range(n):
        pol = random.choices(POLICIES, weights=[p["weight"] for p in POLICIES])[0]
        model = _pick_model(pol["models"])
        prompt = random.choice(pol["prompts"])

        # spread over last 30 days, slightly more recent
        days_ago = int(abs(random.gauss(0, 10))) % 30
        ts = now - timedelta(days=days_ago, hours=random.randint(0, 23),
                             minutes=random.randint(0, 59))

        pt = random.randint(*pol["tokens"]["prompt"])
        ct = random.randint(*pol["tokens"]["completion"])
        cost = _cost(model, pt, ct)
        baseline = _cost(BASELINE, pt, ct)

        cascade = 1 if random.random() < pol["cascade_rate"] else 0
        if cascade:
            cascade += random.randint(0, 2)

        success = 0 if random.random() < 0.005 else 1
        jr = ""
        if cascade > 0 and random.random() < 0.3:
            jr = random.choice(JUDGE_REASONS)

        rows.append((
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            random.choice(USERS),
            random.choice(["gpt-4o", "deepseek-v4-flash"]),
            model,
            pol["name"],
            pol["method"],
            round(random.uniform(0.70, 1.0), 3),
            pt, ct, pt + ct,
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

    # Print summary
    from policyflow import db
    s = db.query_summary(30)
    print(f"Inserted {n} requests")
    print(f"Actual: ¥{s['total_cost']:.2f}  Baseline: ¥{s['compared_cost']:.2f}  "
          f"Saved: ¥{s['saved_amount']:.2f} ({s['saved_pct']}%)")


if __name__ == "__main__":
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    from policyflow.db import init_db
    init_db()
    conn.execute("DELETE FROM requests"); conn.commit()
    seed(conn, n=5000)
    conn.close()
