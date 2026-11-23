import unittest

from app.utils.markdown_splitter import MarkdownStructuredSplitter
from app.utils.text_splitter import CharacterTextSplitter


class MarkdownSplitterTests(unittest.TestCase):
    def test_heading_paths_siblings_and_separators(self):
        doc = "前言\r\n# 交易规则 ##\r\n## 退款\r\n七天\r\n---\r\n### 例外\r\n定制品\r\n## 运费\r\n包邮\r\n# 联系方式\r\n在线"
        pieces = MarkdownStructuredSplitter(80, 15).split(doc)
        self.assertEqual([p.metadata["heading_path"] for p in pieces], [None, "交易规则/退款", "交易规则/运费", "联系方式"])
        self.assertEqual([p.text for p in pieces], ["前言", "## 退款\n七天\n### 例外\n定制品", "## 运费\n包邮", "# 联系方式\n在线"])
        self.assertTrue(all(p.metadata["chunk_type"] == "section" for p in pieces))

    def test_long_section_prefers_paragraphs_then_overlaps_within_its_own_path(self):
        first = "abcdefghijklmnopqrstuv"
        pieces = MarkdownStructuredSplitter(12, 3).split(f"# A\n12345\n\n{first}\n## B\n尾")
        self.assertEqual(pieces[0].text, "# A\n12345")
        self.assertEqual([p.metadata["heading_path"] for p in pieces], ["A"] * (len(pieces) - 1) + ["A/B"])
        self.assertTrue(all(len(p.text) <= 12 for p in pieces))
        self.assertTrue(any(left.text[-3:] == right.text[:3] for left, right in zip(pieces[1:], pieces[2:])))
        self.assertEqual(pieces[-1].text, "## B\n尾")

    def test_overlap_shrinks_to_keep_whole_paragraphs_and_never_emits_only_old_text(self):
        doc = "# H\n11111\n\n22222222\n\n33333333"
        pieces = MarkdownStructuredSplitter(9, 7).split(doc)
        self.assertEqual([p.text for p in pieces], ["# H\n11111", "22222222", "33333333"])

    def test_no_headings_and_plain_text_compatibility(self):
        splitter = MarkdownStructuredSplitter(5, 2)
        self.assertEqual([p.text for p in splitter.split("abcdefgh")], ["abcde", "defgh"])
        doc = "# A\n---\n| a |\n| b |"
        self.assertEqual([p.text for p in splitter.split(doc, plain_text=True)], CharacterTextSplitter(5, 2).split(doc))
        self.assertEqual(splitter.split(" \n---\n "), [])
        self.assertEqual(splitter.split("# 只有标题")[0].text, "# 只有标题")

    def test_long_heading_and_deep_heading_are_never_cut(self):
        for heading in ("# " + "甲" * 22, "### " + "乙" * 22):
            pieces = MarkdownStructuredSplitter(12, 9).split("# parent\n正文\n" + heading + "\n结尾")
            self.assertEqual([p.text for p in pieces if "甲" in p.text or "乙" in p.text], [heading])

    def test_small_table_between_prose_keeps_original_rows_and_section_path(self):
        table = "| 类型 | 期限 |\n|:---|---:|\n| A | 7天 |\n| B | 3天 |"
        pieces = MarkdownStructuredSplitter(100, 15, 1).split("# 手册\n## 退款\n前言\n" + table + "\n注释")
        self.assertEqual([p.text for p in pieces], ["## 退款\n前言", table, "注释"])
        self.assertEqual([p.metadata["chunk_type"] for p in pieces], ["section", "table", "section"])
        self.assertEqual(pieces[1].metadata, {"heading_path": "手册/退款", "chunk_type": "table", "table_part": 1})
        self.assertNotIn("table_part", pieces[2].metadata)

    def test_large_table_has_complete_rows_one_header_per_group_and_resets_part_numbers(self):
        header = r"| 场景\|分类 | 说明 |"
        rows = [f"| {i} | 普通 |" for i in range(9)] + ["| 9 | " + "特" * 50 + " |"]
        doc = "\n".join([header, "|:--|--:|", *rows, "", "| 小 |", "| 值 |"])
        pieces = MarkdownStructuredSplitter(50, 7, 2).split(doc)
        big = pieces[:-1]
        self.assertEqual([p.metadata["table_part"] for p in big], list(range(1, len(big) + 1)))
        self.assertEqual(pieces[-1].metadata["table_part"], 1)
        bodies = [p.text.splitlines() for p in big]
        self.assertTrue(all(lines[0] == r"【表头】场景\|分类 | 说明" and 1 <= len(lines[1:]) <= 2 for lines in bodies))
        self.assertEqual([row for lines in bodies for row in lines[1:]], rows)
        self.assertTrue(all(len(p.text) <= 50 for p in big[:-1]))
        self.assertEqual(bodies[-1][1:], [rows[-1]])

    def test_table_variants_and_header_only_rows(self):
        splitter = MarkdownStructuredSplitter(100, 0)
        table = "项目 | 值\n:--- | ---:\n退款 | 7"
        pieces = splitter.split(table + "\n## 新章 | 后记\n结束")
        self.assertEqual(pieces[0].text, table)
        self.assertEqual(pieces[1].metadata["heading_path"], "新章 | 后记")
        for doc in ("| 单行 |", "| 只有表头 |\n|---|", "普通 a | b 句子\n另一个句子"):
            self.assertTrue(all(p.metadata["chunk_type"] == "section" for p in splitter.split(doc)))
        leading_delimiter = "|----|\n| H |\n" + "\n".join(["| DATA |"] * 5)
        self.assertTrue(all(p.text.startswith("【表头】H\n") for p in MarkdownStructuredSplitter(20, 0).split(leading_delimiter)))

    def test_placeholder_data_rows_and_inline_code_survive(self):
        doc = "\ufeff# 指南\n\x60\x60\x60字面值\x60\x60\x60\n## 章\n| A | B |\n|:-|--:|\nA | B\n| - | - |\n| C | D |"
        pieces = MarkdownStructuredSplitter(17, 2, 1).split(doc)
        tables = [p for p in pieces if p.metadata["chunk_type"] == "table"]
        self.assertEqual([p.text.splitlines()[1] for p in tables], ["A | B", "| - | - |", "| C | D |"])
        self.assertTrue(all(p.metadata["heading_path"] == "指南/章" for p in tables))

    def test_code_fences_protect_heading_table_and_separator_literals(self):
        code = "\x60\x60\x60\x60md\n## 示例\n---\n|A|\n|B|\n\x60\x60\x60\n\x60\x60\x60\x60"
        pieces = MarkdownStructuredSplitter(len(code) + 1, 4).split("# 文档\n前言\n" + code + "\n后记")
        self.assertIn(code, [p.text for p in pieces])
        self.assertTrue(all(p.metadata == {"heading_path": "文档", "chunk_type": "section"} for p in pieces))

    def test_oversized_fenced_code_repeats_fences_without_splitting_a_line(self):
        rows = ["  " + str(i) * 8 for i in range(5)] + ["x" * 50]
        doc = "~~~py\n" + "\n".join(rows) + "\n~~~"
        blocks = MarkdownStructuredSplitter(30, 4).split(doc)
        self.assertGreater(len(blocks), 1)
        contents = [p.text.splitlines() for p in blocks]
        self.assertTrue(all(lines[0] == "~~~py" and lines[-1] == "~~~" for lines in contents))
        self.assertEqual([line for lines in contents for line in lines[1:-1]], rows)
