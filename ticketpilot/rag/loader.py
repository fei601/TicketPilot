"""
知识库文档加载器

加载 knowledge_base/ 目录下的 Markdown 文档，用于 RAG 检索。
"""

from pathlib import Path

import config


def load_knowledge_docs() -> list[dict]:
    """
    加载知识库目录下的所有 Markdown 文档。

    Returns:
        文档列表，每项包含 {"content": str, "source": str, "title": str}
    """
    docs = []
    kb_dir = config.KNOWLEDGE_BASE_DIR

    if not kb_dir.exists():
        return docs

    for md_file in kb_dir.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")

        # 提取标题（第一个 # 开头的行）
        title = md_file.stem
        for line in content.split("\n"):
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                break

        # 按 ## 分割成多个文档块
        chunks = _split_by_heading(content)

        for i, chunk in enumerate(chunks):
            docs.append({
                "content": chunk,
                "source": md_file.name,
                "title": title,
                "chunk_index": i,
            })

    return docs


def _split_by_heading(content: str, heading_level: int = 2) -> list[str]:
    """
    按指定标题级别分割文档。

    Args:
        content: 文档内容
        heading_level: 标题级别（2 表示 ##）

    Returns:
        分割后的文档块列表
    """
    prefix = "#" * heading_level + " "
    chunks = []
    current_chunk = []

    for line in content.split("\n"):
        if line.startswith(prefix) and current_chunk:
            chunks.append("\n".join(current_chunk).strip())
            current_chunk = [line]
        else:
            current_chunk.append(line)

    if current_chunk:
        chunks.append("\n".join(current_chunk).strip())

    return [c for c in chunks if c]
