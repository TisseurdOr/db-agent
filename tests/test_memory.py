"""Memory 模块单元测试。

覆盖 VectorMemory（向量记忆）和 ConversationManager（对话压缩）。

VectorMemory 测试需要 embedding API——用 requires_embedding marker 标记，
没有 EMBEDDING_API_KEY 时自动跳过（embedding 成本极低，~¥0.0007/1K tokens）。
ConversationManager 的压缩路径需要 LLM API——压缩只在超过 max_recent 时触发，
不超时不调 API，所以无需 mock 也能测核心逻辑。
"""

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

requires_embedding = pytest.mark.skipif(
    not os.getenv("EMBEDDING_API_KEY"),
    reason="需要 EMBEDDING_API_KEY 才能跑向量记忆测试",
)


# ─── VectorMemory 测试 ────────────────────────────────────────────


@pytest.fixture
def vector_mem():
    """每次测试用独立的 collection，避免数据污染。"""
    import uuid
    from memory.vector_store import VectorMemory
    col_name = f"test_memory_{uuid.uuid4().hex[:8]}"
    mem = VectorMemory(collection_name=col_name)
    yield mem
    # 清理：删掉测试 collection
    mem.client.delete_collection(col_name)


@requires_embedding
def test_vector_memory_empty_recall(vector_mem):
    """空库召回应返回空列表。"""
    results = vector_mem.recall("销售数据查询")
    assert results == []


@requires_embedding
def test_vector_memory_remember_and_count(vector_mem):
    """存一条 → count 变 1。"""
    vector_mem.remember(
        content="用户查询了 2026年7月各产品线的销售额。软件类380万最高。",
        memory_type="conversation",
    )
    assert vector_mem.count() == 1


@requires_embedding
def test_vector_memory_remember_and_recall_semantic(vector_mem):
    """课程核心测试：存不同主题的记忆，模糊查询能召回正确主题。

    存 3 条不同主题（销售额/员工/订单状态），
    用模糊 query "上次那个销售分析" 检索——应召回销售相关的那条。
    """
    vector_mem.remember(
        content="用户在 2026-07-10 查询了 Q2 各地区的订单金额分布。"
                "北京最高(120万)，上海次之(98万)，深圳第三(76万)。",
        memory_type="conversation",
    )
    vector_mem.remember(
        content="用户查询了各部门的在职员工人数。"
                "销售部45人，研发部120人，市场部30人，财务部15人。",
        memory_type="conversation",
    )
    vector_mem.remember(
        content="用户查询了 pending 状态的订单数量和总金额。"
                "pending 订单共 23 笔，合计 ¥156,000，平均客单价 ¥6,783。",
        memory_type="conversation",
    )

    # 模糊查询——跟任何一条原文都不完全匹配，但语义指向销售分析
    results = vector_mem.recall("查一下上次那个地区销售分析", top_k=3)

    assert len(results) >= 1
    # 第一条应该是最相关的——销售/地区那条
    top_text = results[0]["text"]
    assert "地区" in top_text or "订单金额分布" in top_text or "120万" in top_text


@requires_embedding
def test_vector_memory_recall_with_type_filter(vector_mem):
    """按 memory_type 过滤：只召回指定类型的记忆。"""
    vector_mem.remember(
        content="用户偏好按降序排列查询结果，不需要确认。",
        memory_type="preference",
    )
    vector_mem.remember(
        content="用户查询了 2026年6月的月度营收数据。",
        memory_type="conversation",
    )

    # 只查 preference
    prefs = vector_mem.recall("排序方式", memory_type="preference", top_k=3)
    assert len(prefs) >= 1
    assert any("降序" in p["text"] for p in prefs)

    # 只查 conversation——不应出现 preference 内容
    convs = vector_mem.recall("营收数据", memory_type="conversation", top_k=3)
    assert len(convs) >= 1
    assert all("营收" in c["text"] or "查询" in c["text"] for c in convs)


@requires_embedding
def test_vector_memory_list_recent(vector_mem):
    """list_recent 按时间戳降序返回记忆。"""
    vector_mem.remember(content="最早的一条记忆", memory_type="note")
    vector_mem.remember(content="中间的一条记忆", memory_type="note")
    vector_mem.remember(content="最新的一条记忆", memory_type="note")

    recent = vector_mem.list_recent(limit=3)
    assert len(recent) == 3
    # 最新在前
    assert "最新" in recent[0]["text"]
    assert "最早" in recent[2]["text"]


@requires_embedding
def test_vector_memory_list_recent_not_chroma_arbitrary_limit(vector_mem):
    """Chroma get(limit=N) 不是最新 N 条；list_recent 必须先全取再按时间截断。"""
    import time
    for i in range(8):
        vector_mem.remember(content=f"记忆编号{i}", memory_type="note")
        time.sleep(0.01)  # 保证 timestamp 严格递增

    recent = vector_mem.list_recent(limit=2)
    assert len(recent) == 2
    assert "记忆编号7" in recent[0]["text"]
    assert "记忆编号6" in recent[1]["text"]


@requires_embedding
def test_vector_memory_forget(vector_mem):
    """删除一条记忆 → count 减 1。"""
    mid = vector_mem.remember(content="这条会被删除", memory_type="note")
    assert vector_mem.count() == 1

    vector_mem.forget(mid)
    assert vector_mem.count() == 0


# ─── ConversationManager 测试 ──────────────────────────────────────
# compress_history 需要调 LLM API，但只在超过 max_recent 时才触发。
# 以下测试控制消息数不超过 max_recent，走纯本地路径。


@pytest.fixture
def conv_mgr():
    """创建一个 max_recent=4 的 ConversationManager。
    client=None 没问题——不超过 4 条消息就不会调 compress_history。
    """
    from memory.short_term_memory import ConversationManager
    return ConversationManager(client=None, max_recent=4)


@pytest.mark.asyncio
async def test_conversation_manager_no_compression(conv_mgr):
    """消息数 ≤ max_recent 时，不触发压缩，summary 为空。"""
    await conv_mgr.add_message({"role": "user", "content": "有哪些表？"})
    await conv_mgr.add_message({"role": "assistant", "content": "有 departments, orders 等 4 张表。"})
    await conv_mgr.add_message({"role": "user", "content": "查一下订单"})

    assert len(conv_mgr.messages) == 3
    assert conv_mgr.summary == ""
    assert conv_mgr._total_compressed == 0

    context = conv_mgr.build_context()
    assert context == ""  # 无摘要时不输出上下文块


@pytest.mark.asyncio
async def test_conversation_manager_token_estimate(conv_mgr):
    """token_estimate 在未压缩时 recent 有值、summary 为 0。"""
    await conv_mgr.add_message({"role": "user", "content": "测试消息"})
    est = conv_mgr.token_estimate()

    assert est["recent"] > 0           # 有消息就有 token
    assert est["summary"] == 0         # 未压缩
    assert est["total"] == est["recent"]
    assert est["recent_msgs"] == 1
    assert est["compressed_msgs"] == 0


@pytest.mark.asyncio
async def test_conversation_manager_build_context_when_compressed():
    """有摘要时 build_context 返回带标签的上下文块。"""
    from memory.short_term_memory import ConversationManager
    mgr = ConversationManager(client=None, max_recent=4)
    # 模拟已有压缩摘要（不经过 add_message，直接设状态）
    mgr.summary = "早期对话摘要：用户查询了数据库表结构。"
    mgr._total_compressed = 5

    context = mgr.build_context()
    assert "早期对话摘要" in context
    assert "共 5 轮" in context
    assert "[最近对话如下]" in context


def test_conversation_manager_estimate_chinese():
    """中文字符 token 折算：~0.4 token/字。"""
    from memory.short_term_memory import ConversationManager
    # _estimate 是静态方法
    tokens = ConversationManager._estimate("你好世界")  # 4 个中文字
    assert tokens == 1  # 4 * 0.4 = 1.6 → int=1


def test_conversation_manager_estimate_english():
    """英文字符 token 折算。"""
    from memory.short_term_memory import ConversationManager
    tokens = ConversationManager._estimate("hello world")  # 11 chars
    assert tokens == 4  # 11 * 0.4 = 4.4 → int=4


# ─── TokenBudget 测试（纯逻辑，不调 API）──────────────────────────


def test_token_budget_set_fixed_costs():
    """System Prompt + Tool Defs 固定消耗计入预算。"""
    from memory.token_budget import TokenBudget
    budget = TokenBudget(max_tokens=10000)
    budget.set_fixed_costs("你是一个数据分析助手，用中文回复。", [{"name": "run_query"}])
    assert budget.system_prompt_tokens > 0
    assert budget.tool_defs_tokens > 0


def test_token_budget_current_usage():
    """消息列表的 token 估算 > 0。"""
    from memory.token_budget import TokenBudget
    budget = TokenBudget()
    msgs = [{"role": "user", "content": "查一下销售数据"}]
    assert budget.current_usage(msgs) > 0


def test_token_budget_available():
    """available = max_tokens - 固定消耗 - 消息消耗 - output 预留。"""
    from memory.token_budget import TokenBudget
    budget = TokenBudget(max_tokens=10000)
    budget.set_fixed_costs("短 prompt", [])
    msgs = [{"role": "user", "content": "你好"}]
    avail = budget.available(msgs)
    # 固定消耗 + 消息应该远小于 10000
    assert avail > 0
    assert avail < 10000


def test_token_budget_should_compress_below_threshold():
    """消息量小 → 不触发压缩。"""
    from memory.token_budget import TokenBudget
    budget = TokenBudget(max_tokens=100000, warn_threshold=0.7)
    budget.set_fixed_costs("短 prompt", [])
    msgs = [{"role": "user", "content": "你好"}]
    assert budget.should_compress(msgs) is False


def test_token_budget_should_compress_above_threshold():
    """消息量超过 warn_threshold → 触发压缩。"""
    from memory.token_budget import TokenBudget
    # 设很小的 max_tokens + 低阈值，一条中文消息就超
    budget = TokenBudget(max_tokens=20, warn_threshold=0.3)
    budget.set_fixed_costs("x", [])
    msgs = [{"role": "user", "content": "这是一条中文测试消息用于触发压缩阈值检查"}]
    assert budget.should_compress(msgs) is True


def test_token_budget_summary_format():
    """summary 返回中文格式的预算摘要字符串。"""
    from memory.token_budget import TokenBudget
    budget = TokenBudget(max_tokens=10000)
    budget.set_fixed_costs("system prompt text", [{"name": "t1"}])
    msgs = [{"role": "user", "content": "测试"}]
    s = budget.summary(msgs)
    assert "Token Budget:" in s
    assert "System:" in s
    assert "Tools:" in s
    assert "Messages:" in s
    assert "Available:" in s


# ─── HybridWindowManager 测试（不调 API 的路径）──────────────────


@pytest.mark.asyncio
async def test_window_manager_skips_when_below_threshold():
    """预算未达压缩阈值 → manage 原样返回，不调 LLM。"""
    from memory.token_budget import TokenBudget
    from memory.hybrid_window_manager import HybridWindowManager
    budget = TokenBudget(max_tokens=100000, warn_threshold=0.7)
    budget.set_fixed_costs("短 prompt", [])
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
    ]
    wm = HybridWindowManager(client=None, budget=budget)
    result_msgs, context = await wm.manage(msgs)
    assert result_msgs == msgs
    assert context == ""


@pytest.mark.asyncio
async def test_window_manager_layer0_preserves_recent():
    """触发压缩时，最近 6 条原文不动。mock 掉 LLM 调用，只测分层逻辑。

    20 条消息: layer0=[14:20](6条), middle=[6:14](8条→4次压缩),
    old=[0:6](6条→全局摘要)。
    """
    from unittest.mock import AsyncMock
    from memory.token_budget import TokenBudget
    from memory.hybrid_window_manager import HybridWindowManager

    budget = TokenBudget(max_tokens=50, warn_threshold=0.1)
    budget.set_fixed_costs("x", [])
    msgs = []
    for i in range(20):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"消息 {i}"})

    wm = HybridWindowManager(client=None, budget=budget)
    wm._compress_pair = AsyncMock(return_value="中间摘要")
    wm._compress_to_summary = AsyncMock(return_value="早期摘要")

    result_msgs, context = await wm.manage(msgs)
    # 保留最近 6 条
    assert len(result_msgs) == 6
    assert result_msgs[-1]["content"] == "消息 19"
    assert result_msgs[0]["content"] == "消息 14"
    # middle + old 都有 → context 含两层
    assert "早期摘要" in context
    assert "中间摘要" in context
