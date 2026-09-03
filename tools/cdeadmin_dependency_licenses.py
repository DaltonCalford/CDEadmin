#!/usr/bin/env python3
"""Inventory licenses for installed CDEadmin Python and Node dependencies.

Run this program with the Python interpreter used by pgAdmin. Node package
metadata is read from explicitly supplied installed dependency roots. The
output is evidence only and must be outside every inspected dependency root.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
from collections import Counter
from pathlib import Path


def normalized_license(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return normalized_license(
            value.get("type") or value.get("name") or ""
        )
    if isinstance(value, list):
        licenses = (normalized_license(item) for item in value)
        return " OR ".join(filter(None, licenses))
    return ""


def python_rows() -> list[dict[str, str]]:
    rows = []
    for distribution in importlib.metadata.distributions():
        metadata = distribution.metadata
        classifiers = metadata.get_all("Classifier") or []
        license_classifiers = sorted(
            item for item in classifiers if item.startswith("License ::")
        )
        expression = metadata.get("License-Expression", "")
        declared = normalized_license(metadata.get("License", ""))
        rows.append(
            {
                "ecosystem": "python",
                "name": metadata.get("Name", distribution.name),
                "version": distribution.version,
                "declared_license": expression or declared,
                "license_expression": expression,
                "license_classifiers": " | ".join(license_classifiers),
                "homepage": metadata.get("Home-page", ""),
                "metadata_source": str(distribution.locate_file("")),
            }
        )
    return rows


def node_rows(roots: list[Path]) -> list[dict[str, str]]:
    rows = {}
    for root in roots:
        if not root.is_dir():
            raise SystemExit(f"Node dependency root does not exist: {root}")
        for path in sorted(root.rglob("package.json")):
            try:
                package = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(package, dict):
                continue
            name = package.get("name")
            version = package.get("version")
            if not name or not version:
                continue
            repository = package.get("repository", "")
            if isinstance(repository, dict):
                repository = repository.get("url", "")
            key = (str(name), str(version))
            rows[key] = {
                "ecosystem": "node",
                "name": str(name),
                "version": str(version),
                "declared_license": normalized_license(
                    package.get("license", "")
                ),
                "license_expression": "",
                "license_classifiers": "",
                "homepage": str(package.get("homepage") or repository or ""),
                "metadata_source": str(path),
            }
    return list(rows.values())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_inventory(output: Path, rows: list[dict[str, str]]) -> None:
    fields = [
        "ecosystem",
        "name",
        "version",
        "declared_license",
        "license_expression",
        "license_classifiers",
        "homepage",
        "metadata_source",
    ]
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            sorted(
                rows,
                key=lambda row: (
                    row["ecosystem"],
                    row["name"].lower(),
                    row["version"],
                ),
            )
        )


def capture(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    roots = [Path(value).resolve() for value in args.node_root]
    if any(output == root or root in output.parents for root in roots):
        raise SystemExit(
            "output must be outside installed Node dependency roots"
        )
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()) and not args.overwrite:
        raise SystemExit("output directory is not empty; pass --overwrite")
    if args.overwrite:
        for path in output.iterdir():
            if not path.is_file():
                raise SystemExit(
                    f"refusing to remove output subdirectory: {path}"
                )
            path.unlink()

    rows = python_rows() + node_rows(roots)
    inventory = output / "installed_dependency_license_inventory.csv"
    write_inventory(inventory, rows)
    missing = [
        row
        for row in rows
        if not (
            row["declared_license"] or row["license_classifiers"]
        )
    ]
    counts = Counter(row["ecosystem"] for row in rows)
    summary = {
        "schema": "cdeadmin.installed-dependency-licenses.v1",
        "baseline_id": args.baseline_id,
        "dependency_count": len(rows),
        "ecosystem_counts": dict(sorted(counts.items())),
        "missing_license_metadata": len(missing),
        "inventory_sha256": sha256_file(inventory),
        "qualification": (
            "Metadata inventory only; legal review remains required before "
            "redistribution."
        ),
    }
    (output / "installed_dependency_license_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--node-root", action="append", default=[])
    parser.add_argument("--baseline-id", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    return capture(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
