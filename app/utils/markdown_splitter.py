"""Split Markdown sections and pipe tables while retaining their source headings."""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from app.utils.text_splitter import CharacterTextSplitter


_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ALIGNMENT = re.compile(r":?-+:?")
_PIPE = re.compile(r"(?<!\\)\|")


@dataclass
class SplitPiece:
    text: str
    metadata: dict[str, str | int | None]


def _cells(line: str) -> list[str]:
    cells = _PIPE.split(line.strip())
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def _delimiter(line: str) -> bool:
    cells = _cells(line)
    return "|" in line and bool(cells) and all(_ALIGNMENT.fullmatch(cell) for cell in cells)


def _bordered(line: str) -> bool:
    row = line.strip()
    return len(row) > 2 and row.startswith("|") and row.endswith("|")


def _fence_marker(line: str) -> str | None:
    match = _FENCE.fullmatch(line)
    if match and (match[1][0] == "~" or "`" not in match[2]):
        return match[1]
    return None


def _table_end(lines: list[str], start: int) -> int:
    if start + 1 == len(lines) or "|" not in lines[start]:
        return start
    columns = len(_cells(lines[start]))
    aligned = _delimiter(lines[start + 1]) and len(_cells(lines[start + 1])) == columns
    if not aligned and not (_bordered(lines[start]) and _bordered(lines[start + 1])):
        return start
    end = start + 2
    while end < len(lines):
        row = lines[end]
        if not _bordered(row):
            # Rows without outer pipes need the column count confirmed by an alignment row.
            if not aligned or "|" not in row or _HEADING.fullmatch(row) or _fence_marker(row) or len(_cells(row)) != columns:
                break
        end += 1
    return end


def _groups(header: str, rows: list[str], footer: str | None, size: int, max_rows: int | None = None) -> Iterator[str]:
    """Repeat framing lines. An indivisible data line can exceed the character budget."""
    frame = len(header) + (len(footer) + 1 if footer is not None else 0)
    group = []
    length = frame
    for row in rows:
        if group and (length + len(row) + 1 > size or len(group) == max_rows):
            yield "\n".join([header, *group] + ([footer] if footer is not None else []))
            group = []
            length = frame
        group.append(row)
        length += len(row) + 1
    if group:
        yield "\n".join([header, *group] + ([footer] if footer is not None else []))


def _units(blocks: list[tuple[str, bool]]) -> Iterator[tuple[int, int, bool]]:
    """Offsets of whole paragraphs, headings and fenced code in the joined section."""
    offset = 0
    paragraph = None
    for raw, atomic in blocks:
        if atomic or not raw.strip():
            if paragraph is not None:
                yield paragraph, offset - 1, False
                paragraph = None
            if atomic:
                yield offset, offset + len(raw), True
        elif paragraph is None:
            paragraph = offset
        offset += len(raw) + 1
    if paragraph is not None:
        yield paragraph, offset - 1, False


class MarkdownStructuredSplitter:
    def __init__(self, chunk_size: int, chunk_overlap: int, table_max_rows_per_chunk: int = 10) -> None:
        self.plain = CharacterTextSplitter(chunk_size, chunk_overlap)
        if table_max_rows_per_chunk < 1:
            raise ValueError("table_max_rows_per_chunk must be positive")
        self.size = chunk_size
        self.overlap = chunk_overlap
        self.table_rows = table_max_rows_per_chunk

    def split(self, text: str, *, plain_text: bool = False) -> list[SplitPiece]:
        if plain_text:
            return [SplitPiece(part, {"heading_path": None, "chunk_type": "section"}) for part in self.plain.split(text)]
        lines = text.removeprefix("\ufeff").splitlines()
        pieces: list[SplitPiece] = []
        section: list[tuple[str, bool]] = []
        parent = path = None
        pending_heading = None

        def flush() -> None:
            nonlocal pending_heading
            if any(raw.strip() for raw, _ in section):
                if pending_heading is not None:
                    section.insert(0, (pending_heading, True))
                for part in self._section_texts(section):
                    pieces.append(SplitPiece(part, {"heading_path": path, "chunk_type": "section"}))
            section.clear()
            pending_heading = None

        i = 0
        while i < len(lines):
            line = lines[i]
            i += 1
            marker = _fence_marker(line)
            if marker:
                close = re.compile(r" {0,3}" + marker[0] + "{" + str(len(marker)) + r",}[ \t]*")
                start = i - 1
                while i < len(lines) and not close.fullmatch(lines[i]):
                    i += 1
                body_end = i
                footer = marker
                if i < len(lines):
                    footer = lines[i]
                    i += 1
                code = "\n".join(lines[start:i])
                if len(code) <= self.size or body_end == start + 1:
                    section.append((code, True))
                else:
                    flush()
                    rows = lines[start + 1:body_end]
                    for part in _groups(line, rows, footer, self.size):
                        pieces.append(SplitPiece(part, {"heading_path": path, "chunk_type": "section"}))
                continue

            heading = _HEADING.fullmatch(line)
            if heading:
                if len(heading[1]) > 2:
                    section.append((line, True))
                    continue
                flush()
                title = re.sub(r"(?<!\S)#+$", "", (heading[2] or "").strip()).rstrip()
                if heading[1] == "#":
                    parent = title
                    path = title or None
                else:
                    path = "/".join(filter(None, (parent, title))) or None
                pending_heading = line
                continue
            if line.strip() == "---":
                continue
            end = _table_end(lines, i - 1)
            if end > i:
                rows = lines[i - 1:end]
                header = 1 if _delimiter(rows[0]) else 0
                first_data = header + 1
                if first_data < len(rows) and _delimiter(rows[first_data]):
                    first_data += 1
                if first_data < len(rows):
                    flush()
                    table = "\n".join(rows)
                    parts = (table,) if len(table) <= self.size else _groups(
                        "【表头】" + " | ".join(_cells(rows[header])), rows[first_data:], None, self.size, self.table_rows
                    )
                    for part, content in enumerate(parts, 1):
                        pieces.append(SplitPiece(content, {"heading_path": path, "chunk_type": "table", "table_part": part}))
                else:
                    section.extend((row, False) for row in rows)
                i = end
                continue
            section.append((line, False))

        # A document containing just a heading is still indexable.
        if not pieces and pending_heading and not any(raw.strip() for raw, _ in section):
            section = [(pending_heading, True)]
            pending_heading = None
        flush()
        return pieces

    def _section_texts(self, blocks: list[tuple[str, bool]]) -> Iterator[str]:
        text = "\n".join(raw for raw, _ in blocks)
        if len(text) <= self.size:
            yield text.strip("\n")
            return
        left = right = overlap_floor = 0
        for start, end, atomic in _units(blocks):
            long_prose = not atomic and end - start > self.size
            if left == right:
                left = start
            elif end - left > self.size:
                # A heading can share its first chunk with the start of a long paragraph.
                append_prefix = long_prose and start == right + 1 and start < left + self.size
                if not append_prefix:
                    if text[left:right].strip():
                        yield text[left:right]
                    # Retain only the overlap that leaves room for the entire next unit.
                    carry = max(left, right - self.overlap, overlap_floor, end - self.size)
                    left = carry if carry < right and text[carry:right].strip() else start
            while long_prose and end - left > self.size:
                stop = left + self.size
                part = text[left:stop]
                if part.strip():
                    yield part
                left = max(stop - self.overlap, start)
            right = end
            if atomic:
                overlap_floor = end
        if text[left:right].strip():
            yield text[left:right]
