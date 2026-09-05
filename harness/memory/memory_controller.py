"""记忆控制器：根据 query 类型决定记忆的读/写策略。

导入 router.py 的标记常量，不再重复定义。
"""

import re

from harness.orchestration.multi.router import _META_QUESTION_RE, is_chitchat_query

# 记忆正文若本身是元问答，注入时跳过——否则「最近一条」常是污染过的元问答。
# 也覆盖「这次对话第一句是什么」等自指问题。
_META_MEMORY_RE = re.compile(
    r"^问:\s*.{0,40}("
    r"(刚才|上次|上条|上轮|之前|上一个|上一条|上一轮).{0,8}(问了|查了|问题|查询|语句)|"
    r"(问了什么|查了什么|聊了什么|做过什么|查过什么|问过什么|还记得)|"
    r"(第一句|最初的?问题|最开始|最初一句)|"
    r"这[次轮场]对话"
    r")"
)


def is_chitchat(query: str) -> bool:
    """闲聊跳过向量召回，避免无意义 embedding。"""
    return is_chitchat_query(query)


def is_meta_question(query: str) -> bool:
    """元问题——问对话历史本身而非业务数据。"""
    return bool(_META_QUESTION_RE.search((query or "").strip()))


def is_meta_memory(text: str) -> bool:
    """记忆是否为元问答（不应再当作「上一个业务问题」）。"""
    return bool(_META_MEMORY_RE.search((text or "").strip()))


def should_vector_recall(query: str) -> bool:
    """闲聊/元问题跳过向量召回（元问题走 list_recent）。"""
    return not (is_chitchat(query) or is_meta_question(query))


def should_remember(query: str) -> bool:
    """元问题不写入向量库——防污染。"""
    return not is_meta_question(query)
