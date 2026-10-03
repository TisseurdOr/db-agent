"""Web API 鉴权：Bearer Token / X-API-Key。

三种模式（按环境变量自动切换）：

1. 每用户 token（推荐，真修身份）：
       WEB_API_TOKENS="dba:tokA,zhoufang:tokB,viewer:tokC"
   请求身份 = token 对应的用户，**客户端传入的 user_id 一律忽略**。

2. 单 token（旧的共享密钥，兼容）：
       WEB_API_TOKEN="tok-shared"
   校验通过后身份取服务端配置（AGENT_USER / AGENT_DEFAULT_USER），同样忽略客户端值。

3. 演示模式（两者都没配，默认）：
   不校验、身份由前端"切角色"决定。仅供本地/公开 demo —— 此时 RBAC 不是安全边界，
   只是角色演示，不要用它保护任何真实数据。
"""

import contextvars
import os

from fastapi import Header, HTTPException

# 本请求的服务端身份（由 require_auth 从 token 解析）。注意：必须是 async 依赖，
# 否则 FastAPI 会把依赖丢进线程池，ContextVar 传不到端点。
_auth_user: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "web_auth_user", default=None
)


def _parse_token_map() -> dict[str, str]:
    """WEB_API_TOKENS="user:token,user2:token2" → {token: user}。"""
    raw = os.getenv("WEB_API_TOKENS", "").strip()
    mapping: dict[str, str] = {}
    if not raw:
        return mapping
    for pair in raw.replace("\n", ",").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        uid, tok = pair.split(":", 1)
        uid, tok = uid.strip(), tok.strip()
        if uid and tok:
            mapping[tok] = uid
    return mapping


def _expected_token() -> str:
    return os.getenv("WEB_API_TOKEN", "").strip()


def _presented_token(authorization: str | None, x_api_key: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization[len("Bearer "):].strip()
    if x_api_key:
        return x_api_key.strip()
    return None


def auth_enabled() -> bool:
    """是否配置了任何 token（配置了 = RBAC 是安全边界）。"""
    return bool(_parse_token_map()) or bool(_expected_token())


def current_auth_user() -> str | None:
    """本请求由 token 解析出的身份；演示模式下为 None。"""
    return _auth_user.get()


def resolve_identity(client_user_id: str | None) -> str:
    """决定本次请求使用的 RBAC 身份。

    - 配了 token：一律用服务端 token→用户 的映射结果，忽略客户端传入值；
    - 未配（演示模式）：退回客户端传入值（缺省 viewer），供前端切角色演示。
    """
    server_side = _auth_user.get()
    if server_side:
        return server_side
    return (client_user_id or "").strip() or "viewer"


async def require_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """FastAPI 依赖：校验 token，并把服务端身份写进 ContextVar。

    未配置任何 token 时放行（演示模式），不写身份——由端点用客户端值兜底。
    """
    tokens = _parse_token_map()
    presented = _presented_token(authorization, x_api_key)

    if tokens:
        uid = tokens.get(presented or "")
        if not uid:
            raise HTTPException(status_code=401, detail="未授权：API Token 无效或缺失")
        _auth_user.set(uid)
        return

    legacy = _expected_token()
    if legacy:
        if presented != legacy:
            raise HTTPException(status_code=401, detail="未授权：需要 Bearer Token（配置 WEB_API_TOKEN）")
        _auth_user.set(os.getenv("AGENT_USER") or os.getenv("AGENT_DEFAULT_USER", "viewer"))
        return

    # 演示模式：无 token，不校验，也不设服务端身份。
    return
