from abc import ABC, abstractmethod


class TextSplitter(ABC):
    @abstractmethod
    def split(self, text: str) -> list[str]:
        raise NotImplementedError


class CharacterTextSplitter(TextSplitter):
    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk overlap must be non-negative and smaller than chunk size")
        self.chunk_size = chunk_size
        self.step = chunk_size - chunk_overlap

    def split(self, text: str) -> list[str]:
        chunks = []
        for start in range(0, len(text), self.step):
            end = start + self.chunk_size
            part = text[start:end].strip()
            if part:
                chunks.append(part)
            if end >= len(text):
                break
        return chunks
