import re


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """将长文本按段落边界切分为重叠的块，用于向量库存储。

    切分策略：
    1. 优先按段落（\\n\\n）分割
    2. 如果段落本身超过 chunk_size，按句号/换行进一步分割
    3. 相邻块之间有 overlap 字符的重叠，防止语义断裂
    """
    if len(text) <= chunk_size:
        return [text]

    # Step 1: 按段落分割
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            # 如果单个段落超长，进一步切割
            if len(para) > chunk_size:
                sub_chunks = _split_long_paragraph(para, chunk_size, overlap)
                chunks.extend(sub_chunks)
                current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    # Step 2: 添加重叠
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:] if len(chunks[i - 1]) > overlap else chunks[i - 1]
            overlapped.append(prev_tail + "\n" + chunks[i])
        return overlapped

    return chunks


def _split_long_paragraph(text: str, chunk_size: int, overlap: int) -> list[str]:
    """将超长段落按句子边界切割。"""
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) <= chunk_size:
            current = (current + sent).strip()
        else:
            if current:
                chunks.append(current)
            if len(sent) > chunk_size:
                # 按固定长度硬切（最后手段）
                for i in range(0, len(sent), chunk_size - overlap):
                    chunks.append(sent[i : i + chunk_size])
                current = ""
            else:
                current = sent

    if current:
        chunks.append(current)

    return chunks
