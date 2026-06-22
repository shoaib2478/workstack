def extract_message_text(content) -> str:
    """Normalize AIMessage.content to plain text (Gemini may return block lists)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if text and (block.get("type") in (None, "text") or "text" in block):
                    parts.append(text)
            elif hasattr(block, "text") and getattr(block, "text", None):
                parts.append(block.text)
        return "\n".join(parts)
    return str(content)
