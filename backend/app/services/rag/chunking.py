"""Split text into overlapping chunks along paragraph boundaries."""

import re


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Pack paragraphs into chunks of about `size` chars.

    Each chunk after the first starts with up to `overlap` chars from the end of the
    previous one, cut at a word boundary, so it can exceed `size` by `overlap` + 2.
    A paragraph longer than `size` is split at the last space before the limit, or
    hard-cut if it has none.
    """
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")

    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:
            cut = para.rfind(" ", 0, size + 1)
            cut = cut if cut > 0 else size
            pieces.append(para[:cut].rstrip())
            para = para[cut:].lstrip()
        if para:
            pieces.append(para)

    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + 2 + len(piece) > size:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap else ""
            if " " in tail and len(cur) > overlap:
                tail = tail[tail.index(" ") + 1 :]
            cur = f"{tail}\n\n{piece}" if tail else piece
        else:
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks
