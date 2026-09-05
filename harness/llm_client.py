"""Anthropic client 工厂：三个入口（CLI / Streamlit / FastAPI）共用的创建 + Opik 包装。"""

import os

from anthropic import Anthropic

from harness.observation.opik_tracing import wrap_anthropic_client


def get_anthropic_client() -> Anthropic:
    """按环境变量创建并包装 Anthropic client（接入 Opik trace）。"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 ANTHROPIC_API_KEY（请在 .env 或环境变量中配置）")
    return wrap_anthropic_client(Anthropic(
        api_key=api_key,
        base_url=os.getenv("ANTHROPIC_BASE_URL"),
    ))
