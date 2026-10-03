"""知识库入库：OCR → 表格清洗 → 切分。

设计原则与仓库其余部分一致：确定性优先、依赖可降级。
  - OCR 默认走可注入的 ocr_fn；本机装了 Tesseract 才启用真实引擎。
  - 缺引擎时跳过图片，不阻断文本/Markdown/CSV 入库。
  - 表格整块切分，不在表中间断开（否则检索只拿到半行数字）。
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# 中文制度一段大约 400 字；800 字符能装下一整条规则 + 上下文。
# overlap=150 ≈ 75 个汉字，兜住切在句中的限额数字。
DEFAULT_CHUNK_CHARS = 800
DEFAULT_CHUNK_OVERLAP = 150

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
TEXT_SUFFIXES = {".md", ".txt", ".csv"}
PDF_SUFFIXES = {".pdf"}

_FULLWIDTH_PIPE = str.maketrans({"｜": "|", "丨": "|", "│": "|"})
_DIGIT_SPACE_RE = re.compile(r"(?<=\d)[ \u00a0\u3000](?=\d)")
_MULTI_PIPE_RE = re.compile(r"\|{2,}")
_HEADING_RE = re.compile(r"^(#{1,6}|【[^】]+】)\s*")


def _is_md_separator(line: str) -> bool:
    """只认 Markdown 分隔行（|---|---|），避免把普通英文行误判成表格。"""
    body = line.strip().strip("|").replace(" ", "").replace("\t", "")
    if not body:
        return False
    return all(c in "-:—–" for c in body) and any(c in "-—–" for c in body)


@dataclass(frozen=True)
class OcrResult:
    text: str
    engine: str = "none"
    skipped: bool = False
    reason: str = ""
    confidence: float | None = None


@dataclass
class DocumentChunk:
    text: str
    title: str
    kind: str  # prose | table
    source: str
    chunk_index: int
    category: str = ""
    extra: dict = field(default_factory=dict)


def run_ocr(image_bytes: bytes, ocr_fn: Callable[[bytes], str] | None = None) -> OcrResult:
    """把扫描件转成文字。优先用调用方注入的 ocr_fn（测试/自建引擎）。"""
    if ocr_fn is not None:
        text = (ocr_fn(image_bytes) or "").strip()
        return OcrResult(
            text=text,
            engine="injected",
            skipped=not bool(text),
            reason="" if text else "empty_ocr",
        )
    return _tesseract_ocr(image_bytes)


def _tesseract_ocr(image_bytes: bytes) -> OcrResult:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return OcrResult(text="", skipped=True, reason="ocr_unavailable", engine="tesseract")
    try:
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang="chi_sim+eng") or ""
        text = text.strip()
        return OcrResult(
            text=text,
            engine="tesseract",
            skipped=not bool(text),
            reason="" if text else "empty_ocr",
        )
    except Exception as exc:
        return OcrResult(text="", skipped=True, reason=str(exc), engine="tesseract")


def clean_prose(text: str) -> str:
    """去 OCR 常见噪声：全角空格、重复空行、被切断的数字。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_FULLWIDTH_PIPE)
    text = _DIGIT_SPACE_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_table(raw: str) -> str:
    """把 OCR/CSV/Markdown 脏表收成标准 Markdown 表。

    清洗：全角竖线、数字被空格切断（8 00 → 800）、空行、列数不齐。
    表格作为后续切片的原子块，所以这里必须产出稳定、可检索的文本。
    """
    raw = clean_prose(raw)
    if not raw:
        return ""
    rows: list[list[str]] = []
    for line in raw.splitlines():
        line = _MULTI_PIPE_RE.sub("|", line.strip())
        if not line or _is_md_separator(line):
            continue
        if line.count("|") >= 2:
            cells = [c.strip() for c in line.strip("|").split("|")]
        elif "\t" in line:
            cells = [c.strip() for c in line.split("\t")]
        elif line.count(",") >= 2:
            cells = [c.strip() for c in next(csv.reader([line]))]
        else:
            continue
        if any(c for c in cells):
            rows.append(cells)
    if not rows:
        return raw
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    sep = ["---"] * width

    def _fmt(r: list[str]) -> str:
        return "| " + " | ".join(c.strip() for c in r) + " |"

    lines = [_fmt(header), _fmt(sep)]
    for r in body:
        if any(c.strip() for c in r):
            lines.append(_fmt(r))
    return "\n".join(lines)


def _is_table_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _is_md_separator(s):
        return True
    if s.count("|") >= 2:
        return True
    if "\t" in s and len(s.split("\t")) >= 2:
        return True
    return bool(s.count(",") >= 2 and not s.startswith("#"))


def split_blocks(text: str) -> list[tuple[str, str]]:
    """切成 (kind, text) 块：table 连续成块，其余是 prose。"""
    text = clean_prose(text)
    if not text:
        return []
    blocks: list[tuple[str, str]] = []
    buf: list[str] = []
    kind = "prose"

    def flush() -> None:
        nonlocal buf, kind
        if not buf:
            return
        body = "\n".join(buf).strip()
        if body:
            if kind == "table":
                body = clean_table(body)
            blocks.append((kind, body))
        buf = []

    for line in text.splitlines():
        table_line = _is_table_line(line)
        next_kind = "table" if table_line else "prose"
        if buf and next_kind != kind and line.strip():
            flush()
            kind = next_kind
        elif not buf:
            kind = next_kind if line.strip() else "prose"
        buf.append(line)
    flush()
    return blocks


def chunk_text(
    text: str,
    *,
    title: str,
    source: str,
    category: str = "",
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[DocumentChunk]:
    """按块切片：表格整表一块；正文按标题优先，再按字数窗口。"""
    chunks: list[DocumentChunk] = []
    idx = 0
    for kind, body in split_blocks(text):
        if kind == "table":
            chunks.append(
                DocumentChunk(
                    text=f"【表格】{title}\n{body}",
                    title=title,
                    kind="table",
                    source=source,
                    chunk_index=idx,
                    category=category,
                )
            )
            idx += 1
            continue
        for piece in _chunk_prose(body, max_chars, overlap):
            chunks.append(
                DocumentChunk(
                    text=piece,
                    title=title,
                    kind="prose",
                    source=source,
                    chunk_index=idx,
                    category=category,
                )
            )
            idx += 1
    return chunks


def _chunk_prose(text: str, max_chars: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if _HEADING_RE.match(line) and buf:
            parts.append("\n".join(buf).strip())
            buf = [line]
        else:
            buf.append(line)
    if buf:
        parts.append("\n".join(buf).strip())

    out: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            if part:
                out.append(part)
            continue
        start = 0
        while start < len(part):
            end = start + max_chars
            piece = part[start:end].strip()
            if piece:
                out.append(piece)
            if end >= len(part):
                break
            start = max(end - overlap, start + 1)
    return out


def _read_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:
        return ""


def ingest_bytes(
    data: bytes,
    filename: str,
    *,
    category: str = "",
    ocr_fn: Callable[[bytes], str] | None = None,
) -> tuple[list[DocumentChunk], OcrResult | None]:
    """从文件字节入库。返回 (切片, OCR 结果或 None)。"""
    path = Path(filename)
    suffix = path.suffix.lower()
    title = path.stem
    source = path.name
    ocr_meta: OcrResult | None = None

    if suffix == ".csv":
        text = clean_table(data.decode("utf-8", errors="replace"))
    elif suffix in TEXT_SUFFIXES:
        text = data.decode("utf-8", errors="replace")
    elif suffix in IMAGE_SUFFIXES:
        ocr_meta = run_ocr(data, ocr_fn=ocr_fn)
        text = ocr_meta.text
    elif suffix in PDF_SUFFIXES:
        text = _read_pdf_text(data)
        if len(text) < 40:
            ocr_meta = run_ocr(data, ocr_fn=ocr_fn)
            if ocr_meta.text:
                text = ocr_meta.text
    else:
        text = data.decode("utf-8", errors="replace")

    text = clean_prose(text)
    chunks = chunk_text(text, title=title, source=source, category=category)
    return chunks, ocr_meta


def ingest_path(
    path: str | Path,
    *,
    category: str = "",
    ocr_fn: Callable[[bytes], str] | None = None,
) -> tuple[list[DocumentChunk], OcrResult | None]:
    p = Path(path)
    return ingest_bytes(p.read_bytes(), p.name, category=category, ocr_fn=ocr_fn)


def default_inbox_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "db" / "knowledge_inbox"


_SKIP_INBOX_NAMES = {".gitkeep", "categories.json", "README.md"}


def _load_category_map(root: Path, override: dict[str, str] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    manifest = root / "categories.json"
    if manifest.is_file():
        import json
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            mapping.update({str(k): str(v) for k, v in data.items()})
    if override:
        mapping.update(override)
    return mapping


def ingest_inbox(
    inbox: str | Path | None = None,
    *,
    ocr_fn: Callable[[bytes], str] | None = None,
    category_by_name: dict[str, str] | None = None,
) -> list[DocumentChunk]:
    """扫描 inbox 目录，跳过隐藏文件、清单和说明文件。"""
    root = Path(inbox) if inbox else default_inbox_dir()
    if not root.is_dir():
        return []
    chunks: list[DocumentChunk] = []
    mapping = _load_category_map(root, category_by_name)
    for path in sorted(root.iterdir()):
        if path.name.startswith(".") or path.name in _SKIP_INBOX_NAMES:
            continue
        if not path.is_file():
            continue
        category = mapping.get(path.stem, mapping.get(path.name, ""))
        file_chunks, _ = ingest_path(path, category=category, ocr_fn=ocr_fn)
        chunks.extend(file_chunks)
    return chunks
