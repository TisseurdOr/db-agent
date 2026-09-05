"""路由规则表 — 评测失败后自愈学到的精确匹配规则（query → agent 列表）。

闭环（tests/eval_improve.py）把「路由错」的失败用例反推成规则写进来，
route_override() 在最高优先级查这张表——它就是"过去失败的记忆"。

结构: {"<精确 query>": ["sql", "analysis"]}
空列表 [] 表示该 query 不查库（闲聊类），Router 直接走 done。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

RULES_FILE = Path(__file__).resolve().parent / "router_rules.json"


def load_rules() -> dict[str, list[str]]:
    """读规则表；文件不存在/损坏返回空 dict。每次读盘、不缓存——闭环脚本写完同进程复跑能立刻读到。"""
    try:
        if RULES_FILE.exists():
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): list(v) for k, v in data.items() if isinstance(v, list)}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def add_rule(query: str, agents: list[str]) -> None:
    """追加/覆盖一条规则，原子写回（临时文件 + rename，避免半写毁掉整表）。"""
    rules = load_rules()
    rules[query] = list(agents)
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RULES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, RULES_FILE)
