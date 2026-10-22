"""可选管理员令牌校验（环境变量 ``NPU_ADMIN_TOKEN``）。

设计要点：

* 未设置 ``NPU_ADMIN_TOKEN`` 时**完全放行**，保证本地演示零配置即可使用；
* 设置后，管理类写操作（新增/修改/删除 Provider、模型、默认模型、删除会话等）
  必须携带请求头 ``X-Admin-Token``；
* 读操作（GET）与面向学生的对话接口不做校验，避免影响正常使用。

本模块不参与 ``main.py`` 的装配，需要保护的 router 通过
``Depends(require_admin_token)`` 使用即可。
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from . import config

ADMIN_TOKEN_HEADER = "X-Admin-Token"


def admin_token_required() -> bool:
    """当前部署是否启用了管理员令牌。"""
    return bool(config.ADMIN_TOKEN)


def verify_admin_token(token: str | None) -> None:
    """校验令牌；未配置令牌时直接放行，校验失败抛出 401。"""
    expected = config.ADMIN_TOKEN
    if not expected:
        return
    if not token:
        raise HTTPException(
            status_code=401,
            detail=f"该操作需要管理员令牌：请在请求头 {ADMIN_TOKEN_HEADER} 中提供",
        )
    if not secrets.compare_digest(str(token), expected):
        raise HTTPException(
            status_code=401,
            detail=f"管理员令牌不正确，请检查请求头 {ADMIN_TOKEN_HEADER}",
        )


def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
) -> None:
    """FastAPI 依赖：写操作的路由通过 ``dependencies=[Depends(require_admin_token)]`` 挂载。"""
    verify_admin_token(x_admin_token)
