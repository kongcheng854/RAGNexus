"""Offline checks for the versioned Golden Set format. Uses only the standard library."""

import json
import re
from collections import Counter
from pathlib import Path
from uuid import UUID

BASIC_VERSION = "golden_basic_20"
BASIC_MIX = {"direct_fact": 10, "paraphrase": 5, "multi_document": 5}
BASIC_SIZE = sum(BASIC_MIX.values())
ID_PATTERN = re.compile(r"[a-z0-9_]+")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}:{exc.lineno}:{exc.colno}: {exc.msg}") from None


def is_golden(data):
    return isinstance(data, dict) and (
        bool({"schema_version", "knowledge_bases", "suites"} & data.keys())
        or data.get("name") == "rag_center_golden"
        or str(data.get("version", "")).startswith("golden_")
    )


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def check_object(value, path, fields, errors):
    if not isinstance(value, dict):
        errors.append(f"{path}: 需要 JSON 对象")
        return {}
    for field in value:
        if field not in fields:
            errors.append(f"{path}.{field}: schema_version 1.0 未定义此字段")
    return value


def require_strings(obj, path, fields, errors):
    for field in fields:
        if not nonblank(obj.get(field)):
            errors.append(f"{path}.{field}: 需要非空字符串")


def check_knowledge_bases(value, errors):
    path = "$.knowledge_bases"
    if not isinstance(value, dict) or not value:
        errors.append(f"{path}: 需要非空的知识库映射")
        return {}
    if "ecommerce" not in value:
        errors.append(f"{path}.ecommerce: 缺少基础题的电商知识库")
    for alias, entry in value.items():
        base = f"{path}.{alias}"
        if not nonblank(alias):
            errors.append(f"{path}: 知识库别名不能为空")
        kb = check_object(entry, base, {"kb_id", "corpus_version"}, errors)
        require_strings(kb, base, ("corpus_version",), errors)
        try:
            if not isinstance(kb.get("kb_id"), str) or not UUID(kb["kb_id"]).int:
                raise ValueError
        except ValueError:
            errors.append(f"{base}.kb_id: 需要有效的非零 UUID")
    return value


def check_cases(value, kbs, suites, first_version, errors):
    by_id = {}
    if not isinstance(value, list):
        errors.append("$.cases: 需要题目数组")
        return by_id
    if first_version and len(value) != BASIC_SIZE:
        errors.append(f"$.cases: {BASIC_VERSION} 需要 {BASIC_SIZE} 道题，实际 {len(value)}")
    optional = ("expected_documents", "expected_headings", "tags")
    fields = {"id", "kb_alias", "suite", "case_type", "question", "ground_truth", *optional}
    for index, raw in enumerate(value):
        path = f"$.cases[{index}]"
        case = check_object(raw, path, fields, errors)
        require_strings(case, path, ("question", "ground_truth"), errors)
        case_id = case.get("id")
        if not isinstance(case_id, str) or not ID_PATTERN.fullmatch(case_id):
            errors.append(f"{path}.id: 只允许小写英文、数字和下划线")
        elif case_id in by_id:
            errors.append(f"{path}.id: 与 $.cases[{by_id[case_id][0]}].id 重复")
        else:
            by_id[case_id] = (index, case)
        alias = case.get("kb_alias")
        suite = case.get("suite")
        if not nonblank(alias) or alias not in kbs:
            errors.append(f"{path}.kb_alias: 未知知识库别名")
        if not nonblank(suite) or suite not in suites or suite == "all":
            errors.append(f"{path}.suite: 需要已定义的具体套件")
        if first_version and suite != "basic":
            errors.append(f"{path}.suite: {BASIC_VERSION} 中的题目必须属于 basic")
        if suite == "basic" and alias != "ecommerce":
            errors.append(f"{path}.kb_alias: basic 题目必须使用 ecommerce")
        if not isinstance(case.get("case_type"), str) or case["case_type"] not in BASIC_MIX:
            errors.append(f"{path}.case_type: 允许值为 {', '.join(BASIC_MIX)}")
        for field in optional:
            if field not in case:
                continue
            items = case[field]
            if not isinstance(items, list):
                errors.append(f"{path}.{field}: 需要字符串数组")
                continue
            for pos, item in enumerate(items):
                if not nonblank(item):
                    errors.append(f"{path}.{field}[{pos}]: 需要非空字符串")
    return by_id


def check_suites(suites, cases, errors):
    for required in ("basic", "all"):
        if required not in suites:
            errors.append(f"$.suites.{required}: 缺少套件")
    memberships = {}
    for name, raw in suites.items():
        path = f"$.suites.{name}"
        if not nonblank(name):
            errors.append("$.suites: 套件名不能为空")
        suite = check_object(raw, path, {"description", "case_ids"}, errors)
        require_strings(suite, path, ("description",), errors)
        ids = suite.get("case_ids")
        if ids == "all" and name == "all":
            continue
        path += ".case_ids"
        if not isinstance(ids, list):
            errors.append(f"{path}: 需要题目 ID 数组，all 套件也可以使用字符串 all")
            continue
        seen = memberships[name] = set()
        mix = Counter()
        for pos, case_id in enumerate(ids):
            location = f"{path}[{pos}]"
            if not nonblank(case_id) or case_id not in cases:
                errors.append(f"{location}: 引用的题目不存在")
                continue
            if case_id in seen:
                errors.append(f"{location}: 套件内重复引用 {case_id}")
                continue
            seen.add(case_id)
            case = cases[case_id][1]
            if name == "basic" and case.get("suite") != "basic":
                errors.append(f"{location}: 引用了不属于 basic 的题目")
            kind = case.get("case_type")
            if isinstance(kind, str):
                mix[kind] += 1
        if name == "all" and seen != cases.keys():
            errors.append(f"{path}: all 套件必须覆盖全部题目")
        if name == "basic":
            if len(seen) != BASIC_SIZE:
                errors.append(f"{path}: 需要 {BASIC_SIZE} 道不同的有效题目，实际 {len(seen)}")
            for kind, required in BASIC_MIX.items():
                if mix[kind] != required:
                    errors.append(f"{path}: {kind} 需要 {required} 道，实际 {mix[kind]}")
    for case_id, (index, case) in cases.items():
        suite = case.get("suite")
        if isinstance(suite, str) and suite in memberships and case_id not in memberships[suite]:
            errors.append(f"$.cases[{index}].suite: {suite} 未列入题目 {case_id}")


def validate_golden(data):
    errors = []
    fields = {"schema_version", "name", "version", "description", "knowledge_bases", "suites", "cases"}
    root = check_object(data, "$", fields, errors)
    require_strings(root, "$", ("name", "version", "description"), errors)
    if root.get("schema_version") != "1.0":
        errors.append("$.schema_version: 需要字符串 1.0")
    kbs = check_knowledge_bases(root.get("knowledge_bases"), errors)
    suites = root.get("suites")
    if not isinstance(suites, dict):
        errors.append("$.suites: 需要套件映射")
        suites = {}
    cases = check_cases(root.get("cases"), kbs, suites, root.get("version") == BASIC_VERSION, errors)
    check_suites(suites, cases, errors)
    if errors:
        raise ValueError("\n".join(errors))
