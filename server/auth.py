"""Web API 鉴权：可选 Bearer Token。

配置 `WEB_API_TOKEN` 后，除 /api/health 外所有接口要求
`Authorization: Bearer <token>`（或 `X-API-Key: <token>`）。
未配置时鉴权关闭（demo 默认零配置可跑）；配置后立即启用。
"""

import os

from fastapi import Header, HTTPException


def _expected_token() -> str:
    return os.getenv("WEB_API_TOKEN", "").strip()


def require_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """FastAPI 依赖：校验 Bearer Token。未配置 token 时放行（demo 模式）。"""
    token = _expected_token()
    if not token:
        return  # 没配 WEB_API_TOKEN → 鉴权关闭（本地演示）

    provided = None
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):].strip()
    elif x_api_key:
        provided = x_api_key.strip()

    if not provided or provided != token:
        raise HTTPException(status_code=401, detail="未授权：需要 Bearer Token（配置 WEB_API_TOKEN）")
