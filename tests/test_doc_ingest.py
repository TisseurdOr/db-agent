"""OCR + 表格清洗入库：离线，不依赖 Tesseract / 真实 embedding。"""

import harness.tools.knowledge as kb
from harness.context.doc_ingest import (
    chunk_text,
    clean_table,
    ingest_bytes,
    ingest_inbox,
    run_ocr,
    split_blocks,
)
from harness.tools.knowledge import search_knowledge_base

DIRTY_TABLE = (
    "字段｜口径｜校验\n"
    "价税合计｜含税金额｜与发票联一致\n"
    "税额｜8 00｜需复核\n"
)


def test_clean_table_normalizes_ocr_noise():
    cleaned = clean_table(DIRTY_TABLE)
    assert "800" in cleaned
    assert cleaned.startswith("| 字段")
    assert cleaned.count("\n") >= 3
    assert "｜" not in cleaned


def test_split_blocks_keeps_table_atomic():
    text = "前言说明。\n\n" + DIRTY_TABLE + "\n\n后记。"
    blocks = split_blocks(text)
    kinds = [k for k, _ in blocks]
    assert "table" in kinds
    table = next(body for k, body in blocks if k == "table")
    assert "800" in table
    assert "价税合计" in table


def test_chunk_text_does_not_split_table():
    rows = ["| 列A | 列B |", "| --- | --- |"]
    rows += [f"| v{i} | {i} |" for i in range(120)]
    text = "前言\n\n" + "\n".join(rows)
    chunks = chunk_text(text, title="宽表", source="wide.md")
    tables = [c for c in chunks if c.kind == "table"]
    assert len(tables) == 1
    assert len(tables[0].text) > 800
    assert "v0" in tables[0].text and "v119" in tables[0].text


def test_run_ocr_injected():
    result = run_ocr(b"fake-bytes", ocr_fn=lambda _: DIRTY_TABLE)
    assert result.engine == "injected"
    assert not result.skipped
    assert "价税合计" in result.text


def test_run_ocr_unavailable_skips_without_engine():
    result = run_ocr(b"fake-bytes", ocr_fn=None)
    # 本机若装了 tesseract 也可能跑；缺引擎时必须跳过而不是抛。
    if result.engine == "tesseract" and result.skipped:
        assert result.reason
    elif result.engine == "tesseract" and not result.skipped:
        assert isinstance(result.text, str)
    else:
        assert result.skipped
        assert result.reason == "ocr_unavailable"


def test_ingest_image_uses_injected_ocr():
    chunks, meta = ingest_bytes(
        b"xx", "scan.png", category="财务制度", ocr_fn=lambda _: DIRTY_TABLE,
    )
    assert meta is not None and meta.engine == "injected"
    assert any(c.kind == "table" for c in chunks)
    assert any("800" in c.text for c in chunks)
    assert all(c.category == "财务制度" for c in chunks)


def test_ingest_image_without_engine_does_not_block():
    chunks, meta = ingest_bytes(b"not-an-image", "scan.png", ocr_fn=None)
    if meta is not None and meta.skipped:
        assert chunks == []


def test_ingest_csv_becomes_markdown_table():
    chunks, _ = ingest_bytes("字段,口径,校验\n购方税号,18位,与主数据一致\n".encode(), "t.csv")
    assert chunks
    assert chunks[0].kind == "table"
    assert "购方税号" in chunks[0].text
    assert chunks[0].text.startswith("【表格】")


def test_ingest_inbox_reads_categories(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "categories.json").write_text(
        '{"发票验真字段对照.md": "财务制度"}', encoding="utf-8",
    )
    (inbox / "发票验真字段对照.md").write_text(
        "# 发票验真\n\n| 字段 | 口径 |\n| --- | --- |\n| 价税合计 | 含税金额 |\n",
        encoding="utf-8",
    )
    (inbox / ".gitkeep").write_text("", encoding="utf-8")
    chunks = ingest_inbox(inbox)
    assert chunks
    assert all(c.category == "财务制度" for c in chunks)
    assert any(c.kind == "table" for c in chunks)


def test_search_knowledge_base_includes_inbox_table(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "categories.json").write_text(
        '{"发票验真字段对照.md": "财务制度"}', encoding="utf-8",
    )
    (inbox / "发票验真字段对照.md").write_text(
        "# 发票验真字段对照\n\n| 字段 | 口径 | 校验 |\n| --- | --- | --- |\n"
        "| 价税合计 | 含税金额 | 与发票联一致 |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("harness.context.doc_ingest.default_inbox_dir", lambda: inbox)
    prev = kb._kb_memory
    kb._kb_memory = None
    kb.reset_runtime_docs()
    try:
        result = search_knowledge_base("价税合计 发票联", top_k=5)
        assert result["count"] > 0
        assert any("发票" in r["title"] for r in result["results"])
        assert any(r["category"] == "财务制度" for r in result["results"])
    finally:
        kb.reset_runtime_docs()
        kb._kb_memory = prev
