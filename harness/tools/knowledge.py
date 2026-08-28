"""知识库检索 + 用户记忆 + 向量记忆 Tool。

用 @tool 装饰器写的版本——对比之前手动写 JSON Schema dict 的版本：
  之前: 每个 Tool ~30 行（schema dict 25 行 + 函数 5 行），参数名改一处改三处
  现在: 每个 Tool ~15 行（装饰器 1 行 + docstring 8 行 + 函数体 6 行），
        改参数只改函数签名，schema 自动跟。

装饰器做的事（tools/__init__.py）：
  1. type hints → JSON Schema 类型映射
  2. docstring → 每个参数生成 description
  3. 有默认值的参数 → 不放入 required
  4. 函数名 → Tool name
"""

import sqlite3

from harness.tools import tool

# 向量记忆 / LLM——由 main.py 注入（模块级单例，避免 Tool 参数里传对象）
_vector_memory = None
_llm_client = None
_rag_pipeline = None
_kb_memory = None        # 知识库向量索引（VectorMemory，collection=knowledge_base）


def set_vector_memory(vm):
    """注入 VectorMemory 实例。main.py 初始化后调用。"""
    global _vector_memory
    _vector_memory = vm


def set_llm_client(client):
    """注入 LLM client，供 search_memory 的 Self-Query 拆解使用。"""
    global _llm_client
    _llm_client = client


def set_rag_pipeline(rag):
    """注入 RAGPipeline（HyDE + LLM rerank），供 search_memory 精排使用。"""
    global _rag_pipeline
    _rag_pipeline = rag


def set_knowledge_base_memory(vm):
    """注入知识库向量索引（测试用，embed_fn 离线确定性）。"""
    global _kb_memory
    _kb_memory = vm


def build_knowledge_base_index(embed_fn=None):
    """把 _KNOWLEDGE_BASE 索引进向量库（collection=knowledge_base）。

    embed_fn 用于测试注入离线 embedding；为 None 时走环境变量 EMBEDDING_API_KEY。
    embedding 不可用会抛异常，由调用方（main.py）捕获降级到关键词检索。
    count()==0 幂等检查：首次索引 19 篇，之后跳过重复 embed。
    """
    global _kb_memory
    from harness.memory.vector_store import VectorMemory
    vm = VectorMemory(collection_name="knowledge_base", embed_fn=embed_fn)
    if vm.count() == 0:
        for title, content in _KNOWLEDGE_BASE.items():
            vm.remember(
                content, memory_type="knowledge",
                metadata={"title": title, "category": _DOC_CATEGORIES.get(title, "")},
            )
    _kb_memory = vm
    return vm


# ─── 模拟知识库文档 ───────────────────────────────────────────
# Phase 3.3 换 ChromaDB 向量检索，这套文档结构不变。

def _ensure_user_memory_table(conn):
    """读取 db/user_memory.sql 确保表结构存在（课程 0017：独立 schema 文件）。"""
    import os
    sql_path = os.path.join(os.path.dirname(__file__), "..", "..", "db", "user_memory.sql")
    with open(sql_path) as f:
        conn.executescript(f.read())


_KNOWLEDGE_BASE = {
    # ── 销售 & 提成 ──
    "销售提成制度": (
        "销售部提成 = 订单金额 × 提成比例。"
        "提成比例按产品类型：软件类 8%，硬件类 5%，服务类 3%。"
        "季度销售额超过 50 万的销售代表，提成比例上浮 2 个百分点。"
        "提成每月核算，次月 10 号随工资发放。"
    ),
    "客户分级标准": (
        "S 级：年采购额 > 200 万或战略合作客户，享专属客户经理 + 7×24 响应。"
        "A 级：年采购额 50-200 万，享季度回访 + 优先排期。"
        "B 级：年采购额 < 50 万，标准服务。"
        "每年 Q4 重新评级，新客户首年默认 B 级，大单可申请越级。"
    ),

    # ── 产品 & 定价 ──
    "产品定价说明": (
        "企业版 SaaS 年费 50,000 元，专业版 20,000 元，基础版 5,000 元。"
        "定制开发 4,000 元/人天，紧急 6,000 元/人天。技术咨询 30,000 元起。"
        "批量采购折扣：5-10 套 9 折，11-50 套 8.5 折，50+ 套面议。"
    ),
    "产品退换政策": (
        "SaaS 订阅：购买后 7 天内无条件退款，超过 7 天按剩余月份比例退款。"
        "硬件产品：15 天内质量问题包换，1 年内免费维修。"
        "定制开发：需求确认后不退，变更需求按新增人天计费。"
    ),

    # ── 人事 & 考勤 ──
    "考勤制度": (
        "标准工时 9:00-18:00，弹性上下班 ±1 小时。"
        "迟到 3 次/月内不扣款，超 3 次每次扣 50 元。"
        "旷工半天扣当日工资，连续旷工 3 天或月累计 5 天按自动离职处理。"
        "加班需提前申请：工作日 1.5 倍，休息日 2 倍，法定假日 3 倍。"
    ),
    "休假制度": (
        "带薪年假：入职满 1 年 10 天，满 3 年 15 天，满 5 年 20 天，上限 25 天。"
        "病假：带薪 5 天/年，超出的按基本工资 60% 计。"
        "婚假 10 天，产假 158 天，陪产假 15 天，丧假 3 天。"
        "年假可跨年结转，最多保留 5 天至次年 3 月底。"
    ),
    "绩效考核制度": (
        "考核周期：季度考核 + 年度总评。"
        "S 级（前 10%）：奖金系数 1.5，优先晋升。"
        "A 级（10%-35%）：奖金系数 1.2。"
        "B 级（35%-85%）：奖金系数 1.0。"
        "C 级（后 15%）：奖金系数 0.8，连续两季 C 进入 PIP。"
        "D 级（后 5%）：无奖金，直接 PIP。"
    ),
    "招聘流程": (
        "需求审批：部门提出 → HR 审核 → 分管 VP 批准。"
        "面试流程：HR 初筛 → 技术面 1 → 技术面 2 → HR 终面 → Offer。"
        "关键岗位（P6+）需加一轮跨部门交叉面。"
        "内推奖励：入职通过试用期后，推荐人奖 5000-20000 元（按级别）。"
    ),
    "员工福利政策": (
        "正式员工享有五险一金、补充商业保险、年度体检。"
        "每月交通补贴 500 元，通讯补贴 300 元。加班餐补：工作日超 2h 补 50 元。"
        "学习基金：每人每年 5000 元，可用于课程、认证、技术书籍。"
    ),

    # ── 财务 & 采购 ──
    "报销制度": (
        "差旅：高铁二等座/飞机经济舱实报实销，住宿 400 元/晚上限，餐补 100 元/天。"
        "招待费：单次人均 ≤ 300 元，月累计 ≤ 3000 元，超标需 VP 特批。"
        "常规报销（办公用品、市内交通）：月结，次月 15 号打款。"
        "所有报销需 3 个工作日内提交发票，超期不予受理。"
    ),
    "采购流程": (
        "小额采购（< 5000 元）：部门负责人审批即可。"
        "中额采购（5000-50,000 元）：部门 + 财务双审批，至少两家比价。"
        "大额采购（> 50,000 元）：公开招标或三家以上竞争性谈判，VP 终批。"
        "软件/SaaS 类采购统一走 IT 部门审核安全合规。"
    ),
    "预算管理制度": (
        "年度预算每年 11 月启动编制，12 月终稿，次年 1 月生效。"
        "各部门预算包含：人力成本、运营费用、项目经费、储备金（5%）。"
        "超预算 10% 以内由 VP 审批，超 10% 需 CEO 审批。"
        "季度预算 review：Q2 初可根据上半年实际情况调整下半年预算。"
    ),

    # ── IT & 数据安全 ──
    "数据安全管理制度": (
        "数据分级：公开（L1）、内部（L2）、机密（L3）、绝密（L4）。"
        "客户数据和财务数据为 L3，任何导出操作需主管审批并留日志。"
        "数据库只读权限仅 DBA 和数据分析师可申请，生产写操作需双人复核。"
        "员工离职当天关闭所有系统权限，数据保留 30 天后清理。"
        "禁止将客户数据上传至外部 AI 平台（ChatGPT 等），违者按信息安全事件处理。"
    ),
    "IT 设备管理": (
        "新员工标配：MacBook Pro / ThinkPad X1（按角色二选一）+ 27 寸显示器。"
        "设备更换周期：笔记本 3 年、显示器 5 年、手机 2 年。"
        "设备遗失：需 24 小时内报备 IT + 直属领导，个人承担 30% 折旧费。"
        "离职归还：全套设备含充电器，缺失按折旧费扣款。"
    ),

    # ── 战略 & 项目 ──
    "2026年公司战略": (
        "Q1: SaaS 3.0 上线。Q2: 拓展华东市场，新增 50 客户，营收增长 20%。"
        "Q3: 启动 AI 功能开发，招 5 名 AI 工程师。全年: 营收 5000 万，净利率 15%。"
        "核心竞争力：从卖软件转型为卖行业解决方案，客单价提升 30%。"
    ),
    "项目管理流程": (
        "立项：提交项目章程（目标、范围、预算、里程碑）→ PMO 审核 → VP 批准。"
        "执行：双周 sprint，每周五站会 + 周五 demo。"
        "变更：需求变更走 CR 流程，评估影响 + 成本后由项目经理和需求方双签。"
        "结项：交付物验收 + 复盘报告（What went well / What didn't / Action items）。"
        "项目分级：A 级（战略项目，CEO 关注）、B 级（部门级）、C 级（小改进）。"
    ),

    # ── 大数据 & 数据工程 ──
    "HBase操作参考": (
        "HBase 是列式 NoSQL 数据库，无 SQL——用 Shell 命令操作。"
        "scan '表名': 扫描表，加 {COLUMNS=>'cf:col', LIMIT=>10, FILTER=>\"...\"} 限返回。"
        "get '表名', '行键': 按行键精确读一行。"
        "count '表名': 统计行数，可加 FILTER 统计符合条件的行。"
        "put '表名', '行键', '列族:列名', '值': 写入一个单元格。"
        "delete '表名', '行键', '列族:列名': 删除单元格（deleteall 删整行）。"
        "create '表名', '列族1', '列族2': 建表，可指定 VERSIONS（版本数）、TTL（过期秒数）。"
        "describe '表名': 查看表结构（列族、属性）。disable '表名' / enable '表名' / drop '表名'。"
        "常用 Filter: RowFilter(行键过滤)、SingleColumnValueFilter(列值过滤)、PrefixFilter(行键前缀)、"
        "ValueFilter(值过滤)、KeyOnlyFilter(只返回行键)、PageFilter(分页)、FirstKeyOnlyFilter(快计数)。"
        "操作符: =, !=, >, >=, <, <=。"
        "比较器: binary:精确, binaryprefix:前缀, substring:子串, regexstring:正则。"
        "scan 全表极慢——生产务必加 STARTROW/STOPROW 或 FILTER 限制扫描范围。"
    ),
    "Hive/Hue表结构参考": (
        "Hive 数据仓库表通常存储在 HDFS 上，建表时指定 LOCATION 和存储格式。"
        "典型订单表（orders_hive）: "
        "order_id BIGINT, customer_id INT, product_id INT, total DECIMAL(12,2), "
        "quantity INT, status STRING, created_at TIMESTAMP "
        "PARTITIONED BY (dt STRING, region STRING) "
        "STORED AS PARQUET。"
        "典型用户行为表（user_events）: "
        "user_id BIGINT, event_type STRING, event_props MAP<STRING,STRING>, "
        "event_time TIMESTAMP "
        "PARTITIONED BY (dt STRING) STORED AS ORC。"
        "Hive 支持复杂类型: ARRAY<STRING>（数组）、MAP<STRING,INT>（键值对）、STRUCT<a:INT,b:STRING>（结构体）。"
        "用 LATERAL VIEW explode(array_col) 展开数组——这是 Hive 独有的写法，Impala 用 FROM t, t.arr。"
        "分区表查特定日期: SELECT ... FROM t WHERE dt='2025-06-01'（Hive 会自动分区裁剪跳过无关目录）。"
        "Hue 是 Cloudera 的 Web 查询编辑器——不是独立引擎，它在后台调 HiveServer2 或 Impala daemon。"
    ),
    "HiveQL与Impala语法差异": (
        "Hive 和 Impala 共享大部分 SQL 语法，但有重要差异。"
        "统计信息: Hive 用 ANALYZE TABLE t COMPUTE STATISTICS；Impala 用 COMPUTE STATS t。"
        "分区查看: 两者都用 SHOW PARTITIONS t。"
        "INSERT: Hive INSERT OVERWRITE 可能覆盖整表；Impala 仅覆盖指定分区。"
        "窗口函数: Impala 要求窗口内必须有 ORDER BY，Hive 可选。Impala 不支持 ROWS BETWEEN。"
        "LATERAL VIEW: Hive 用 LATERAL VIEW explode(col)；Impala 用 FROM t, t.arr_col AS item。"
        "JOIN: Impala 支持 LEFT ANTI JOIN（Hive 无），Hive 支持 MAPJOIN hint。"
        "OFFSET: Impala 支持 LIMIT n OFFSET m 分页；Hive 仅 LIMIT。"
        "存储格式: Hive 支持 ORC 最好；Impala 推荐 Parquet（ORC 支持不完整）。"
        "COMPUTE STATS 对 Impala 至关重要——未统计的表 JOIN 顺序可能很差，查询慢数十倍。"
        "Hue 上写查询时注意：选中 Impala 引擎才有 COMPUTE STATS / STRAIGHT_JOIN / LEFT ANTI JOIN 等特性。"
    ),
}


# 文档分类——用于 docs_filter 权限过滤（entitlement.filter_docs 按 category 匹配）。
# category 值对齐 entitlement 里 viewer/support 的 docs_filter 白名单：
#   viewer = ["产品手册","部门介绍","销售制度"]  → 命中「产品手册」「销售制度」类
#   support = ["技术文档","产品手册"]             → 命中「技术文档」「产品手册」类
# 「部门介绍」暂无对应文档，是白名单里的死条目（or 关系，不影响结果）。
_DOC_CATEGORIES = {
    "销售提成制度": "销售制度",
    "客户分级标准": "销售制度",
    "产品定价说明": "产品手册",
    "产品退换政策": "产品手册",
    "考勤制度": "人事制度",
    "休假制度": "人事制度",
    "绩效考核制度": "人事制度",
    "招聘流程": "人事制度",
    "员工福利政策": "人事制度",
    "报销制度": "财务制度",
    "采购流程": "财务制度",
    "预算管理制度": "财务制度",
    "数据安全管理制度": "数据安全",
    "IT 设备管理": "IT制度",
    "2026年公司战略": "战略文档",
    "项目管理流程": "战略文档",
    "HBase操作参考": "技术文档",
    "Hive/Hue表结构参考": "技术文档",
    "HiveQL与Impala语法差异": "技术文档",
}


# ─── Tool 定义 ────────────────────────────────────────────────
# 关键设计意图:
# - search_memory: 查"Agent 经历过什么"（跨会话记忆）—— Self-Query + 向量检索
# - run_query:    查"数据库里有什么"（结构化实时数据）— tools/query.py
# - search_knowledge_base: 查"公司知道什么"（静态知识文档）


def _keyword_search(query: str, top_k: int) -> list[dict]:
    """关键词打分检索——向量索引未就绪时的降级路径（离线/测试无 embedding）。"""
    def score(text: str) -> float:
        q, t = query.lower(), text.lower()
        if q in t:
            return 10.0 + len(q)
        hits = sum(1 for kw in q.split() if kw in t)
        return hits / max(len(q.split()), 1) * 5

    scored = []
    for title, content in _KNOWLEDGE_BASE.items():
        s = score(title) * 1.5 + score(content)
        if s > 0:
            scored.append({
                "title": title, "content": content,
                "category": _DOC_CATEGORIES.get(title, ""),
                "score": round(s, 1),
            })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def _vector_search(query: str, top_k: int) -> list[dict]:
    """向量语义检索——从知识库索引召回。"""
    results = _kb_memory.recall(query, top_k=top_k, memory_type="knowledge")
    return [
        {
            "title": r["metadata"].get("title", ""),
            "content": r["text"],
            "category": r["metadata"].get("category", ""),
            "score": r.get("score"),
        }
        for r in results
    ]


@tool(description=(
    "搜索公司知识库（规章制度、产品政策、战略文档）。"
    "当用户问公司的提成怎么算、年假多少天、产品怎么定价等非数据库查询时调用。"
    "不要用此 Tool 查销售数据、订单——那些在数据库里，用 run_query。"
    "返回 {results: [{title, content, score, category}], count}；无匹配时 hint 建议换关键词。"
))
def search_knowledge_base(query: str, top_k: int = 3) -> dict:
    """query: 自然语言搜索词，如 '提成'、'年假'
    top_k: 返回条数，默认 3"""
    if not query.strip():
        return {"results": [], "count": 0, "hint": "搜索词为空"}

    # 候选：向量语义检索优先；索引未就绪时降级关键词打分。
    # 多取几倍候选，给 filter_docs 权限过滤留余量（否则过滤后可能不足 top_k）。
    n = max(top_k * 3, 5)
    if _kb_memory is not None and _kb_memory.count() > 0:
        candidates = _vector_search(query, n)
    else:
        candidates = _keyword_search(query, n)

    # 文档级权限：按用户角色 docs_filter 白名单过滤（鉴权洞 #3）。
    from harness.constraints.entitlement import guard, resolve_user_id
    ent = guard(resolve_user_id(), "search_knowledge_base", docs=candidates)
    if isinstance(ent, dict):
        return ent
    results = (ent.docs or [])[:top_k]

    return {
        "results": results, "count": len(results),
        "hint": None if results else "没有匹配的文档，试试换个关键词",
    }


@tool(description=(
    "将重要信息存入用户记忆。当用户表达偏好（'以后按降序排'）、"
    "做决策、或产生值得记住的洞察时调用。跨会话可通过 read_memory 找回。"
    "返回 {stored: true, memory_id: N}。"
))
def save_to_memory(content: str, memory_type: str = "note") -> dict:
    """content: 要记忆的内容，完整描述方便以后检索
    memory_type: preference(偏好) / insight(洞察) / note(备注)"""
    from db.seed import DB_PATH
    from harness.constraints.entitlement import resolve_user_id
    user_id = resolve_user_id()
    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_user_memory_table(conn)
        cur = conn.execute(
            "INSERT INTO user_memory (user_id, memory_type, content) VALUES (?,?,?)",
            (user_id, memory_type, content))
        conn.commit()
        return {"stored": True, "memory_id": cur.lastrowid}
    except Exception as e:
        return {"error": True, "message": str(e)}
    finally:
        conn.close()


@tool(description=(
    "读取用户记忆。用户说'上次'、'之前'、'我的偏好'时调用。"
    "按时间倒序返回。返回 {memories: [{id, memory_type, content, created_at}], count}。"
))
def read_memory(memory_type: str = "all", limit: int = 10) -> dict:
    """memory_type: preference / insight / note / all（不过滤）
    limit: 返回条数，默认 10"""
    from db.seed import DB_PATH
    from harness.constraints.entitlement import resolve_user_id
    user_id = resolve_user_id()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_user_memory_table(conn)
        if memory_type == "all":
            cur = conn.execute(
                "SELECT * FROM user_memory WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit))
        else:
            cur = conn.execute(
                "SELECT * FROM user_memory WHERE user_id=? AND memory_type=? ORDER BY created_at DESC LIMIT ?",
                (user_id, memory_type, limit))
        mems = [dict(r) for r in cur.fetchall()]
        if mems:
            ids = [m["id"] for m in mems]
            conn.execute(
                f"UPDATE user_memory SET access_count=access_count+1 WHERE id IN ({','.join('?'*len(ids))})", ids)
            conn.commit()
        return {"memories": mems, "count": len(mems)}
    except Exception as e:
        return {"error": True, "message": str(e)}
    finally:
        conn.close()


@tool(description=(
    "搜索 Agent 的长期对话记忆（Self-Query + 向量语义检索）。"
    "当用户提到'上次'、'之前'、'我记得'、'历史'等引用过去对话的关键词时调用。"
    "也适合用户问模糊的问题、需要从历史中找到相关上下文时。"
    "内部会先拆解语义部分与过滤条件（memory_type/year），再检索。"
    "注意：查结构化数据（订单、员工、销售额）用 run_query；查公司政策用 search_knowledge_base。"
    "返回 {results: [{text, score, metadata}], count, parsed}；库为空时返回空列表。"
))
async def search_memory(query: str, top_k: int = 5,
                        memory_type: str = None) -> dict:
    """query: 自然语言查询，如 '上次那个销售分析'、'之前讨论过的地区数据'
    top_k: 返回条数，默认 5
    memory_type: conversation(对话) / preference(偏好) / None(不过滤，由 Self-Query 抽取)"""
    if _vector_memory is None:
        return {"results": [], "count": 0, "error": "向量记忆未初始化"}
    if _llm_client is None:
        # 无 LLM 时退回普通 recall，保证 Tool 仍可用
        results = _vector_memory.recall(
            query, top_k=top_k, memory_type=memory_type,
        )
        return {
            "results": [
                {"text": r["text"], "score": r.get("score"), "metadata": r.get("metadata")}
                for r in results
            ],
            "count": len(results),
            "parsed": {"semantic_query": query, "filters": {}},
        }

    from harness.context.self_query import self_query_retrieve

    results, parts = await self_query_retrieve(
        query,
        _vector_memory,
        _llm_client,
        top_k=top_k,
        memory_type=memory_type,
        reranker=_rag_pipeline,
    )
    return {
        "results": [
            {"text": r["text"], "score": r.get("score"), "metadata": r.get("metadata")}
            for r in results
        ],
        "count": len(results),
        "parsed": parts,
    }
