"""channels/session.py —— 会话归属：(租户, 渠道, 会话) → 稳定的 thread_id

同一个 (tenant_id, channel, conversation_id) 永远映射到同一个 thread_id，
这样：① 同一飞书群的连续对话能续上；② 不同渠道/不同群互不串台；
③不同租户即使渠道+会话 id 偶然相同，thread 也不同。
"""
import hashlib

from channels.base import InboundMessage


def thread_id_for(msg: InboundMessage, tenant_id: str = "default") -> str:
    """(租户, 渠道, 会话) → 稳定 thread_id（同输入恒等同输出）。

    加入 tenant_id：多租户下，不同租户的会话彻底隔离——
    即便两个租户的 (channel, conversation_id) 偶然相同，thread 也不同。

    用哈希而非拼接，是为了得到定长、无特殊字符的 id（适合做 checkpointer 的 key）。
    """
    raw = f"{tenant_id}:{msg.channel}:{msg.conversation_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
