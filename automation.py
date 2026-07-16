#!/usr/bin/env python3
"""Local, evidence-first favorite research automation.

The pipeline never fetches remote content. It only transforms user-provided
JSON/JSONL captures into a review queue and, after explicit decisions, local
research notes and project briefs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "automation.json"
CATEGORIES = ("project", "tutorial", "reference", "tool")
PRIORITIES = ("P0", "P1", "P2", "P3")
TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "msclkid",
    "share_app_id",
    "share_link_id",
    "timestamp",
}


class PipelineError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError(f"文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"JSON 格式错误：{path}:{exc.lineno}: {exc.msg}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise PipelineError(f"收件箱不存在：{path}")
    if path.suffix.lower() == ".jsonl":
        records: list[dict[str, Any]] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PipelineError(f"JSONL 格式错误：{path}:{number}: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise PipelineError(f"JSONL 每行必须是对象：{path}:{number}")
            records.append(value)
        return records
    value = read_json(path)
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        value = value["items"]
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise PipelineError("JSON 输入必须是对象数组，或包含 items 数组的对象")
    return value


def canonicalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise PipelineError(f"source_url 必须是 http(s) 链接：{value!r}")
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_KEYS:
            continue
        query.append((key, item))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def content_fingerprint(item: dict[str, Any]) -> str:
    material = "|".join(
        [compact_text(item.get("title")).lower(), compact_text(item.get("notes")).lower()]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def stable_id(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:12]
    return f"fav-{digest}"


def validate_evidence(raw: Any, position: int) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PipelineError(f"第 {position} 条的 evidence 必须是数组")
    result = []
    for evidence_position, evidence in enumerate(raw, 1):
        if not isinstance(evidence, dict):
            raise PipelineError(f"第 {position} 条 evidence[{evidence_position}] 必须是对象")
        normalized = {
            "type": compact_text(evidence.get("type")),
            "claim": compact_text(evidence.get("claim")),
            "locator": compact_text(evidence.get("locator")),
            "strength": compact_text(evidence.get("strength") or "medium"),
            "quote": compact_text(evidence.get("quote")),
        }
        missing = [key for key in ("type", "claim", "locator") if not normalized[key]]
        if missing:
            raise PipelineError(
                f"第 {position} 条 evidence[{evidence_position}] 缺少：{', '.join(missing)}"
            )
        if normalized["strength"] not in {"low", "medium", "high"}:
            raise PipelineError(f"evidence strength 只能是 low/medium/high")
        result.append(normalized)
    return result


def normalize_item(raw: dict[str, Any], position: int) -> dict[str, Any]:
    title = compact_text(raw.get("title"))
    source_url = compact_text(raw.get("source_url"))
    if not title or not source_url:
        raise PipelineError(f"第 {position} 条必须包含非空 title 和 source_url")
    canonical_url = canonicalize_url(source_url)
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise PipelineError(f"第 {position} 条的 tags 必须是数组")
    category_hint = compact_text(raw.get("category_hint")).lower()
    if category_hint and category_hint not in CATEGORIES:
        raise PipelineError(f"第 {position} 条 category_hint 不在允许分类中：{category_hint}")
    item = {
        "item_id": stable_id(canonical_url),
        "title": title,
        "source_url": source_url,
        "canonical_source_url": canonical_url,
        "captured_at": compact_text(raw.get("captured_at")) or utc_now(),
        "notes": compact_text(raw.get("notes")),
        "tags": [compact_text(tag) for tag in tags if compact_text(tag)],
        "category_hint": category_hint,
        "evidence": validate_evidence(raw.get("evidence"), position),
    }
    item["content_fingerprint"] = content_fingerprint(item)
    return item


KEYWORDS = {
    "project": ("项目", "原型", "构建", "做一个", "开发", "mvp", "产品"),
    "tutorial": ("教程", "步骤", "入门", "教学", "怎么", "指南", "实战"),
    "reference": ("参考", "灵感", "视觉", "案例", "设计", "风格", "动效"),
    "tool": ("工具", "插件", "平台", "软件", "cli", "编辑器", "开源库"),
}


def classify(item: dict[str, Any]) -> tuple[str, float, list[str]]:
    hint = item.get("category_hint")
    if hint:
        return hint, 0.98, ["输入提供了 category_hint；仍需人工确认"]
    haystack = " ".join([item["title"], item["notes"], *item["tags"]]).lower()
    scores = {category: 0 for category in CATEGORIES}
    matches: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    for category, words in KEYWORDS.items():
        for word in words:
            if word in haystack:
                scores[category] += 1
                matches[category].append(word)
    category = max(CATEGORIES, key=lambda candidate: (scores[candidate], -CATEGORIES.index(candidate)))
    if scores[category] == 0:
        return "reference", 0.35, ["未命中稳定关键词，保守归入 reference"]
    confidence = min(0.55 + scores[category] * 0.1, 0.9)
    return category, confidence, [f"命中关键词：{', '.join(matches[category])}"]


def suggest_priority(item: dict[str, Any], category: str) -> tuple[str, list[str]]:
    reasons = []
    score = 0
    if category == "project":
        score += 2
        reasons.append("项目型收藏可形成明确产出")
    if len(item["evidence"]) >= 2:
        score += 1
        reasons.append("包含至少两条本地证据")
    if item["notes"]:
        score += 1
        reasons.append("已有人工备注")
    priority = "P1" if score >= 4 else "P2" if score >= 2 else "P3"
    return priority, reasons or ["信息较少，默认低优先级"]


def load_config(path: Path) -> dict[str, Any]:
    config = read_json(path)
    if not isinstance(config, dict):
        raise PipelineError("配置文件顶层必须是对象")
    configured_categories = tuple(config.get("pipeline", {}).get("allowed_categories", []))
    if configured_categories and configured_categories != CATEGORIES:
        raise PipelineError(f"allowed_categories 必须严格为：{', '.join(CATEGORIES)}")
    if config.get("pipeline", {}).get("require_human_confirmation") is not True:
        raise PipelineError("安全边界要求 require_human_confirmation=true")
    if config.get("research", {}).get("remote_fetch") is not False:
        raise PipelineError("安全边界要求 research.remote_fetch=false")
    if config.get("schedule", {}).get("enabled") is not False:
        raise PipelineError("原型不得启用真实调度：schedule.enabled 必须为 false")
    return config


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def portable_path(path: Path) -> str:
    """Return a stable audit label without leaking a user's home directory."""
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return f"external:{resolved.name}"


def prepare(input_path: Path, output_root: Path, run_id: str, config_path: Path) -> dict[str, Any]:
    load_config(config_path)
    run_dir = output_root / "runs" / run_id
    if run_dir.exists():
        raise PipelineError(f"run_id 已存在，请换一个 run_id：{run_dir}")

    raw_records = load_records(input_path)
    normalized = [normalize_item(raw, position) for position, raw in enumerate(raw_records, 1)]
    ledger_path = output_root / "state" / "dedup-index.json"
    ledger = read_json(ledger_path) if ledger_path.exists() else {"by_url": {}, "by_content": {}}
    by_url = dict(ledger.get("by_url", {}))
    by_content = dict(ledger.get("by_content", {}))
    unique: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    for item in normalized:
        duplicate_of = by_url.get(item["canonical_source_url"])
        match_type = "canonical_source_url"
        if not duplicate_of:
            duplicate_of = by_content.get(item["content_fingerprint"])
            match_type = "content_fingerprint"
        if duplicate_of:
            duplicates.append(
                {
                    "item_id": item["item_id"],
                    "title": item["title"],
                    "source_url": item["source_url"],
                    "duplicate_of": duplicate_of,
                    "matched_by": match_type,
                }
            )
            continue
        unique.append(item)
        by_url[item["canonical_source_url"]] = item["item_id"]
        by_content[item["content_fingerprint"]] = item["item_id"]

    review_queue = []
    for item in unique:
        category, confidence, classification_reasons = classify(item)
        priority, priority_reasons = suggest_priority(item, category)
        review_queue.append(
            {
                **item,
                "classification": {
                    "suggested_category": category,
                    "confidence": confidence,
                    "reasons": classification_reasons,
                },
                "suggested_priority": priority,
                "priority_reasons": priority_reasons,
                "suggested_output_dir": f"categorized/{category}",
                "evidence_status": "present" if item["evidence"] else "missing",
                "review_status": "pending",
            }
        )

    created_at = utc_now()
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": created_at,
        "input_path": portable_path(input_path),
        "status": "awaiting_human_review",
        "network_access": "disabled_by_design",
        "counts": {
            "received": len(normalized),
            "unique": len(unique),
            "duplicates": len(duplicates),
            "pending_review": len(review_queue),
        },
    }
    write_json(run_dir / "inbox" / "normalized-items.json", normalized)
    write_json(run_dir / "duplicates" / "duplicate-items.json", duplicates)
    write_json(run_dir / "review" / "review-queue.json", review_queue)
    write_json(run_dir / "run-manifest.json", manifest)
    write_json(
        ledger_path,
        {"schema_version": 1, "updated_at": created_at, "by_url": by_url, "by_content": by_content},
    )
    return {"run_dir": str(run_dir), **manifest["counts"], "status": manifest["status"]}


def load_decisions(path: Path) -> dict[str, dict[str, Any]]:
    value = read_json(path)
    if isinstance(value, dict):
        value = value.get("decisions")
    if not isinstance(value, list):
        raise PipelineError("decisions 文件必须包含 decisions 数组")
    result = {}
    for position, decision in enumerate(value, 1):
        if not isinstance(decision, dict):
            raise PipelineError(f"第 {position} 条 decision 必须是对象")
        item_id = compact_text(decision.get("item_id"))
        action = compact_text(decision.get("action")).lower()
        reviewer = compact_text(decision.get("reviewer"))
        decided_at = compact_text(decision.get("decided_at"))
        if not item_id or action not in {"approve", "edit", "reject"} or not reviewer or not decided_at:
            raise PipelineError(
                f"第 {position} 条 decision 需要 item_id、approve/edit/reject、reviewer、decided_at"
            )
        if item_id in result:
            raise PipelineError(f"decision 重复 item_id：{item_id}")
        result[item_id] = decision
    return result


def apply_decision(item: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    overrides = decision.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise PipelineError(f"{item['item_id']} 的 overrides 必须是对象")
    category = compact_text(overrides.get("category") or item["classification"]["suggested_category"]).lower()
    priority = compact_text(overrides.get("priority") or item["suggested_priority"]).upper()
    title = compact_text(overrides.get("title") or item["title"])
    if category not in CATEGORIES:
        raise PipelineError(f"确认后的 category 非法：{category}")
    if priority not in PRIORITIES:
        raise PipelineError(f"确认后的 priority 非法：{priority}")
    return {
        **item,
        "title": title,
        "category": category,
        "priority": priority,
        "output_dir": f"categorized/{category}",
        "review_status": "approved",
        "review": {
            "action": compact_text(decision["action"]).lower(),
            "reviewer": compact_text(decision["reviewer"]),
            "decided_at": compact_text(decision["decided_at"]),
            "comment": compact_text(decision.get("comment")),
        },
    }


def md_cell(value: Any) -> str:
    return compact_text(value).replace("|", "\\|")


def research_markdown(item: dict[str, Any]) -> str:
    evidence_rows = []
    for evidence in item["evidence"]:
        evidence_rows.append(
            f"| {md_cell(evidence['claim'])} | {md_cell(evidence['type'])} | "
            f"{md_cell(evidence['locator'])} | {md_cell(evidence['strength'])} |"
        )
    if not evidence_rows:
        evidence_rows.append("| 暂无证据；发布前必须补充 | evidence_gap | manual_input | low |")
    return f"""# {item['title']}

- 分类：`{item['category']}`
- 优先级：`{item['priority']}`
- 来源：[原始收藏]({item['source_url']})
- 收藏时间：{item['captured_at']}
- 人工确认：{item['review']['reviewer']} / {item['review']['decided_at']}
- 研究模式：仅使用本地输入证据；未进行远端抓取

## 保存理由

{item['notes'] or '输入未提供备注。'}

## 证据表

| 可支持的判断 | 证据类型 | 定位 | 强度 |
|---|---|---|---|
{chr(10).join(evidence_rows)}

## 证据边界

以上内容只证明人工输入中明确记录的观察。没有证据支持的功能、作者身份、实现细节与效果均视为未知，不得补写成事实。

## 下一步

1. 打开来源链接进行人工复核（本工具不会代为访问）。
2. 补充可定位的截图、原话或操作观察。
3. 若分类或优先级变化，更新确认记录后重新发布到新 run。
"""


def project_brief_markdown(item: dict[str, Any]) -> str:
    claims = [evidence["claim"] for evidence in item["evidence"]]
    evidence_summary = "；".join(claims) if claims else "暂无足够证据，需先补证。"
    return f"""# 项目 Brief：{item['title']}

## 元信息

- Item ID：`{item['item_id']}`
- 优先级：`{item['priority']}`
- 来源：[原始收藏]({item['source_url']})
- 输出目录：`{item['output_dir']}`
- 人工确认：{item['review']['reviewer']} / {item['review']['decided_at']}

## 一句话目标

把“{item['title']}”转成一个可验证的小型本地原型，先验证核心工作流，再决定是否扩展。

## 已知依据

{evidence_summary}

## 第一版范围

1. 只实现人工已确认的最小主流程。
2. 保存来源链接、证据、确认人、优先级与输出位置。
3. 给出可以本地重复执行的验收步骤。

## 明确不做

- 不从来源账号静默抓取数据。
- 不登录或修改任何远端账号。
- 不把缺少证据的推测包装成已确认事实。
- 不自动发布、购买、发信或创建真实调度。

## 验收标准

- [ ] 核心流程可在本地用固定样例重复运行。
- [ ] 每个关键结论能回指来源链接或具体证据定位。
- [ ] 人工可以修改分类/优先级，或拒绝该条收藏。
- [ ] 缺失证据与未决问题被明确展示。

## 建议下一步

先安排一次 30 分钟人工复核：确认用户、输入、输出和成功标准，再把结果拆成不超过 3 天的 MVP。
"""


def index_markdown(approved: list[dict[str, Any]], rejected: list[dict[str, Any]], pending: list[dict[str, Any]]) -> str:
    lines = ["# 收藏研究清单", "", "本清单只包含经过人工决策的本地输入，不代表已核验远端内容。", ""]
    for category in CATEGORIES:
        lines.extend([f"## {category}", ""])
        matches = [item for item in approved if item["category"] == category]
        if not matches:
            lines.append("- 暂无")
        for item in sorted(matches, key=lambda candidate: (PRIORITIES.index(candidate["priority"]), candidate["title"])):
            brief = f"；[项目 brief](briefs/{item['item_id']}.md)" if category == "project" else ""
            lines.append(
                f"- **{item['priority']}** [{item['title']}]({item['source_url']}) — "
                f"[研究记录](research/{item['item_id']}.md){brief}"
            )
        lines.append("")
    lines.extend(["## 本轮状态", "", f"- 已批准：{len(approved)}", f"- 已拒绝：{len(rejected)}", f"- 待确认：{len(pending)}"])
    return "\n".join(lines)


def publish(run_dir: Path, decisions_path: Path) -> dict[str, Any]:
    manifest_path = run_dir / "run-manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("status") == "published":
        raise PipelineError("该 run 已发布；为保留审计记录，请创建新的 run")
    queue = read_json(run_dir / "review" / "review-queue.json")
    decisions = load_decisions(decisions_path)
    known_ids = {item["item_id"] for item in queue}
    unknown = sorted(set(decisions) - known_ids)
    if unknown:
        raise PipelineError(f"decisions 包含不在队列中的 item_id：{', '.join(unknown)}")

    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    resolved_queue: list[dict[str, Any]] = []
    for item in queue:
        decision = decisions.get(item["item_id"])
        if not decision:
            pending.append(item)
            resolved_queue.append(item)
            continue
        action = compact_text(decision["action"]).lower()
        if action == "reject":
            rejected_item = {
                "item_id": item["item_id"],
                "title": item["title"],
                "source_url": item["source_url"],
                "review_status": "rejected",
                "review": {
                    "reviewer": compact_text(decision["reviewer"]),
                    "decided_at": compact_text(decision["decided_at"]),
                    "comment": compact_text(decision.get("comment")),
                },
            }
            rejected.append(rejected_item)
            resolved_queue.append({**item, **rejected_item})
            continue
        approved_item = apply_decision(item, decision)
        approved.append(approved_item)
        resolved_queue.append(approved_item)

    for item in approved:
        write_json(run_dir / item["output_dir"] / f"{item['item_id']}.json", item)
        write_text(run_dir / "research" / f"{item['item_id']}.md", research_markdown(item))
        if item["category"] == "project":
            write_text(run_dir / "briefs" / f"{item['item_id']}.md", project_brief_markdown(item))
    write_json(run_dir / "review" / "review-queue.json", resolved_queue)
    write_json(
        run_dir / "review" / "applied-decisions.json",
        {"source": portable_path(decisions_path), "decisions": list(decisions.values())},
    )
    write_json(run_dir / "rejected" / "rejected-items.json", rejected)
    write_text(run_dir / "index.md", index_markdown(approved, rejected, pending))

    manifest["status"] = "published" if not pending else "published_with_pending_review"
    manifest["published_at"] = utc_now()
    manifest["counts"].update(
        {"approved": len(approved), "rejected": len(rejected), "pending_review": len(pending), "briefs": sum(item["category"] == "project" for item in approved)}
    )
    write_json(manifest_path, manifest)
    return {"run_dir": str(run_dir), "status": manifest["status"], **manifest["counts"]}


def verify(run_dir: Path, write_report: bool = True) -> dict[str, Any]:
    manifest = read_json(run_dir / "run-manifest.json")
    queue = read_json(run_dir / "review" / "review-queue.json")
    errors: list[str] = []
    approved = [item for item in queue if item.get("review_status") == "approved"]
    for item in approved:
        for key in ("source_url", "evidence", "priority", "output_dir", "review"):
            if key not in item:
                errors.append(f"{item.get('item_id')}: 缺少 {key}")
        category = item.get("category")
        if category not in CATEGORIES:
            errors.append(f"{item.get('item_id')}: 非法分类 {category}")
            continue
        categorized = run_dir / "categorized" / category / f"{item['item_id']}.json"
        research = run_dir / "research" / f"{item['item_id']}.md"
        if not categorized.exists():
            errors.append(f"{item['item_id']}: 缺少分类产物")
        if not research.exists():
            errors.append(f"{item['item_id']}: 缺少研究记录")
        if category == "project" and not (run_dir / "briefs" / f"{item['item_id']}.md").exists():
            errors.append(f"{item['item_id']}: 缺少项目 brief")
    result = {
        "checked_at": utc_now(),
        "run_id": manifest.get("run_id"),
        "status": "pass" if not errors else "fail",
        "checks": {"approved_items": len(approved), "errors": errors},
    }
    if write_report:
        write_json(run_dir / "verification-report.json", result)
    return result


def settings_from_args(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    base = ROOT
    input_value = args.input or config["pipeline"]["input_path"]
    output_value = args.output or config["pipeline"]["output_root"]
    return resolve_path(input_value, base), resolve_path(output_value, base), config_path


def default_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地收藏研究自动化（无远端抓取）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--config", default=str(DEFAULT_CONFIG))
        subparser.add_argument("--input", help="JSON/JSONL 收件箱；覆盖配置")
        subparser.add_argument("--output", help="输出根目录；覆盖配置")
        subparser.add_argument("--run-id", default=default_run_id())

    prepare_parser = subparsers.add_parser("prepare", help="去重、分类并生成待确认队列")
    common(prepare_parser)

    publish_parser = subparsers.add_parser("publish", help="应用人工 decisions 后生成研究与 brief")
    publish_parser.add_argument("--run-dir", required=True)
    publish_parser.add_argument("--decisions", required=True)

    run_parser = subparsers.add_parser("run", help="手动完整跑通 prepare + publish + verify")
    common(run_parser)
    run_parser.add_argument("--decisions", required=True)

    verify_parser = subparsers.add_parser("verify", help="验证已生成产物")
    verify_parser.add_argument("--run-dir", required=True)

    config_parser = subparsers.add_parser("check-config", help="验证调度配置的安全边界")
    config_parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            input_path, output_root, config_path = settings_from_args(args)
            result = prepare(input_path, output_root, args.run_id, config_path)
        elif args.command == "publish":
            result = publish(Path(args.run_dir).resolve(), Path(args.decisions).resolve())
        elif args.command == "run":
            input_path, output_root, config_path = settings_from_args(args)
            prepared = prepare(input_path, output_root, args.run_id, config_path)
            result = publish(Path(prepared["run_dir"]), Path(args.decisions).resolve())
            result["verification"] = verify(Path(prepared["run_dir"]))
            if result["verification"]["status"] != "pass":
                raise PipelineError("产物验证失败")
        elif args.command == "verify":
            result = verify(Path(args.run_dir).resolve())
            if result["status"] != "pass":
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
        else:
            config = load_config(Path(args.config).resolve())
            result = {
                "status": "pass",
                "schedule_enabled": config["schedule"]["enabled"],
                "schedule_mode": config["schedule"]["mode"],
                "remote_fetch": config["research"]["remote_fetch"],
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except PipelineError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
