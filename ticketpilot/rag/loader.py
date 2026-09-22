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
            # 小节标题优先：chunk 以 "## " 开头时用该 heading 做 title，
            # 否则（文件头到第一个 ## 之间的引子块）回退文件级 "# " 标题。
            # 为什么：引用链路承诺的是小节粒度——retriever 拼【来源：title】，
            # LLM 被要求按「来源：《小节标题》」落引用。全部 chunk 共用文件级
            # 标题时，引用退化成《常见问题 FAQ》：指向整个文件而非答案出处，
            # 用户无法核对，D4 验收项"来源引用"名存实亡。
            first_line = chunk.split("\n", 1)[0]
            chunk_title = (first_line.lstrip("#").strip()
                           if first_line.startswith("## ") else title)
            docs.append({
                "content": chunk,
                "source": md_file.name,
                "title": chunk_title,
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
