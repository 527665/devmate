"""gateway/auth.py —— 网关鉴权（API Key 示例 + 多租户上下文）

用 FastAPI 依赖注入：在路由上声明 Depends(require_api_key)，
未带或错误的 key 直接 401，不进入业务。
"""
from fastapi import Header, HTTPException, status

from infra.settings import get_settings

# ⚠ 本地实况：.env 目前没配 DEVMATE_API_KEYS，settings.api_keys 是空字典 {}。
#   按下面这段逻辑，空字典 = 没有任何 key 被允许 = 一切请求都 401（不是"不鉴权"）。
#   要放行必须先往 .env 加一行单行 JSON，例如：
#   DEVMATE_API_KEYS={"k-alice": "tenant-a", "k-bob": "tenant-b"}


async def require_api_key(x_api_key: str | None = Header(None)) -> str:
    """校验请求头 X-API-Key。返回它对应的租户标识（这里简化为 key 本身）。

    生产里：key → 租户的映射查数据库/配置；这里用 settings 里配的允许列表演示。
    """
    s = get_settings()
    allowed = s.api_keys  # dict: {api_key: tenant_id}
    if not x_api_key or x_api_key not in allowed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
        )
    return allowed[x_api_key]   # 返回租户 id，供下游使用
