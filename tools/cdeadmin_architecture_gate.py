#!/usr/bin/env python3
"""Enforce additive CDEadmin multi-engine architecture boundaries."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path
from typing import Iterable


SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
BRANCH_SUBJECT = re.compile(
    r"(?:engine|provider|dialect|experience|adapter|target)", re.IGNORECASE
)
JS_LITERAL_BRANCH = re.compile(
    r"\b(?P<subject>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*"
    r"(?:===|!==|==|!=)\s*['\"][^'\"]+['\"]"
)
JS_SWITCH = re.compile(
    r"\bswitch\s*\(\s*(?P<subject>[^)]+)\)\s*\{(?P<body>.*?)\}",
    re.DOTALL,
)
JS_PROVIDER_IMPORT = re.compile(
    r"(?:import\s+.*?\s+from\s+|require\s*\()(?P<quote>['\"])"
    r"(?P<target>[^'\"]*providers[^'\"]*)(?P=quote)"
)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def source_paths(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in SOURCE_SUFFIXES:
            excluded = {"node_modules", "__pycache__"}
            if not any(part in excluded for part in path.parts):
                yield path


def token_count(path: Path, token: str) -> int:
    try:
        with path.open("rb") as stream:
            tokens = tokenize.tokenize(stream.readline)
            return sum(
                item.type == tokenize.NAME and item.string == token
                for item in tokens
            )
    except (OSError, SyntaxError, tokenize.TokenError):
        return 0


def contains_position(node: ast.AST, line: int, column: int) -> bool:
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        return False
    starts_before = (node.lineno, node.col_offset) <= (line, column)
    ends_after = (line, column) < (node.end_lineno, node.end_col_offset)
    return starts_before and ends_after


def statement_scope(
    statement: ast.stmt, parents: dict[ast.AST, ast.AST]
) -> str:
    names = []
    node: ast.AST | None = statement
    while node in parents:
        node = parents[node]
        definition_types = (
            ast.ClassDef,
            ast.FunctionDef,
            ast.AsyncFunctionDef,
        )
        if isinstance(node, definition_types):
            names.append(node.name)
    return ".".join(reversed(names)) or "<module>"


def occurrence_fingerprint(
    statement: ast.stmt, parents: dict[ast.AST, ast.AST]
) -> str:
    payload = "|".join(
        (
            statement_scope(statement, parents),
            statement.__class__.__name__,
            ast.dump(statement, include_attributes=False),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_occurrence_fingerprints(path: Path, token: str) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        positions = [
            item.start
            for item in tokens
            if item.type == tokenize.NAME and item.string == token
        ]
    except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError):
        return []
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    statements = [
        node for node in ast.walk(tree) if isinstance(node, ast.stmt)
    ]
    fingerprints = []
    for line, column in positions:
        candidates = [
            node
            for node in statements
            if contains_position(node, line, column)
        ]
        if not candidates:
            continue
        statement = min(
            candidates,
            key=lambda node: (
                node.end_lineno - node.lineno,
                node.end_col_offset - node.col_offset,
            ),
        )
        fingerprints.append(occurrence_fingerprint(statement, parents))
    return sorted(fingerprints)


def legacy_driver_occurrences(
    source: Path, policy: dict
) -> dict[str, list[str]]:
    legacy = policy["legacy_global_driver"]
    root = source / legacy["root"]
    if not root.is_dir():
        return {}
    rows = {}
    for path in sorted(root.rglob("*.py")):
        if any(part in {"node_modules", "__pycache__"} for part in path.parts):
            continue
        fingerprints = file_occurrence_fingerprints(path, legacy["token"])
        if fingerprints:
            rows[path.relative_to(source).as_posix()] = fingerprints
    return rows


def legacy_driver_counts(source: Path, policy: dict) -> dict[str, int]:
    legacy = policy["legacy_global_driver"]
    root = source / legacy["root"]
    if not root.is_dir():
        return {}
    rows = {}
    for path in sorted(root.rglob("*.py")):
        if any(part in {"node_modules", "__pycache__"} for part in path.parts):
            continue
        count = token_count(path, legacy["token"])
        if count:
            rows[path.relative_to(source).as_posix()] = count
    return rows


def violation(rule: str, path: str, detail: str) -> dict[str, str]:
    return {"rule": rule, "path": path, "detail": detail}


def scan_legacy_driver(source: Path, policy: dict) -> tuple[list[dict], dict]:
    current = legacy_driver_counts(source, policy)
    current_occurrences = legacy_driver_occurrences(source, policy)
    legacy = policy["legacy_global_driver"]
    allowed = legacy.get("maximum_by_file", {})
    allowed_occurrences = legacy.get("occurrence_fingerprints_by_file", {})
    violations = []
    if legacy.get("baseline_total") != sum(allowed.values()):
        violations.append(
            violation(
                "global-driver-policy-integrity",
                "tools/cdeadmin_architecture_policy.json",
                "baseline_total does not match maximum_by_file",
            )
        )
    if allowed_occurrences:
        fingerprint_total = sum(map(len, allowed_occurrences.values()))
        fingerprint_counts = {
            path: len(values) for path, values in allowed_occurrences.items()
        }
        if fingerprint_total != legacy.get("baseline_total") or (
            fingerprint_counts != allowed
        ):
            violations.append(
                violation(
                    "global-driver-policy-integrity",
                    "tools/cdeadmin_architecture_policy.json",
                    "occurrence fingerprints do not match frozen counts",
                )
            )
    for path, count in current.items():
        maximum = allowed.get(path)
        if maximum is None:
            violations.append(
                violation(
                    "no-new-global-driver-file",
                    path,
                    f"{legacy['token']} is not present in the frozen "
                    "inventory",
                )
            )
        elif count > maximum:
            violations.append(
                violation(
                    "global-driver-ratchet",
                    path,
                    f"count {count} exceeds frozen maximum {maximum}",
                )
            )
        elif allowed_occurrences:
            allowed_fingerprints = Counter(allowed_occurrences[path])
            current_fingerprints = Counter(current_occurrences.get(path, []))
            if current_fingerprints - allowed_fingerprints:
                violations.append(
                    violation(
                        "global-driver-new-occurrence",
                        path,
                        "a use moved or changed outside the frozen statements",
                    )
                )
    baseline_total = sum(allowed.values())
    current_total = sum(current.values())
    inventory = {
        "baseline_files": len(allowed),
        "current_files": len(current),
        "baseline_total": baseline_total,
        "current_total": current_total,
        "removed": baseline_total - current_total,
    }
    return violations, inventory


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def string_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def branch_subject(node: ast.AST) -> bool:
    return bool(BRANCH_SUBJECT.search(dotted_name(node)))


def collection_has_string(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return False
    return any(string_literal(child) for child in node.elts)


def python_engine_branches(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            values = [node.left, *node.comparators]
            if any(branch_subject(item) for item in values) and any(
                string_literal(item) or collection_has_string(item)
                for item in values
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.Match) and branch_subject(node.subject):
            if any(
                isinstance(case.pattern, ast.MatchValue) and string_literal(
                    case.pattern.value
                )
                for case in node.cases
            ):
                lines.append(node.lineno)
    return sorted(set(lines))


def python_imports(path: Path) -> list[tuple[int, str, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                (node.lineno, alias.name, 0) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(
                (
                    node.lineno,
                    ".".join(filter(None, (module, alias.name))),
                    node.level,
                )
                for alias in node.names
            )
    return imports


def javascript_engine_branches(text: str) -> list[int]:
    offsets = []
    for match in JS_LITERAL_BRANCH.finditer(text):
        if BRANCH_SUBJECT.search(match.group("subject")):
            offsets.append(match.start())
    for match in JS_SWITCH.finditer(text):
        if BRANCH_SUBJECT.search(match.group("subject")) and re.search(
            r"\bcase\s+['\"][^'\"]+['\"]\s*:", match.group("body")
        ):
            offsets.append(match.start())
    return sorted({text.count("\n", 0, offset) + 1 for offset in offsets})


def configured_roots(source: Path, policy: dict, name: str) -> list[Path]:
    return [(source / value).resolve() for value in policy.get(name, [])]


def owning_provider(path: Path, provider_roots: list[Path]) -> str | None:
    for root in provider_roots:
        if is_relative_to(path, root):
            relative = path.relative_to(root)
            return relative.parts[0] if relative.parts else None
    return None


def is_test_path(path: Path, policy: dict) -> bool:
    markers = set(policy.get("test_path_parts", []))
    return bool(markers.intersection(path.parts))


def scan_common_and_providers(source: Path, policy: dict) -> list[dict]:
    common_roots = configured_roots(source, policy, "common_roots")
    provider_roots = configured_roots(source, policy, "provider_roots")
    provider_names = {
        path.name
        for root in provider_roots
        if root.is_dir()
        for path in root.iterdir()
        if path.is_dir()
    }
    violations = []
    for root in provider_roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name.lower().startswith("fixture"):
                violations.append(
                    violation(
                        "fixture-production-exclusion",
                        child.relative_to(source).as_posix(),
                        "fixture provider package is below a production root",
                    )
                )
    for path in source_paths(source):
        resolved = path.resolve()
        in_common = any(
            is_relative_to(resolved, root) for root in common_roots
        )
        owner = owning_provider(resolved, provider_roots)
        if not in_common and owner is None:
            continue
        relative = path.relative_to(source).as_posix()
        if owner and owner.lower().startswith("fixture") and not is_test_path(
            path, policy
        ):
            violations.append(
                violation(
                    "fixture-production-exclusion",
                    relative,
                    "fixture providers may exist only below test paths",
                )
            )
        text = path.read_text(encoding="utf-8", errors="replace")
        branch_lines = (
            python_engine_branches(path)
            if path.suffix == ".py"
            else javascript_engine_branches(text)
        )
        if in_common:
            for line in branch_lines:
                violations.append(
                    violation(
                        "no-common-engine-branch",
                        relative,
                        f"raw engine/provider branch at line {line}",
                    )
                )
        if path.suffix == ".py":
            imports = python_imports(path)
            for line, target, level in imports:
                if in_common and ".providers" in target:
                    violations.append(
                        violation(
                            "common-provider-import-boundary",
                            relative,
                            f"provider import {target!r} at line {line}",
                        )
                    )
                if owner and ".providers." in target:
                    imported = target.split(".providers.", 1)[1].split(
                        ".", 1
                    )[0]
                    if imported in provider_names and imported != owner:
                        violations.append(
                            violation(
                                "cross-provider-import-boundary",
                                relative,
                                f"provider {owner!r} imports {imported!r} "
                                f"at line {line}",
                            )
                        )
                relative_provider_import = (
                    level and target.split(".", 1)[0] in provider_names
                )
                if owner and relative_provider_import:
                    imported = target.split(".", 1)[0]
                    if imported != owner:
                        violations.append(
                            violation(
                                "cross-provider-import-boundary",
                                relative,
                                f"provider {owner!r} imports {imported!r} "
                                f"at line {line}",
                            )
                        )
        else:
            for match in JS_PROVIDER_IMPORT.finditer(text):
                target = match.group("target")
                line = text.count("\n", 0, match.start()) + 1
                if in_common:
                    violations.append(
                        violation(
                            "common-provider-import-boundary",
                            relative,
                            f"provider import {target!r} at line {line}",
                        )
                    )
                if owner:
                    candidates = [
                        part
                        for part in Path(target).parts
                        if part not in {".", "..", "providers"}
                    ]
                    provider_candidates = (
                        item for item in candidates if item in provider_names
                    )
                    imported = next(provider_candidates, None)
                    if imported and imported != owner:
                        violations.append(
                            violation(
                                "cross-provider-import-boundary",
                                relative,
                                f"provider {owner!r} imports {imported!r} "
                                f"at line {line}",
                            )
                        )
    return violations


def scan_support_manifests(source: Path, policy: dict) -> list[dict]:
    allowed = set(policy.get("support_claim_states", {}))
    violations = []
    for path in sorted(source.rglob("*.json")):
        if any(part in {"node_modules", ".git"} for part in path.parts):
            continue
        if path.name not in {"provider_manifest.json", "support_claims.json"}:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            violations.append(
                violation(
                    "support-manifest-json",
                    path.relative_to(source).as_posix(),
                    f"manifest cannot be read: {exc}",
                )
            )
            continue
        values = data if isinstance(data, list) else [data]
        for item in values:
            if not isinstance(item, dict) or "support_state" not in item:
                continue
            state = item["support_state"]
            if state not in allowed:
                violations.append(
                    violation(
                        "support-claim-vocabulary",
                        path.relative_to(source).as_posix(),
                        f"unknown support_state {state!r}",
                    )
                )
                continue
            evidence_states = {"implemented", "compatibility_mapped"}
            identity = item.get("identity", {})
            nested_evidence = (
                identity.get("evidence_reference")
                if isinstance(identity, dict) else None
            )
            evidence = item.get("evidence_reference") or nested_evidence
            if state in evidence_states and not evidence:
                violations.append(
                    violation(
                        "support-claim-evidence",
                        path.relative_to(source).as_posix(),
                        f"{state!r} requires evidence_reference",
                    )
                )
            disabled_states = {"deferred", "unsupported"}
            if state in disabled_states and item.get("enabled") is True:
                violations.append(
                    violation(
                        "support-claim-fail-closed",
                        path.relative_to(source).as_posix(),
                        f"{state!r} may not be enabled",
                    )
                )
            if item.get("fixture") is True and state == "implemented":
                violations.append(
                    violation(
                        "fixture-support-claim",
                        path.relative_to(source).as_posix(),
                        "fixture evidence cannot establish implemented "
                        "support",
                    )
                )
    return violations


def evaluate(source: Path, policy: dict) -> dict:
    violations, inventory = scan_legacy_driver(source, policy)
    violations.extend(scan_common_and_providers(source, policy))
    violations.extend(scan_support_manifests(source, policy))
    return {
        "schema": "cdeadmin.architecture-gate-result.v1",
        "baseline_id": policy["baseline_id"],
        "legacy_global_driver_inventory": inventory,
        "violation_count": len(violations),
        "violations": sorted(
            violations,
            key=lambda item: (item["rule"], item["path"], item["detail"]),
        ),
    }


def freeze_legacy(source: Path, policy_path: Path, policy: dict) -> None:
    counts = legacy_driver_counts(source, policy)
    occurrences = legacy_driver_occurrences(source, policy)
    legacy = policy["legacy_global_driver"]
    previous = legacy.get("maximum_by_file", {})
    if previous:
        new_files = sorted(set(counts) - set(previous))
        increases = sorted(
            path
            for path, count in counts.items()
            if count > previous.get(path, 0)
        )
        if new_files or increases:
            raise SystemExit(
                "legacy baseline may only shrink; remove new/increased uses "
                "before freezing"
            )
        previous_occurrences = legacy.get(
            "occurrence_fingerprints_by_file", {}
        )
        if previous_occurrences:
            def has_new_fingerprint(path: str, values: list[str]) -> bool:
                previous_values = previous_occurrences.get(path, [])
                return bool(Counter(values) - Counter(previous_values))

            changed = sorted(
                path
                for path, values in occurrences.items()
                if has_new_fingerprint(path, values)
            )
            if changed:
                raise SystemExit(
                    "legacy baseline may only remove frozen occurrences; "
                    "changed uses require migration away from the global "
                    "driver"
                )
    policy["legacy_global_driver"]["maximum_by_file"] = counts
    policy["legacy_global_driver"][
        "occurrence_fingerprints_by_file"
    ] = occurrences
    policy["legacy_global_driver"]["baseline_total"] = sum(counts.values())
    policy_path.write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--output")
    parser.add_argument(
        "--freeze-legacy",
        action="store_true",
        help="Replace the legacy ceiling with current token counts",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source = Path(args.source).resolve()
    policy_path = Path(args.policy).resolve()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if args.freeze_legacy:
        freeze_legacy(source, policy_path, policy)
        return 0
    result = evaluate(source, policy)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 1 if result["violation_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
