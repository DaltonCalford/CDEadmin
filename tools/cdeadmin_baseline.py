#!/usr/bin/env python3
"""Capture a deterministic, read-only CDEadmin/pgAdmin baseline inventory.

The source tree is never written. All generated evidence is placed in an
explicit output directory, which must be outside the source tree.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
}

PACKAGE_SURFACES = (
    ("desktop-electron", "runtime/package.json", "runtime/src/js/cdeadmin.js"),
    ("server-wsgi", "web/pgAdmin4.wsgi", "web/pgAdmin4.py"),
    ("container", "Dockerfile", "pkg/docker/entrypoint.sh"),
    ("helm", "pkg/helm/Chart.yaml", "pkg/helm/templates/deployment.yaml"),
    ("pip", "pkg/pip/build.sh", "pkg/pip/setup_pip.py"),
    ("source", "pkg/src/build.sh", "Makefile"),
    ("debian", "pkg/debian/build.sh", "pkg/debian/control.in"),
    ("rpm", "pkg/redhat/build.sh", "pkg/redhat/pgadmin4.spec"),
    ("windows", "pkg/win32/installer.iss.in", "pkg/win32/README.md"),
    ("macos", "pkg/mac/build.sh", "pkg/mac/Info.plist.in"),
)

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|token)\b\s*[:=]\s*[^,\s]{8,}"
    ),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_command(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as exc:
        return 127, str(exc)
    return completed.returncode, completed.stdout.strip()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def source_files(source: Path) -> list[Path]:
    files = []
    for path in source.rglob("*"):
        if not path.is_file() or any(
            part in EXCLUDED_PARTS for part in path.parts
        ):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(source).as_posix())


def literal(node: ast.AST | None) -> object | None:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        try:
            return ast.unparse(node)
        except (AttributeError, ValueError):
            return None


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def python_tree(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fieldnames, extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def collect_routes(source: Path) -> list[dict]:
    routes = []
    root = source / "web" / "pgadmin"
    if not root.exists():
        return routes
    for path in sorted(root.rglob("*.py")):
        tree = python_tree(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                decorator_name = dotted_name(decorator.func)
                if not decorator_name.endswith(".route"):
                    continue
                route = literal(decorator.args[0]) if decorator.args else None
                keywords = {
                    item.arg: literal(item.value)
                    for item in decorator.keywords
                }
                methods = keywords.get("methods") or ["GET"]
                if isinstance(methods, str):
                    methods = [methods]
                if not isinstance(methods, (list, tuple, set)):
                    methods = [str(methods)]
                routes.append(
                    {
                        "module": path.relative_to(source).as_posix(),
                        "search_key": f"def {node.name}",
                        "blueprint": decorator_name.rsplit(".route", 1)[0],
                        "route": str(route),
                        "methods": "|".join(
                            sorted(str(item) for item in methods)
                        ),
                        "endpoint": str(keywords.get("endpoint") or node.name),
                    }
                )
    return sorted(
        routes,
        key=lambda row: (row["module"], row["route"], row["methods"]),
    )


def collect_migrations(source: Path) -> list[dict]:
    migrations = []
    root = source / "web" / "migrations" / "versions"
    if not root.exists():
        return migrations
    for path in sorted(root.glob("*.py")):
        tree = python_tree(path)
        if tree is None:
            continue
        assignments = {}
        functions = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "revision",
                        "down_revision",
                        "branch_labels",
                        "depends_on",
                    }:
                        assignments[target.id] = literal(node.value)
        migrations.append(
            {
                "module": path.relative_to(source).as_posix(),
                "search_key": f"revision = {assignments.get('revision')!r}",
                "revision": assignments.get("revision"),
                "down_revision": assignments.get("down_revision"),
                "has_upgrade": "upgrade" in functions,
                "has_downgrade": "downgrade" in functions,
            }
        )
    return sorted(migrations, key=lambda row: str(row["revision"]))


def normalized_requirement(line: str) -> str:
    value = line.strip()
    if not value or value.startswith("#"):
        return ""
    return value.split(" #", 1)[0].strip()


def collect_python_requirements(source: Path) -> list[dict]:
    rows = []
    seen = set()

    def read_requirements(path: Path, requested_by: str = "") -> None:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            return
        seen.add(resolved)
        for raw in path.read_text(encoding="utf-8").splitlines():
            item = normalized_requirement(raw)
            if not item:
                continue
            if item.startswith(("-r ", "--requirement ")):
                nested = item.split(maxsplit=1)[1]
                read_requirements(
                    path.parent / nested,
                    path.relative_to(source).as_posix(),
                )
                continue
            if item.startswith("-"):
                continue
            rows.append(
                {
                    "ecosystem": "python",
                    "scope": path.relative_to(source).as_posix(),
                    "name": re.split(r"[<>=!~;\[]", item, maxsplit=1)[0],
                    "declared": item,
                    "kind": "requirement",
                    "declared_license": "unresolved",
                    "requested_by": requested_by,
                }
            )

    for path in sorted(source.glob("**/requirements*.txt")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        read_requirements(path)
    return rows


def collect_node_dependencies(source: Path) -> list[dict]:
    rows = []
    for path in sorted(source.glob("**/package.json")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        scope = path.relative_to(source).as_posix()
        for kind in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
        ):
            for name, declared in sorted(data.get(kind, {}).items()):
                rows.append(
                    {
                        "ecosystem": "node",
                        "scope": scope,
                        "name": name,
                        "declared": declared,
                        "kind": kind,
                        "declared_license": "unresolved",
                        "requested_by": data.get("name", ""),
                    }
                )
    return rows


def collect_branding(source: Path) -> list[dict]:
    rows = []
    for relative in ("web/branding.py", "web/version.py"):
        path = source / relative
        tree = python_tree(path) if path.is_file() else None
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            value = literal(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith(
                    "APP_"
                ):
                    rows.append(
                        {
                            "category": "application_constant",
                            "source": relative,
                            "search_key": f"{target.id} =",
                            "name": target.id,
                            "value": value,
                            "sha256": "",
                        }
                    )
    for pattern in ("LICENSE*", "COPYRIGHT*", "NOTICE*"):
        for path in sorted(source.glob(pattern)):
            if path.is_file():
                rows.append(
                    {
                        "category": "legal_file",
                        "source": path.relative_to(source).as_posix(),
                        "search_key": path.name,
                        "name": path.name,
                        "value": "present",
                        "sha256": sha256_file(path),
                    }
                )
    for relative in (
        "web/package.json",
        "runtime/package.json",
        "pkg/helm/Chart.yaml",
    ):
        path = source / relative
        if path.is_file():
            rows.append(
                {
                    "category": "delivery_metadata",
                    "source": relative,
                    "search_key": path.name,
                    "name": relative,
                    "value": "present",
                    "sha256": sha256_file(path),
                }
            )
    return sorted(
        rows,
        key=lambda row: (row["category"], row["source"], row["name"]),
    )


def collect_external_urls(source: Path) -> list[dict]:
    rows = []
    candidates = [
        source / "web" / "config.py",
        source / "web" / "branding.py",
        source / "runtime" / "package.json",
        source / "web" / "package.json",
        source / "pkg" / "helm" / "Chart.yaml",
    ]
    pattern = re.compile(r"https?://[^\s'\"<>]+")
    for path in candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for url in sorted(set(pattern.findall(text))):
            rows.append(
                {
                    "source": path.relative_to(source).as_posix(),
                    "search_key": url,
                    "url": url.rstrip(",);]"),
                    "classification": (
                        "source_declared_external_service_or_metadata"
                    ),
                }
            )
    return rows


def collect_source_metrics(source: Path, files: list[Path]) -> list[dict]:
    suffixes = Counter(path.suffix.lower() or "[no_suffix]" for path in files)
    metrics = [
        {"metric": "source_files", "value": len(files)},
        {
            "metric": "source_bytes",
            "value": sum(path.stat().st_size for path in files),
        },
        {
            "metric": "python_test_files",
            "value": len(list((source / "web").glob("**/tests/**/*.py"))),
        },
        {
            "metric": "javascript_regression_files",
            "value": len(
                [
                    path
                    for path in (
                        source / "web" / "regression" / "javascript"
                    ).glob("**/*")
                    if path.suffix in {".js", ".jsx", ".ts", ".tsx"}
                ]
            ),
        },
        {
            "metric": "feature_test_files",
            "value": len(
                list(
                    (
                        source / "web" / "regression" / "feature_tests"
                    ).glob("*.py")
                )
            ),
        },
        {
            "metric": "sql_template_files",
            "value": len(
                [
                    path
                    for path in (source / "web" / "pgadmin").glob(
                        "**/templates/**/*"
                    )
                    if path.is_file()
                ]
            ),
        },
    ]
    metrics.extend(
        {"metric": f"files_suffix_{suffix}", "value": count}
        for suffix, count in sorted(suffixes.items())
    )
    return metrics


def read_app_version(source: Path) -> str:
    path = source / "web" / "version.py"
    tree = python_tree(path) if path.is_file() else None
    values = {}
    if tree:
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "APP_RELEASE",
                        "APP_REVISION",
                        "APP_SUFFIX",
                    }:
                        values[target.id] = literal(node.value)
    version = (
        f"{values.get('APP_RELEASE', 'unknown')}."
        f"{values.get('APP_REVISION', 'unknown')}"
    )
    if values.get("APP_SUFFIX"):
        version = f"{version}-{values['APP_SUFFIX']}"
    return version


def git_metadata(source: Path, requested_revision: str | None) -> dict:
    code, top = run_command(["git", "rev-parse", "--show-toplevel"], source)
    has_git = code == 0 and Path(top).resolve() == source
    revision = requested_revision or "unavailable"
    status = "not_a_git_checkout"
    if has_git:
        _, resolved = run_command(
            ["git", "rev-parse", requested_revision or "HEAD"], source
        )
        revision = resolved or revision
        _, porcelain = run_command(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            source,
        )
        status = "clean" if not porcelain else "dirty"
    return {"revision": revision, "git_status": status, "has_git": has_git}


def write_hash_inventory(source: Path, output: Path, files: list[Path]) -> str:
    rows = []
    root_digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(source).as_posix()
        digest = sha256_file(path)
        rows.append(
            {
                "sha256": digest,
                "bytes": path.stat().st_size,
                "path": relative,
            }
        )
        root_digest.update(relative.encode("utf-8"))
        root_digest.update(b"\0")
        root_digest.update(digest.encode("ascii"))
        root_digest.update(b"\n")
    write_csv(
        output / "source_file_hashes.csv",
        ["sha256", "bytes", "path"],
        rows,
    )
    return root_digest.hexdigest()


def scan_evidence_for_secrets(output: Path) -> list[dict]:
    findings = []
    for path in sorted(output.iterdir()):
        if not path.is_file() or path.name in {
            "evidence_manifest.sha256",
            "secret_scan.json",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for index, pattern in enumerate(SECRET_PATTERNS, start=1):
            if pattern.search(text):
                findings.append(
                    {
                        "file": path.name,
                        "pattern": f"secret_pattern_{index}",
                    }
                )
    return findings


def write_evidence_manifest(output: Path) -> None:
    rows = []
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "evidence_manifest.sha256":
            rows.append(f"{sha256_file(path)}  {path.name}")
    (output / "evidence_manifest.sha256").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )


def capture(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if not source.is_dir():
        raise SystemExit(f"source directory does not exist: {source}")
    if output == source or is_relative_to(output, source):
        raise SystemExit("output must be outside the source tree")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()) and not args.overwrite:
        raise SystemExit(
            "output directory is not empty; pass --overwrite to replace "
            "generated files"
        )
    if args.overwrite:
        for path in output.iterdir():
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                raise SystemExit(
                    f"refusing to remove output subdirectory: {path}"
                )

    git = git_metadata(source, args.revision)
    if args.require_clean and git["git_status"] != "clean":
        raise SystemExit(
            f"clean git checkout required; status={git['git_status']}"
        )

    files = source_files(source)
    version = read_app_version(source)
    captured_at = args.captured_at or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()
    short_revision = git["revision"][:12]
    baseline_id = args.baseline_id or f"pgadmin4-{version}-{short_revision}"
    source_digest = write_hash_inventory(source, output, files)

    metadata = {
        "schema": "cdeadmin.pgadmin-baseline.v1",
        "baseline_id": baseline_id,
        "captured_at": captured_at,
        "application_version": version,
        "source_revision": git["revision"],
        "source_git_status": git["git_status"],
        "source_tree_sha256": source_digest,
        "source_file_count": len(files),
        "capture_tool": "tools/cdeadmin_baseline.py",
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    (output / "baseline_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    routes = collect_routes(source)
    write_csv(
        output / "api_route_inventory.csv",
        ["module", "search_key", "blueprint", "route", "methods", "endpoint"],
        routes,
    )
    migrations = collect_migrations(source)
    write_csv(
        output / "migration_inventory.csv",
        [
            "module",
            "search_key",
            "revision",
            "down_revision",
            "has_upgrade",
            "has_downgrade",
        ],
        migrations,
    )
    dependencies = collect_python_requirements(
        source
    ) + collect_node_dependencies(source)
    write_csv(
        output / "declared_dependency_inventory.csv",
        [
            "ecosystem",
            "scope",
            "name",
            "declared",
            "kind",
            "declared_license",
            "requested_by",
        ],
        sorted(
            dependencies,
            key=lambda row: (
                row["ecosystem"],
                row["scope"],
                row["kind"],
                row["name"],
            ),
        ),
    )
    write_csv(
        output / "branding_provenance_inventory.csv",
        ["category", "source", "search_key", "name", "value", "sha256"],
        collect_branding(source),
    )
    write_csv(
        output / "external_service_inventory.csv",
        ["source", "search_key", "url", "classification"],
        collect_external_urls(source),
    )
    write_csv(
        output / "source_metrics.csv",
        ["metric", "value"],
        collect_source_metrics(source, files),
    )
    package_rows = []
    for delivery, primary, secondary in PACKAGE_SURFACES:
        package_rows.append(
            {
                "delivery": delivery,
                "primary_path": primary,
                "primary_present": (source / primary).exists(),
                "secondary_path": secondary,
                "secondary_present": (source / secondary).exists(),
                "smoke_status": "not_run",
            }
        )
    write_csv(
        output / "package_delivery_matrix.csv",
        [
            "delivery",
            "primary_path",
            "primary_present",
            "secondary_path",
            "secondary_present",
            "smoke_status",
        ],
        package_rows,
    )

    tool_rows = []
    for name, command, relative_cwd in (
        ("python", [sys.executable, "--version"], "."),
        ("node", ["node", "--version"], "."),
        ("corepack", ["corepack", "--version"], "."),
        ("yarn-web", ["corepack", "yarn", "--version"], "web"),
        ("yarn-runtime", ["corepack", "yarn", "--version"], "runtime"),
        ("make", ["make", "--version"], "."),
        ("git", ["git", "--version"], "."),
    ):
        code, text = run_command(command, source / relative_cwd)
        tool_rows.append(
            {
                "tool": name,
                "exit_code": code,
                "version": text.splitlines()[0] if text else "",
            }
        )
    write_csv(
        output / "toolchain_inventory.csv",
        ["tool", "exit_code", "version"],
        tool_rows,
    )

    test_rows = [
        {
            "suite": "python-module",
            "command": "python regression/runtests.py --exclude feature_tests",
            "status": "not_run",
        },
        {
            "suite": "javascript",
            "command": "corepack yarn run test:js-once",
            "status": "not_run",
        },
        {
            "suite": "frontend-bundle",
            "command": "corepack yarn run bundle",
            "status": "not_run",
        },
        {
            "suite": "feature",
            "command": "python regression/runtests.py --pkg feature_tests",
            "status": "not_run",
        },
        {
            "suite": "desktop",
            "command": "runtime package smoke",
            "status": "not_run",
        },
        {
            "suite": "container",
            "command": "container package smoke",
            "status": "not_run",
        },
    ]
    write_csv(
        output / "test_baseline_matrix.csv",
        ["suite", "command", "status"],
        test_rows,
    )

    secret_findings = scan_evidence_for_secrets(output)
    (output / "secret_scan.json").write_text(
        json.dumps(
            {"findings": secret_findings}, indent=2, sort_keys=True
        ) + "\n",
        encoding="utf-8",
    )
    summary = f"""# CDE-PREP-000 Static Baseline Capture

- Baseline ID: `{baseline_id}`
- Application version: `{version}`
- Source revision: `{git['revision']}`
- Source status: `{git['git_status']}`
- Source files: {len(files)}
- Source tree SHA-256: `{source_digest}`
- API route rows: {len(routes)}
- Migration rows: {len(migrations)}
- Declared dependency rows: {len(dependencies)}
- Evidence secret-scan findings: {len(secret_findings)}

Test and package execution status is initialized as `not_run`; the isolated
baseline runner updates execution evidence separately. Static inventory does
not imply functional, performance, packaging, or support readiness.
"""
    (output / "STATIC_CAPTURE_SUMMARY.md").write_text(
        summary, encoding="utf-8"
    )
    write_evidence_manifest(output)
    return 2 if secret_findings else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, help="Read-only pgAdmin source tree"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Evidence output directory outside source",
    )
    parser.add_argument(
        "--revision",
        help="Revision identity when source is an exported archive",
    )
    parser.add_argument("--baseline-id", help="Stable baseline identifier")
    parser.add_argument(
        "--captured-at",
        help="ISO-8601 capture time for reproducible metadata",
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Require a clean git checkout",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace files in an existing output directory",
    )
    return parser


def main() -> int:
    return capture(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
