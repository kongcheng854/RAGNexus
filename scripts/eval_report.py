"""Render the structured comparison without re-running a scorer or deciding gates."""

import json

from scripts.eval_dataset import write_json, write_text
from scripts.eval_metrics import CALLS, P95, QUALITY
from scripts.golden_dataset import read_json

TITLES = {"context_recall": "Context Recall", "context_precision": "Context Precision",
          P95: "P95 延迟", CALLS: "平均应用模型调用数"}
VERDICTS = {
    "effective": ("有效，建议启用", "建议在本实验固定的检索配置中启用。调整线上 Profile 前应验证对应组合。"),
    "effective_high_cost": ("有效但成本较高，建议按场景启用", "建议在质量优先的 Profile 或复杂问题场景中启用，并保留已测得的延迟和调用预算。"),
    "no_clear_benefit": ("无明显收益，暂不启用", "当前数据集上的收益未达到启用条件，建议保留 baseline。"),
    "regression": ("出现负向效果，建议回退", "建议回退到 baseline，优先复核下降题型及对应召回上下文。"),
    "execution_failed": ("评测执行失败，暂时不能下结论", "暂缓启用。修复缺失数据或执行异常后，在相同条件下复核两组。"),
}


def text(value):
    return " ".join(str(value).split()).replace("|", "\\|").replace("`", "'")


def fmt(value, places=3, scale=1, unit="", signed=False):
    if value is None:
        return "不可用"
    number = value * scale
    return format(number, ("+" if signed else "") + f".{places}f") + unit


def approved(value):
    return {True: "是", False: "否", None: "不可核实"}[value]


def metric_cells(c, key):
    before, after, d = c["baseline"][key], c["candidate"][key], c["delta"][key]
    checks, limits = c["checks"], c["thresholds"]
    if key in QUALITY:
        cells = [fmt(before), fmt(after), fmt(d, 1, 100, " 个百分点", True)]
        if key == c["primary_metric"]:
            threshold = fmt(limits["min_primary_improvement"], 1, 100, " 个百分点", True) + " 或以上"
            passed = approved(checks["primary"])
            if key in limits["quality_guardrails"]:
                threshold += "，同时满足护栏 " + fmt(limits["quality_guardrails"][key]["min_delta"], 1, 100, " 个百分点", True)
                passed += " / " + approved(checks["guardrails"][key])
            return cells + [threshold, passed]
        if key in limits["quality_guardrails"]:
            minimum = limits["quality_guardrails"][key]["min_delta"]
            return cells + [fmt(minimum, 1, 100, " 个百分点", True) + " 或以上", approved(checks["guardrails"][key])]
        return cells + ["未设单项门槛", "—"]
    if key == P95:
        return [fmt(before, 1, unit=" ms"), fmt(after, 1, unit=" ms"),
                fmt(d, 1, unit=" ms", signed=True) + " / " + fmt(c["delta"]["p95_increase_ratio"], 1, 100, "%", True),
                "增幅 ≤ " + fmt(limits["cost_limits"]["max_p95_increase_ratio"], 1, 100, "%"), approved(checks["p95"])]
    return [fmt(before, 2, unit=" 次"), fmt(after, 2, unit=" 次"), fmt(d, 2, unit=" 次", signed=True),
            "增量 ≤ " + fmt(limits["cost_limits"]["max_model_calls_increase"], 2, unit=" 次"),
            approved(checks["application_model_calls"])]


def render(c):
    primary, primary_name = c["primary_metric"], TITLES[c["primary_metric"]]
    keys = (*QUALITY, P95, CALLS)
    table = {k: metric_cells(c, k) for k in keys}
    lines = [f"# {text(c['title']).removesuffix('报告')}报告", "", "## 结论", "",
             f"**{c['verdict_text']}。**", "", f"数据集 `{text(c['dataset_version'])}`，套件 `{text(c['suite'])}`。"
             f"本次计划 {c['expected_cases']} 题，有 {c['paired_cases']} 题取得两组完整质量评分。", ""]
    for k in keys:
        b, a, _, limit, passed = table[k]
        d = c["delta"][k]
        if d is None:
            change = "变化不可用"
        elif k in QUALITY:
            change = ("提升" if d > 0 else "下降" if d < 0 else "变化") + " " + fmt(abs(d))
            change += "，即 " + fmt(abs(d), 1, 100, " 个百分点")
        elif k == P95:
            change = "耗时变化 " + fmt(d, 1, unit=" ms", signed=True)
            change += "，相对变化 " + fmt(c["delta"]["p95_increase_ratio"], 1, 100, "%", True)
        else:
            change = "每题平均变化 " + fmt(d, 2, unit=" 次", signed=True)
        gate = {"是": "已达标", "否": "未达标", "不可核实": "暂不可核实", "—": "仅供观察"}.get(passed, passed)
        lines.append(f"- {TITLES[k]} 从 {b} 到 {a}，{change}。{gate}（{limit}）。")
    if c["reasons"]:
        lines += ["", "。".join(map(text, c["reasons"])) + "。"]
    if c["verdict"] == "effective":
        lines += ["", "主指标达到预设门槛，至少一项质量指标改善，质量护栏和两项成本限制均通过。"]
    elif c["verdict"] == "effective_high_cost":
        exceeded = [name for key, name in (("p95", "P95 延迟增幅"), ("application_model_calls", "平均应用模型调用增量"))
                    if c["checks"][key] is False]
        lines += ["", "主指标达到预设门槛，质量护栏通过。" + "、".join(exceeded) + "超过成本限制。"]
    elif c["verdict"] == "no_clear_benefit":
        lines += ["", "主指标变化未达到预设提升门槛。" if not c["checks"]["primary"]
                  else "主指标达到零门槛，但主指标和已配置护栏均无正向变化。"]
    elif c["verdict"] == "regression":
        broken = [TITLES[m] for m, passed in c["checks"]["guardrails"].items() if passed is False]
        lines += ["", "、".join(broken) + " 突破质量护栏。" if broken else "主指标下降达到预设回退幅度。"]
    if c["thresholds"]["min_primary_improvement"] == 0:
        lines += ["", "零门槛用于核对主指标持平或提升。启用还要求主指标或已配置的护栏指标出现正向变化。"]
    lines += ["", c["recommendation"], "", "## 实验目标", "",
              f"比较“{text(c['labels']['baseline'])}”与“{text(c['labels']['candidate'])}”对 {primary_name} 的影响。"
              f"实验标识为 `{text(c['experiment_id'])}`。", "", "## 两组声明差异", "",
              "| 请求字段 | baseline | candidate |", "|---|---|---|"]
    for path, values in c["declared_differences"].items():
        lines.append(f"| `{text(path)}` | {text(values['baseline'])} | {text(values['candidate'])} |")
    env = c["conditions"]
    run, judge = env["run_config"], env["judge"]
    lines += ["", "## 数据集、知识库、索引与运行条件", "",
              f"- 运行 `{text(c['run_id'])}`，评分批次 `{text(c.get('score_id') or '未生成')}`。",
              f"- 业务并发 {run.get('concurrency', '未知')}，每组预热上限 {run.get('warmup_cases', '未知')} 题，"
              f"业务超时 {run.get('timeout_seconds', '未知')} 秒。",
              f"- Judge 模型 {text(judge.get('model') or '未调用或未知')}，温度 {judge.get('temperature', '未知')}，"
              f"超时 {judge.get('timeout_seconds', '未知')} 秒，并发 {judge.get('max_workers', '未知')}。"]
    for alias, kb in env["knowledge_bases"].items():
        server = env["server_environments"].get(kb["kb_id"], {})
        index = env["index_id"] or server.get("index") or "未知"
        lines.append(f"- `{text(alias)}` 使用知识库 `{text(kb['kb_id'])}`，语料版本 `{text(kb['corpus_version'])}`，"
                     f"索引 `{text(json.dumps(index, ensure_ascii=False) if isinstance(index, dict) else index)}`，"
                     f"Embedding 模型 {text(server.get('embedding_model') or '未知')}。")
    lines += ["", "## 公共指标对比", "",
              "| 指标 | baseline | candidate | 变化 | 门槛 | 是否通过 |", "|---|---:|---:|---:|---|---|"]
    lines.extend("| " + " | ".join([TITLES[k], *table[k]]) + " |" for k in keys)
    lines += ["", "表中质量变化为两组有效评分均值之差。"]
    for g in ("baseline", "candidate"):
        n = c[g]["quality_samples"]
        lines.append(f"{g} 的 Recall 使用 {n['context_recall']} 题，Precision 使用 {n['context_precision']} 题。")
    lines += ["", "预设门槛理由为" + text(c["thresholds"].get("rationale") or "历史配置未记录理由") +
              f" 正式失败题上限为 {c['thresholds']['max_failed_cases']}。", "", "## 分题型结果"]
    for part in c["slices"]:
        lines += ["", f"### {text(part['field'])} · {text(part['value'])}", ""]
        if agg := part["aggregate"]:
            lines.append(f"配对 {part['paired_cases']} 题。")
            for k in QUALITY:
                lines.append(f"{TITLES[k]} 从 {fmt(agg['baseline'][k])} 到 {fmt(agg['candidate'][k])}，"
                             f"变化 {fmt(agg['delta'][k], 1, 100, ' 个百分点', True)}。")
            if part["breached_guardrails"]:
                lines.append("此切片突破质量护栏：" + "、".join(TITLES[k] for k in part["breached_guardrails"]) + "。")
        else:
            lines += [f"有效配对 {part['paired_cases']} 题，少于 3 题，仅列逐题分数，不能据此得出统计结论。", ""]
            for i in part["case_ids"]:
                row = c["cases"][i]
                scores = "，".join(f"{TITLES[k]} {fmt(row['baseline'][k])} → {fmt(row['candidate'][k])}" for k in QUALITY)
                lines.append(f"- `{text(i)}`　{scores}。")
    lines += ["", "## 改善与退化最多的题目"]
    for key, heading in (("most_improved", "改善最多"), ("most_regressed", "退化最多")):
        lines += ["", f"### {heading}", ""]
        if not c[key]:
            lines.append("没有符合条件的配对题目。")
        for i in c[key]:
            row = c["cases"][i]
            lines.append(f"- `{text(i)}`　{text(row['question'])}　{primary_name} "
                         f"{fmt(row['delta'][primary], 1, 100, ' 个百分点', True)}。")
            for g, ev in row["evidence"].items():
                if ev:
                    location = " / ".join(text(v) for v in (ev["document"], ev["heading"], ev["source"]) if v)
                    lines.append(f"  - {g} 第 {ev['rank']} 条 {location}　{text(ev['excerpt'])}")
                else:
                    lines.append(f"  - {g} 无可用上下文摘要。")
    lines += ["", "## 失败、降级与不可用字段", ""]
    for item in c["diagnostics"]:
        lines.append(f"- {item['group']} / `{text(item['case_id'])}`　{'，'.join(map(text, item['issues']))}。")
    if not c["diagnostics"]:
        lines.append("。".join(map(text, c["reasons"])) + "。" if c["reasons"] else "两组均无逐题异常。")
    lines += ["", "## 成本分析", "",
              "延迟采用正式成功请求的客户端耗时，P50 和 P95 线性插值。应用模型调用数统计改写和重排，排除预热、Embedding 和 Judge。", "",
              "| 组 | 成功请求 | 失败请求 | 延迟样本 | 平均 ms | P50 ms | P95 ms | 最大 ms | 计数样本 | 平均调用数 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for g in ("baseline", "candidate"):
        s = c[g]
        cells = [g, str(s["successful_requests"]), str(s["failed_requests"]), str(s["latency_samples"])]
        cells += [fmt(s[k], 1) for k in ("mean_latency_ms", "p50_latency_ms", P95, "max_latency_ms")]
        lines.append("| " + " | ".join(cells + [str(s["model_call_samples"]), fmt(s[CALLS], 2)]) + " |")
    if any(c[g].get("request_count_source") == "manifest" for g in ("baseline", "candidate")):
        lines += ["", "缺少可核验逐题指标的组，其成功和失败请求数取自运行清单。"]
    if any(c[g]["model_call_samples"] < c["expected_cases"] for g in ("baseline", "candidate")):
        lines += ["", "部分题目调用数不可用。表中部分均值仅供诊断。"]
    lines += ["", "## 启用或回退建议", "", c["recommendation"], "", "## 原始结果文件位置", ""]
    for name, path in c["artifacts"].items():
        lines.append(f"- [{name}]({path})" if c["artifact_available"][name] else f"- `{path}`　文件不可用")
    return "\n".join(lines) + "\n"


def publish(output, comparison, source):
    verdict = comparison["verdict"]
    comparison["verdict_text"], comparison["recommendation"] = VERDICTS[verdict]
    # Existing readers of the chapter 02 comparison use this coarse status.
    comparison["status"] = {"execution_failed": "证据不足", "effective": "达到门槛"}.get(verdict, "未达到门槛")
    prefix = "" if output == source else "../"
    names = ("baseline.raw.jsonl", "candidate.raw.jsonl", "dataset.snapshot.json", "experiment.snapshot.json")
    comparison["artifacts"] = {name: prefix + name for name in names}
    for name in ("manifest.json", "baseline.metrics.json", "candidate.metrics.json", "comparison.json"):
        comparison["artifacts"][name] = name
    comparison["artifact_available"] = {name: name == "comparison.json" or (output / path).is_file()
                                        for name, path in comparison["artifacts"].items()}
    path = output / "comparison.json"
    write_json(path, comparison)
    write_text(output / "report.md", render(read_json(path)))
