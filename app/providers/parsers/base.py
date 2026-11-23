from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedDocument:
    content: str
    source_type: str
    metadata: dict = field(default_factory=dict)


class DocumentParser(ABC):
    extensions: tuple[str, ...] = ()

    def supports(self, filename: str, mime_type: str | None = None) -> bool:
        return Path(filename).suffix.lower() in self.extensions

    @abstractmethod
    async def parse(self, file_bytes: bytes, *, filename: str) -> ParsedDocument:
        raise NotImplementedError
