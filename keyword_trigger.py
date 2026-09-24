"""无前缀指令的精准匹配路由。

框架无关（不 import AstrBot），通过组件类名鸭子类型取纯文本，
便于脱离运行时做单元测试。
"""

# AstrBot 的纯文本组件类名为 Plain（旧版本 / 其他适配器可能叫 Text），两者都接受。
_PLAIN_COMPONENT_NAMES = ("Plain", "Text")


def extract_plain_text(components) -> str:
    """从消息组件列表提取纯文本（忽略 At / Reply / 图片等非文本组件）。"""
    parts = []
    for c in components or []:
        if c.__class__.__name__ in _PLAIN_COMPONENT_NAMES:
            parts.append(getattr(c, "text", "") or "")
    return "".join(parts).strip()


def match_exact(text: str, routes: dict) -> str | None:
    """精准匹配：整条消息（去 @ 后的纯文本，trim）必须与指令词完全相等。"""
    if not text:
        return None
    return routes.get(text)


def find_reply_id(components) -> str | None:
    """取出引用回复组件指向的消息 id（无引用返回 None）。"""
    for c in components or []:
        if c.__class__.__name__ == "Reply":
            rid = getattr(c, "id", None)
            return str(rid) if rid is not None else None
    return None
