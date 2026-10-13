"""gateway/auth.py —— 网关鉴权（API Key + 多租户上下文 + 飞书回调验签/解密）

两条防线，都发生在"调用模型"之前，所以挡住就是真的没花钱：
① require_api_key：在路由上声明 Depends(require_api_key)，key 不对直接 401；
② 飞书 Webhook 回调无法带我们的 API Key，改用飞书自己的三道校验：
   verify_feishu_signature（验签）→ feishu_decrypt_body（按需解密）
   → verify_feishu_token（比对 payload 里的 token）。
"""
import hashlib
import hmac
import json

from fastapi import Header, HTTPException, Request, status

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


def _reject(detail: str) -> None:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def feishu_decrypt_body(request: Request, raw: bytes) -> bytes:
    """按需解密飞书回调，返回明文 body（可直接 json.loads）。

    配了 DEVMATE_FEISHU_ENCRYPT_KEY 时，飞书把 body 发成 {"encrypt": "..."}：
    AES-256-CBC，key = sha256(encrypt_key)，密文 = iv + 密文块，PKCS7 填充。
    解密直接复用 lark SDK 的 AESCipher（core/utils/decryptor.py），不自己造轮子。
    漏这一步的后果：把密文当明文解析，所有事件静默丢失。

    没配 encrypt_key 时原样返回（长连接模式不经过这里）。
    """
    s = get_settings()
    if not s.feishu_encrypt_key:
        _warn_no_encrypt_key()
        return raw
    try:
        envelope = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        _reject("飞书回调 body 不是合法 JSON")
    encrypt = envelope.get("encrypt") if isinstance(envelope, dict) else None
    if not encrypt:
        return raw
    from lark_oapi.core.utils.decryptor import AESCipher

    try:
        return AESCipher(s.feishu_encrypt_key).decrypt_str(encrypt).encode("utf-8")
    except Exception:  # noqa: BLE001 密钥不对就解不出东西，一律拒
        _reject("飞书回调解密失败（检查 DEVMATE_FEISHU_ENCRYPT_KEY）")


def verify_feishu_signature(request: Request, raw: bytes) -> None:
    """校验飞书签名：sha256(timestamp + nonce + encrypt_key + 【原始 body】) 的 hex
    与请求头 X-Lark-Signature 比对。不通过抛 401。

    ⚠ 两个容易踩的点：
    ① 飞书不是 HMAC，就是这一串拼接后的 SHA256 摘要（与 lark SDK 的 _verify_sign 一致）；
    ② 签名算在【原始（密文）body】上，所以必须"先验签、再解密"，顺序反了签名对不上。
    没配 encrypt_key 时无法验签：放行并打一次 warning（此时别把端点暴露到公网）。
    """
    s = get_settings()
    if not s.feishu_encrypt_key:
        return
    timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
    nonce = request.headers.get("X-Lark-Request-Nonce", "")
    signature = request.headers.get("X-Lark-Signature", "")
    expect = hashlib.sha256(
        (timestamp + nonce + s.feishu_encrypt_key).encode("utf-8") + raw
    ).hexdigest()
    if not signature or not hmac.compare_digest(expect, signature):
        _reject("飞书签名校验失败")


def verify_feishu_token(plain: bytes) -> None:
    """校验 verification_token —— 它在【解密后的 payload 里】，不是请求头。

    v2 事件在 header.token，v1 事件在顶层 token（与 lark SDK 的 do 一致）。
    没配 DEVMATE_FEISHU_VERIFICATION_TOKEN 时跳过。

    与 SDK 的一处有意差异：SDK 在 payload 不带 token 时直接跳过校验，这里拒绝。
    因为"我们配了 token 而对端没发"= 配置不一致，跳过就等于这道门白装。
    真遇到握手失败：要么在飞书后台把 Verification Token 填上，要么把这里的
    DEVMATE_FEISHU_VERIFICATION_TOKEN 留空（二者必须一致）。
    """
    s = get_settings()
    if not s.feishu_verification_token:
        return
    try:
        payload = json.loads(plain or b"{}")
    except Exception:  # noqa: BLE001
        _reject("飞书回调明文不是合法 JSON")
    token = (payload.get("header") or {}).get("token") or payload.get("token")
    if token != s.feishu_verification_token:
        _reject("verification_token 不匹配（飞书后台未配 token 时，请把 "
                "DEVMATE_FEISHU_VERIFICATION_TOKEN 留空）")


_warned = False


def _warn_no_encrypt_key() -> None:
    global _warned
    if _warned:
        return
    _warned = True
    from infra.logging import get_logger
    get_logger().warning(
        "未配置 DEVMATE_FEISHU_ENCRYPT_KEY：/webhook/feishu 跳过验签，"
        "不要把该端点暴露到公网（长连接模式不受影响）。")


def _warn_no_encrypt_key_once() -> None:  # 兼容旧调用名
    _warn_no_encrypt_key()
