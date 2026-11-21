from abc import ABC, abstractmethod


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, content: str) -> str:
        raise NotImplementedError


class PlainTextParser(DocumentParser):
    def parse(self, content: str) -> str:
        return content.strip()
