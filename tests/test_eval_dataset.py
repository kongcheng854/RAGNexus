import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from scripts import run_eval
from scripts.golden_dataset import validate_golden


def example_dataset():
    cases = []
    for kind, count in (("direct_fact", 10), ("paraphrase", 5), ("multi_document", 5)):
        for index in range(count):
            case_id = f"{kind}_{index}"
            cases.append({"id": case_id, "suite": "basic", "kb_alias": "ecommerce", "case_type": kind,
                          "question": f"临时问题 {case_id}？", "ground_truth": f"临时答案 {case_id}。"})
    return {
        "schema_version": "1.0", "name": "rag_center_golden", "version": "golden_basic_20",
        "description": "临时测试数据", "knowledge_bases": {
            "ecommerce": {"kb_id": str(uuid4()), "corpus_version": "test_corpus"}},
        "suites": {"basic": {"description": "基础题", "case_ids": [case["id"] for case in cases]},
                   "all": {"description": "全部", "case_ids": "all"}},
        "cases": cases,
    }


class GoldenValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = example_dataset()

    def test_valid_baseline_cli_needs_only_standard_library(self):
        case = self.data["cases"][0]
        case.update(expected_documents=["test.md"], expected_headings=["第一节"], tags=["demo"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps(self.data), encoding="utf-8-sig")
            result = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(Path(run_eval.__file__)),
                                     "validate-dataset", "--dataset", str(path)],
                                    cwd=tmp, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("golden_basic_20", result.stdout)
        self.assertIn("20", result.stdout)

    def test_duplicate_id_blank_text_unknown_alias_and_type_listed_together(self):
        self.data["cases"][1].update(id=self.data["cases"][0]["id"], question=" ", ground_truth="\n",
                                     kb_alias="missing", case_type="typo")
        with self.assertRaises(ValueError) as ctx:
            validate_golden(self.data)
        for field in ("id", "question", "ground_truth", "kb_alias", "case_type"):
            self.assertIn(f"$.cases[1].{field}:", str(ctx.exception))

    def test_optional_fields_must_be_string_arrays(self):
        self.data["cases"][0].update(expected_documents="doc.md", expected_headings=[2], tags=None)
        with self.assertRaises(ValueError) as ctx:
            validate_golden(self.data)
        for field in ("expected_documents:", "expected_headings[0]:", "tags:"):
            self.assertIn("$.cases[0]." + field, str(ctx.exception))

    def test_suite_references_count_and_membership(self):
        ids = self.data["suites"]["basic"]["case_ids"]
        ids[0], ids[1] = "missing", ids[2]
        with self.assertRaises(ValueError) as ctx:
            validate_golden(self.data)
        for message in ("case_ids[0]: 引用的题目不存在", "case_ids[2]: 套件内重复", "实际 18",
                        "$.cases[0].suite:", "$.cases[1].suite:"):
            self.assertIn(message, str(ctx.exception))

    def test_basic_type_distribution(self):
        self.data["cases"][0]["case_type"] = "paraphrase"
        with self.assertRaises(ValueError) as ctx:
            validate_golden(self.data)
        self.assertIn("direct_fact 需要 10 道，实际 9", str(ctx.exception))
        self.assertIn("paraphrase 需要 5 道，实际 6", str(ctx.exception))

    def test_single_kb_route_and_version_boundaries(self):
        self.data["cases"][0].update(id="UPPER", kb_ids=[str(uuid4())], suite="all")
        self.data["knowledge_bases"]["ecommerce"].update(kb_id="placeholder", corpus_version="")
        self.data.update(api_key="sensitive-test-value")
        del self.data["version"]
        with self.assertRaises(ValueError) as ctx:
            validate_golden(self.data)
        for field in ("$.version", "$.knowledge_bases.ecommerce.kb_id", "$.knowledge_bases.ecommerce.corpus_version",
                      "$.cases[0].id", "$.cases[0].kb_ids", "$.cases[0].suite", "$.api_key"):
            self.assertIn(field + ":", str(ctx.exception))
        self.assertNotIn("sensitive-test-value", str(ctx.exception))

    def test_invalid_json_returns_line_and_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{\n]", encoding="utf-8")
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(run_eval.main(["validate-dataset", "--dataset", str(path)]), 1)
            self.assertIn(f"{path}:2:1", err.getvalue())


class GoldenScoringTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "golden.json"
        self.output = self.path.with_name("report.json")
        self.data = example_dataset()

    def save(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def test_suite_order_single_kb_routes_and_report_identity(self):
        from scripts import run_retrieval_eval as runner

        self.data["version"] = "extended_test"
        self.data["knowledge_bases"]["second"] = {"kb_id": str(uuid4()), "corpus_version": "other_corpus"}
        other = dict(self.data["cases"][0], id="another", suite="other_suite", kb_alias="second")
        self.data["cases"].append(other)
        self.data["suites"]["other_suite"] = {"description": "定序抽查", "case_ids": ["another", "direct_fact_1"]}
        self.save()
        fetch = AsyncMock(return_value=[])
        args = ["run", "--dataset", str(self.path), "--suite", "other_suite", "--api-key", "test",
                "--index-id", "index_a", "--settings-snapshot", "frozen-config", "--output", str(self.output)]
        with patch.object(runner, "retrieve", fetch), patch.object(runner, "make_judge", return_value=("test", None)), \
                patch.object(runner, "Settings"), redirect_stdout(io.StringIO()):
            self.assertEqual(run_eval.main(args), 0)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["dataset_version"], self.data["version"])
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["suite"], "other_suite")
        self.assertEqual(report["knowledge_bases"], self.data["knowledge_bases"])
        self.assertEqual((report["index_id"], report["settings_snapshot"]), ("index_a", "frozen-config"))
        self.assertEqual([r["id"] for r in report["cases"]], ["another", "direct_fact_1"])
        for call, row, alias in zip(fetch.await_args_list, report["cases"], ("second", "ecommerce")):
            kb = self.data["knowledge_bases"][alias]["kb_id"]
            self.assertEqual(tuple(map(str, call.args[1])), (kb,))
            self.assertEqual((row["kb_alias"], row["kb_ids"]), (alias, [kb]))

    def test_invalid_unselected_case_aborts_before_scoring_or_replacing_output(self):
        from scripts import run_retrieval_eval as runner

        self.data["suites"]["sample"] = {"description": "抽查", "case_ids": ["direct_fact_0"]}
        self.data["cases"][-1]["ground_truth"] = " "
        self.save()
        self.output.write_text("previous report", encoding="utf-8")
        args = ["--dataset", str(self.path), "--suite", "sample", "--output", str(self.output)]
        with patch.object(runner, "make_judge") as judge, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(args), 1)
            self.assertEqual(run_eval.main(["run", *args]), 1)
        self.assertIn("$.cases[19].ground_truth", err.getvalue())
        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous report")
        judge.assert_not_called()

    def test_feedback_export_and_merge_cannot_replace_formal_set(self):
        from scripts import export_eval_cases_from_langfuse as exporter

        self.save()
        original = self.path.read_bytes()
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(exporter.main(["--output", str(self.path)]), 1)
            self.assertEqual(exporter.main(["--merge", str(self.path), "--output", str(self.output)]), 1)
        self.assertIn("Golden Set", err.getvalue())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.output.exists())
