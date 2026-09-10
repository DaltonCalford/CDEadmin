#!/usr/bin/env python3
"""Bootstrap portable client tools for the CDEadmin reference demos."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
RUNTIME = ROOT / "runtime"
SQLITE_VERSION = "3.53.0"
SQLITE_URL = "https://www.sqlite.org/2026/sqlite-src-3530000.zip"
SQLITE_SHA256 = (
    "fbc30cdbfcfa42c78fe7bddd3fd37ab8995369a31d39097a5d0633296c0b6e65"
)
FOUNDATIONDB_IMAGE = "foundationdb/foundationdb:7.3.77"
FIREBIRD_IMAGE = "firebirdsql/firebird:5.0.4"
MARIADB_IMAGE = "mariadb:12.2.2"
MYSQL_IMAGE = "mysql:9.7.0"
CASSANDRA_IMAGE = "cassandra:5.0.8"
IGNITE_IMAGE = "apacheignite/ignite:2.17.0"
YUGABYTE_IMAGE = "yugabytedb/yugabyte:2025.2.2.2-b11"


def run(command, *, cwd=None):
    print("+", " ".join(str(item) for item in command), flush=True)
    return subprocess.run(
        [str(item) for item in command], cwd=cwd, check=True, text=True,
    )


def require_program(program):
    location = shutil.which(program)
    if not location:
        raise RuntimeError(f"required program is not installed: {program}")
    return location


def install_python_requirements():
    run([
        sys.executable, "-m", "pip", "install", "-r",
        ROOT / "requirements.txt",
    ])


def _sqlite_runtime_ready(prefix):
    output = prefix / "bin/sqlite3"
    library = prefix / "lib/libsqlite3.so"
    if output.exists() and library.exists():
        version = subprocess.check_output(
            [str(output), "--version"], text=True,
        ).split()[0]
        modules = subprocess.check_output([
            str(output), ":memory:",
            "SELECT name FROM pragma_module_list "
            "WHERE name IN ('fts5','rtree') ORDER BY name;",
        ], text=True).splitlines()
        return version == SQLITE_VERSION and modules == ["fts5", "rtree"]
    return False


def build_sqlite(archive_source=None):
    prefix = RUNTIME / f"sqlite-{SQLITE_VERSION}"
    output = prefix / "bin/sqlite3"
    if _sqlite_runtime_ready(prefix):
        print(f"SQLite {SQLITE_VERSION} already bootstrapped: {output}")
        return
    require_program("cc")
    require_program("make")
    with tempfile.TemporaryDirectory(prefix="cdeadmin-sqlite-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "sqlite.zip"
        if archive_source is None:
            print(f"+ download {SQLITE_URL}", flush=True)
            urllib.request.urlretrieve(SQLITE_URL, archive)
        else:
            shutil.copy2(archive_source.resolve(), archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != SQLITE_SHA256:
            raise RuntimeError(
                f"SQLite archive checksum mismatch: {digest}"
            )
        with zipfile.ZipFile(archive) as source:
            source.extractall(temporary_path / "source")
        source_root = temporary_path / "source/sqlite-src-3530000"
        # Python's zip extraction intentionally does not restore executable
        # mode bits. SQLite's configure wrapper invokes this helper directly.
        (source_root / "configure").chmod(0o755)
        (source_root / "autosetup/autosetup-find-tclsh").chmod(0o755)
        build = temporary_path / "build"
        build.mkdir()
        run([
            source_root / "configure", "--all",
            f"--prefix={prefix.resolve()}",
        ], cwd=build)
        run(["make", "-j2"], cwd=build)
        run(["make", "install"], cwd=build)
        if not _sqlite_runtime_ready(prefix):
            raise RuntimeError(
                "SQLite runtime lacks the required FTS5 or RTREE module"
            )


def build_tikv_helper():
    require_program("go")
    output = RUNTIME / "bin/cdeadmin-tikv-helper"
    evidence = RUNTIME / "build_evidence/tikv-helper.json"
    run([
        sys.executable, REPOSITORY / "tools/cdeadmin_build_tikv_helper.py",
        "--output", output, "--evidence", evidence,
    ])


def build_ignite_fixture():
    """Compile the exact-version task/service fixture used by live gates."""
    require_program("docker")
    require_program("javac")
    require_program("jar")
    source = REPOSITORY / "tools/fixtures/apache_ignite/" \
        "CdeAdminIgniteFixture.java"
    output_root = RUNTIME / "ignite-fixture"
    output = output_root / "cdeadmin-ignite-fixture.jar"
    evidence = RUNTIME / "build_evidence/ignite-fixture.json"
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    expected = {
        "schema": "cdeadmin.ignite-fixture-build.v1",
        "image": IGNITE_IMAGE,
        "source_sha256": source_digest,
        "java_release": 11,
    }
    if output.is_file() and evidence.is_file():
        try:
            observed = json.loads(evidence.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            observed = None
        if isinstance(observed, dict) and all(
                observed.get(key) == value for key, value in expected.items()
        ) and observed.get("jar_sha256") == hashlib.sha256(
                output.read_bytes()).hexdigest():
            print(f"Ignite fixture already bootstrapped: {output}")
            return
    name = f"cdeadmin-demo-ignite-libs-{os.getpid()}"
    with tempfile.TemporaryDirectory(
            prefix="cdeadmin-ignite-fixture-") as temporary:
        temporary_path = Path(temporary)
        dependencies = temporary_path / "dependencies"
        classes = temporary_path / "classes"
        dependencies.mkdir()
        classes.mkdir()
        run(["docker", "create", "--name", name, IGNITE_IMAGE, "true"])
        try:
            for library in ("ignite-core-2.17.0.jar", "cache-api-1.0.0.jar"):
                run([
                    "docker", "cp",
                    f"{name}:/opt/ignite/apache-ignite/libs/{library}",
                    dependencies / library,
                ])
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        classpath = os.pathsep.join(
            str(path) for path in sorted(dependencies.glob("*.jar"))
        )
        run([
            "javac", "--release", "11", "-cp", classpath,
            "-d", classes, source,
        ])
        output_root.mkdir(parents=True, exist_ok=True)
        run(["jar", "--create", "--file", output, "-C", classes, "."])
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({
        **expected,
        "jar_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_foundationdb_client():
    require_program("docker")
    clients = tuple(
        RUNTIME / f"foundationdb/bin/{name}"
        for name in ("fdbcli", "fdbbackup")
    )
    cli = clients[0]
    restore = RUNTIME / "foundationdb/bin/fdbrestore"
    library = RUNTIME / "foundationdb/lib/libfdb_c.so"
    restore_ready = (
        restore.is_symlink() and os.readlink(restore) == "fdbbackup"
    )
    if (all(path.exists() for path in clients) and restore_ready and
            library.exists()):
        print(f"FoundationDB client already bootstrapped: {cli}")
        return
    name = f"cdeadmin-demo-fdb-client-{os.getpid()}"
    run(["docker", "create", "--name", name, FOUNDATIONDB_IMAGE, "true"])
    try:
        cli.parent.mkdir(parents=True, exist_ok=True)
        library.parent.mkdir(parents=True, exist_ok=True)
        for client in clients:
            run([
                "docker", "cp", f"{name}:/usr/bin/{client.name}", client,
            ])
            client.chmod(0o755)
        restore.unlink(missing_ok=True)
        restore.symlink_to("fdbbackup")
        run(["docker", "cp", f"{name}:/usr/lib/libfdb_c.so", library])
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def extract_firebird_client():
    """Extract the exact Firebird 5 client required by service dialogs."""
    require_program("docker")
    library = RUNTIME / "firebird/lib/libfbclient.so.5.0.4"
    link = RUNTIME / "firebird/lib/libfbclient.so.2"
    if library.exists() and link.is_symlink() and os.readlink(
            link) == library.name:
        print(f"Firebird client already bootstrapped: {library}")
        return
    name = f"cdeadmin-demo-firebird-client-{os.getpid()}"
    run(["docker", "create", "--name", name, FIREBIRD_IMAGE, "true"])
    try:
        library.parent.mkdir(parents=True, exist_ok=True)
        run([
            "docker", "cp",
            f"{name}:/opt/firebird/lib/libfbclient.so.5.0.4", library,
        ])
        library.chmod(0o755)
        link.unlink(missing_ok=True)
        link.symlink_to(library.name)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def extract_mariadb_clients():
    """Extract exact MariaDB client tools used by provider forms."""
    require_program("docker")
    clients = tuple(
        RUNTIME / f"mariadb/bin/{name}"
        for name in (
            "mariadb", "mariadb-dump", "mariadb-upgrade",
            "mariadb-binlog", "mariadb-admin",
        )
    )
    if all(path.exists() for path in clients):
        identities = tuple(
            subprocess.run(
                [str(path), "--version"], check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            ).stdout for path in clients
        )
        if all("12.2.2-MariaDB" in item for item in identities):
            print(f"MariaDB clients already bootstrapped: {clients[0]}")
            return
    name = f"cdeadmin-demo-mariadb-client-{os.getpid()}"
    run(["docker", "create", "--name", name, MARIADB_IMAGE, "true"])
    try:
        clients[0].parent.mkdir(parents=True, exist_ok=True)
        for client in clients:
            run([
                "docker", "cp", f"{name}:/usr/bin/{client.name}", client,
            ])
            client.chmod(0o755)
        for client in clients:
            identity = subprocess.run(
                [str(client), "--version"], check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            ).stdout
            if "12.2.2-MariaDB" not in identity:
                raise RuntimeError(
                    f"extracted MariaDB client is not 12.2.2: {client}"
                )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def extract_mysql_shell():
    """Extract the exact MySQL Shell and its private OpenSSL runtime."""
    require_program("docker")
    prefix = RUNTIME / "mysql"
    executable = prefix / "bin/mysqlsh"
    openssl = prefix / "lib64/libssl.so.3.5.1"
    crypto = prefix / "lib64/libcrypto.so.3.5.1"
    environment = dict(os.environ)
    environment["LD_LIBRARY_PATH"] = str(prefix / "lib64")
    if executable.is_file() and openssl.is_file() and crypto.is_file():
        identity = subprocess.run(
            [str(executable), "--version"], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=environment,
        ).stdout
        if "Ver 9.7.0" in identity:
            print(f"MySQL Shell already bootstrapped: {executable}")
            return
    name = f"cdeadmin-demo-mysql-shell-{os.getpid()}"
    run(["docker", "create", "--name", name, MYSQL_IMAGE, "true"])
    try:
        if prefix.exists():
            shutil.rmtree(prefix)
        for directory in ("bin", "lib", "libexec", "share", "lib64"):
            (prefix / directory).mkdir(parents=True, exist_ok=True)
        for source, destination in (
            ("/usr/bin/mysqlsh", prefix / "bin/mysqlsh"),
            ("/usr/lib/mysqlsh", prefix / "lib/mysqlsh"),
            ("/usr/libexec/mysqlsh", prefix / "libexec/mysqlsh"),
            ("/usr/share/mysqlsh", prefix / "share/mysqlsh"),
            ("/usr/lib64/libssl.so.3.5.1", openssl),
            ("/usr/lib64/libcrypto.so.3.5.1", crypto),
        ):
            run(["docker", "cp", f"{name}:{source}", destination])
        executable.chmod(0o755)
        (prefix / "lib64/libssl.so.3").symlink_to(openssl.name)
        (prefix / "lib64/libcrypto.so.3").symlink_to(crypto.name)
        identity = subprocess.run(
            [str(executable), "--version"], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=environment,
        ).stdout
        if "Ver 9.7.0" not in identity:
            raise RuntimeError(
                "extracted MySQL Shell is not the required 9.7.0 runtime"
            )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def extract_cassandra_clients():
    """Extract the exact Cassandra tools and libraries used by forms."""
    require_program("docker")
    root = RUNTIME / "cassandra"
    tools = tuple(root / f"bin/{name}" for name in (
        "cqlsh", "nodetool", "sstableloader",
    ))
    release_jar = root / "lib/apache-cassandra-5.0.8.jar"
    client_options = root / "conf/jvm17-clients.options"
    if (all(path.is_file() for path in tools) and release_jar.is_file() and
            client_options.is_file()):
        print(f"Cassandra clients already bootstrapped: {tools[0]}")
        return
    name = f"cdeadmin-demo-cassandra-client-{os.getpid()}"
    run(["docker", "create", "--name", name, CASSANDRA_IMAGE, "true"])
    try:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        run(["docker", "cp", f"{name}:/opt/cassandra/.", root])
        configuration = root / "conf"
        if configuration.is_symlink():
            configuration.unlink()
        configuration.mkdir(exist_ok=True)
        run(["docker", "cp", f"{name}:/etc/cassandra/.", configuration])
        if not release_jar.is_file() or not all(
                path.is_file() for path in tools):
            raise RuntimeError(
                "extracted Cassandra client distribution is not 5.0.8"
            )
        for path in tools:
            path.chmod(0o755)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def extract_yugabytedb_ycql_client():
    """Extract the exact YCQL shell shipped by YugabyteDB 2025.2.2.2."""
    require_program("docker")
    root = RUNTIME / "yugabytedb"
    executable = root / "bin/ycqlsh"
    script = root / "bin/ycqlsh.py"
    library = root / "pylib/cqlshlib"
    if executable.is_file() and script.is_file() and library.is_dir():
        print(f"YugabyteDB YCQL client already bootstrapped: {executable}")
        return
    name = f"cdeadmin-demo-ycql-client-{os.getpid()}"
    run(["docker", "create", "--name", name, YUGABYTE_IMAGE, "true"])
    try:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        for child in ("bin/ycqlsh", "bin/ycqlsh.py", "pylib", "lib"):
            destination = root / child
            destination.parent.mkdir(parents=True, exist_ok=True)
            run([
                "docker", "cp", f"{name}:/home/yugabyte/{child}",
                destination,
            ])
        executable.chmod(0o755)
        if not script.is_file() or not library.is_dir():
            raise RuntimeError(
                "extracted YugabyteDB YCQL client is incomplete"
            )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def generate_profiles():
    template = (ROOT / "connection_profiles.template.json").read_text(
        encoding="utf-8"
    )
    rendered = template.replace("${DEMO_ROOT}", str(ROOT.resolve()))
    rendered = rendered.replace(
        "${REPOSITORY_ROOT}", str(REPOSITORY.resolve())
    )
    document = json.loads(rendered)
    output = RUNTIME / "connection_profiles.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Generated connection profiles: {output}")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-python", action="store_true",
        help="Install the pinned Python client packages into this interpreter",
    )
    parser.add_argument("--skip-sqlite", action="store_true")
    parser.add_argument(
        "--sqlite-archive", type=Path,
        help="Use this verified SQLite source archive instead of downloading",
    )
    parser.add_argument("--skip-tikv", action="store_true")
    parser.add_argument("--skip-ignite-fixture", action="store_true")
    parser.add_argument("--skip-foundationdb", action="store_true")
    parser.add_argument("--skip-firebird-client", action="store_true")
    parser.add_argument("--skip-mariadb-client", action="store_true")
    parser.add_argument("--skip-mysql-shell", action="store_true")
    parser.add_argument("--skip-cassandra-client", action="store_true")
    parser.add_argument("--skip-yugabytedb-client", action="store_true")
    return parser.parse_args()


def main():
    args = arguments()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.install_python:
        install_python_requirements()
    if not args.skip_sqlite:
        build_sqlite(args.sqlite_archive)
    if not args.skip_tikv:
        build_tikv_helper()
    if not args.skip_ignite_fixture:
        build_ignite_fixture()
    if not args.skip_foundationdb:
        extract_foundationdb_client()
    if not args.skip_firebird_client:
        extract_firebird_client()
    if not args.skip_mariadb_client:
        extract_mariadb_clients()
    if not args.skip_mysql_shell:
        extract_mysql_shell()
    if not args.skip_cassandra_client:
        extract_cassandra_clients()
    if not args.skip_yugabytedb_client:
        extract_yugabytedb_ycql_client()
    generate_profiles()
    print("Reference-engine demo bootstrap completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
