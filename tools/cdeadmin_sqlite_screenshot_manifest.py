#!/usr/bin/env python3
"""Index and verify embedded-provider task-form screenshot evidence.

The UI gates intentionally keep control values out of their reports.  This
collector preserves that rule while making every screenshot independently
auditable through a value-free occurrence record and the shared CSV manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MANIFEST_FIELDS = (
    "interface_id",
    "reference_version",
    "profile_id",
    "server_id",
    "database_target_id",
    "command_id",
    "form_id",
    "resource_kind",
    "resource_id",
    "state",
    "viewport",
    "device_scale",
    "font_scale",
    "theme",
    "locale",
    "screenshot_path",
    "occurrence_path",
    "interaction_result",
    "transaction_proof_id",
    "captured_at_utc",
)

EXPECTED_PROFILES = frozenset({
    "standard", "narrow", "wide", "high-contrast", "font-150",
})

SCREENSHOT_NAME = re.compile(
    r"^(?P<state>.+)-(?P<width>[0-9]+)x(?P<height>[0-9]+)-"
    r"(?P<presentation>default|high-contrast|scaled-font-[0-9]+)\.png$"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify and index embedded-provider UI evidence."
    )
    parser.add_argument(
        "--engine", choices=("sqlite", "duckdb"), default="sqlite"
    )
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _profile(path: Path) -> dict[str, Any]:
    match = SCREENSHOT_NAME.match(path.name)
    if match is None:
        raise ValueError(f"Unrecognised screenshot filename: {path}")
    width = int(match.group("width"))
    height = int(match.group("height"))
    presentation = match.group("presentation")
    if presentation == "high-contrast":
        profile_id = "high-contrast"
        theme = "high-contrast"
        font_scale = 100
    elif presentation.startswith("scaled-font-"):
        font_scale = int(presentation.rsplit("-", 1)[1])
        profile_id = f"font-{font_scale}"
        theme = "default"
    elif (width, height) == (800, 900):
        profile_id = "narrow"
        theme = "default"
        font_scale = 100
    elif (width, height) == (2560, 1080):
        profile_id = "wide"
        theme = "default"
        font_scale = 100
    else:
        profile_id = "standard"
        theme = "default"
        font_scale = 100
    filename_state = match.group("state").replace("-", "_")
    if filename_state == "database_properties":
        filename_state = "initial"
    return {
        "profile_id": profile_id,
        "viewport": f"{width}x{height}",
        "theme": theme,
        "font_scale": font_scale,
        "state": filename_state,
    }


def _screenshots(
    screenshots: dict[str, dict[str, str]],
) -> Iterable[tuple[str, Path, str]]:
    for state, evidence in sorted(screenshots.items()):
        if not evidence:
            continue
        yield state, Path(evidence["path"]), evidence["sha256"]


def _record(
    *,
    report_path: Path,
    report: dict[str, Any],
    item: dict[str, Any],
    state: str,
    screenshot: Path,
    expected_sha256: str,
    command_id: str,
    form_id: str,
    resource_kind: str,
    transaction_proof: bool,
) -> dict[str, str]:
    if not screenshot.is_file():
        raise FileNotFoundError(screenshot)
    observed_sha256 = _sha256(screenshot)
    if observed_sha256 != expected_sha256:
        raise ValueError(
            f"Screenshot digest mismatch for {screenshot}: "
            f"expected {expected_sha256}, observed {observed_sha256}"
        )
    profile = _profile(screenshot)
    if profile["state"] != state.replace("-", "_"):
        raise ValueError(
            f"Screenshot state mismatch for {screenshot}: {state}"
        )
    captured_at = report.get("captured_at") or datetime.fromtimestamp(
        screenshot.stat().st_mtime, tz=timezone.utc
    ).isoformat()
    occurrence_path = screenshot.with_suffix(".occurrence.json")
    occurrence = {
        "schema": "cdeadmin.ui-screenshot-occurrence.v1",
        "interface_id": report["interface_id"],
        "reference_version": report["reference_version"],
        "profile_id": profile["profile_id"],
        "command_id": command_id,
        "form_id": form_id,
        "resource_kind": resource_kind,
        "state": state,
        "viewport": profile["viewport"],
        "font_scale": profile["font_scale"],
        "theme": profile["theme"],
        "screenshot_sha256": observed_sha256,
        "source_report": str(report_path),
        "credential_values_exported": False,
        "control_values_recorded": False,
        "captured_at_utc": captured_at,
    }
    occurrence_path.write_text(
        json.dumps(occurrence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    completed = state == "completed"
    interaction_result = {
        "initial": "rendered",
        "cancelled": "keyboard_cancelled_without_execution",
        "validation_error": "invalid_input_rejected_without_execution",
        "plan_preview": "provider_plan_ready",
        "completed": "provider_operation_completed",
    }.get(state, "observed")
    default_database = (
        "cdeadmin_demo.duckdb"
        if report["interface_id"] == "duckdb-native"
        else "cdeadmin_demo.sqlite"
    )
    resource_id = (item.get("resource_id") or
                   report.get("database_label") or
                   default_database)
    return {
        "interface_id": report["interface_id"],
        "reference_version": report["reference_version"],
        "profile_id": profile["profile_id"],
        "server_id": report.get("server_label", "localhost"),
        "database_target_id": report.get(
            "database_label", default_database
        ),
        "command_id": command_id,
        "form_id": form_id,
        "resource_kind": resource_kind,
        "resource_id": str(resource_id),
        "state": state,
        "viewport": profile["viewport"],
        "device_scale": "1",
        "font_scale": str(profile["font_scale"]),
        "theme": profile["theme"],
        "locale": "en",
        "screenshot_path": str(screenshot),
        "occurrence_path": str(occurrence_path),
        "interaction_result": interaction_result,
        "transaction_proof_id": (
            f"{report_path}#{command_id}" if completed and transaction_proof
            else ""
        ),
        "captured_at_utc": captured_at,
    }


def _object_rows(
    report_path: Path, report: dict[str, Any]
) -> list[dict[str, str]]:
    rows = []
    for item in report["results"]:
        for state, screenshot, digest in _screenshots(item["screenshots"]):
            rows.append(_record(
                report_path=report_path,
                report=report,
                item=item,
                state=state,
                screenshot=screenshot,
                expected_sha256=digest,
                command_id=item["command_id"],
                form_id=item["form_id"],
                resource_kind=item["resource_kind"],
                transaction_proof=False,
            ))
    return rows


def _maintenance_rows(
    report_path: Path, report: dict[str, Any]
) -> list[dict[str, str]]:
    rows = []
    for item in report["forms"]:
        for state, screenshot, digest in _screenshots(item["screenshots"]):
            rows.append(_record(
                report_path=report_path,
                report=report,
                item=item,
                state=state,
                screenshot=screenshot,
                expected_sha256=digest,
                command_id=item["command_id"],
                form_id=item["form_id"],
                resource_kind="database",
                transaction_proof=True,
            ))
    return rows


def _lifecycle_rows(
    report_path: Path, report: dict[str, Any]
) -> list[dict[str, str]]:
    rows = []
    for item in report["rendered"]:
        for state, screenshot, digest in _screenshots(item["screenshots"]):
            rows.append(_record(
                report_path=report_path,
                report=report,
                item=item,
                state=state,
                screenshot=screenshot,
                expected_sha256=digest,
                command_id=item["command_id"],
                form_id=item["form_id"],
                resource_kind="database",
                transaction_proof=False,
            ))
    for item in report["completed"]:
        engine_id = report["engine_id"]
        command_id = f"database.{engine_id}.{item['mode']}"
        form_id = (
            f"cdeadmin.{report['interface_id']}.database."
            f"{item['mode']}.v1"
        )
        for state, screenshot, digest in _screenshots(item["screenshots"]):
            rows.append(_record(
                report_path=report_path,
                report=report,
                item=item,
                state=state,
                screenshot=screenshot,
                expected_sha256=digest,
                command_id=command_id,
                form_id=form_id,
                resource_kind="database",
                transaction_proof=True,
            ))
    return rows


def _property_rows(
    report_path: Path, report: dict[str, Any]
) -> list[dict[str, str]]:
    screenshot = report["screenshot"]
    return [_record(
        report_path=report_path,
        report=report,
        item={},
        state="initial",
        screenshot=Path(screenshot["path"]),
        expected_sha256=screenshot["sha256"],
        command_id=report["command_id"],
        form_id=(
            f"cdeadmin.{report['interface_id']}.database.properties.v1"
        ),
        resource_kind="database",
        transaction_proof=False,
    )]


def _report_paths(evidence_dir: Path, engine: str) -> list[Path]:
    patterns = (
        f"{engine}-object-form-gate*.json",
        f"{engine}-maintenance-ui-gate*.json",
        f"{engine}-database-lifecycle-ui-gate*.json",
        f"{engine}-lifecycle-ui-gate*.json",
        f"{engine}-properties-ui-gate*.json",
    )
    return sorted({
        path for pattern in patterns for path in evidence_dir.rglob(pattern)
    })


def _existing_other_rows(
    path: Path, interface_id: str, reference_version: str
) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return [
            row for row in csv.DictReader(stream)
            if not (
                row.get("interface_id") == interface_id and
                row.get("reference_version") == reference_version
            )
        ]


def main() -> int:
    args = _arguments()
    identity = {
        "sqlite": ("sqlite-native", "3.53.0"),
        "duckdb": ("duckdb-native", "1.5.2"),
    }
    interface_id, reference_version = identity[args.engine]
    rows: list[dict[str, str]] = []
    reports = _report_paths(args.evidence_dir, args.engine)
    if len(reports) != 20:
        raise RuntimeError(
            f"Expected 20 {args.engine} presentation reports, found "
            f"{len(reports)}"
        )
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("interface_id") != interface_id or (
                report.get("reference_version") != reference_version):
            raise ValueError(f"Unexpected provider identity in {report_path}")
        if report.get("credential_values_exported") is not False:
            raise ValueError(
                f"Unsafe credential evidence flag in {report_path}"
            )
        if not (
            report.get("complete") is True or
            report.get("passed") is True
        ):
            raise ValueError(f"Incomplete UI evidence report: {report_path}")
        if "object-form" in report_path.name:
            rows.extend(_object_rows(report_path, report))
        elif "maintenance" in report_path.name:
            rows.extend(_maintenance_rows(report_path, report))
        elif "lifecycle" in report_path.name:
            rows.extend(_lifecycle_rows(report_path, report))
        else:
            rows.extend(_property_rows(report_path, report))

    def identity(row: dict[str, str]) -> tuple[str, ...]:
        return tuple(row[field] for field in (
            "interface_id", "reference_version", "profile_id", "command_id",
            "form_id", "state", "screenshot_path",
        ))
    if len({identity(row) for row in rows}) != len(rows):
        raise ValueError(
            f"Duplicate {args.engine} screenshot occurrence identities"
        )
    all_rows = _existing_other_rows(
        args.manifest_output, interface_id, reference_version
    ) + rows
    all_rows.sort(key=lambda row: (
        row["interface_id"], row["reference_version"], row["profile_id"],
        row["command_id"], row["state"], row["screenshot_path"],
    ))
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest_output.open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["profile_id"]] = counts.get(row["profile_id"], 0) + 1
    if (
        frozenset(counts) != EXPECTED_PROFILES or
        len(set(counts.values())) != 1
    ):
        raise ValueError(
            f"{args.engine} presentation profiles are missing or contain "
            "unequal "
            f"screenshot coverage: {counts}"
        )
    summary = {
        "schema": f"cdeadmin.{args.engine}-screenshot-manifest.v1",
        "interface_id": interface_id,
        "reference_version": reference_version,
        "complete": True,
        "source_report_count": len(reports),
        "screenshot_occurrence_count": len(rows),
        "profile_counts": dict(sorted(counts.items())),
        "all_screenshot_digests_verified": True,
        "all_occurrence_records_value_free": True,
        "manifest_output": str(args.manifest_output),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
