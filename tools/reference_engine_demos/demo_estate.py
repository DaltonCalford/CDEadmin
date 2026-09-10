#!/usr/bin/env python3
"""Manage persistent, on-demand CDEadmin reference-engine demo data."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNTIME = ROOT / "runtime"
EVIDENCE = ROOT / "evidence"
PASSWORD = "CDEadminDemo-2026!"
NETWORK = "cdeadmin-demo-net"
VITESS_COMPOSE = ROOT / "config/vitess/docker-compose.yml"
VITESS_PROJECT = "cdeadmin-demo-vitess"
CASSANDRA_IMAGE = "cassandra:5.0.8"
CASSANDRA_CONFIGURATION = (
    "authenticated-three-node-v5-native-and-streaming-ports"
)
CASSANDRA_ENTRYPOINT = ROOT / "config/cassandra/entrypoint.sh"
CASSANDRA_TRIGGER_SOURCE = (
    ROOT / "config/cassandra/CDEadminQualificationTrigger.java"
)
CASSANDRA_TOOL_AUTH_SOURCE = (
    ROOT / "config/cassandra/CDEadminEnvironmentAuthProvider.java"
)
CASSANDRA_TRIGGER_RUNTIME = RUNTIME / "cassandra-triggers"
CASSANDRA_TRIGGER_JAR = CASSANDRA_TRIGGER_RUNTIME / "cdeadmin-trigger.jar"
FOUNDATIONDB_CONFIGURATION = "four-process-safe-control-plane-v2"
FOUNDATIONDB_ENTRYPOINT = ROOT / "config/foundationdb/entrypoint.sh"
IGNITE_CONFIGURATION = "authenticated-two-node-object-control-v4"
IGNITE_CONFIG = ROOT / "config/ignite-config.xml"
IGNITE_CONTROL = ROOT / "config/ignite/control.sh"
IGNITE_FIXTURE_JAR = (
    RUNTIME / "ignite-fixture/cdeadmin-ignite-fixture.jar"
)
IGNITE_NODES = (
    ("cdeadmin-demo-apache-ignite",
     "cdeadmin-demo-apache-ignite-v2", "cdeadmin-demo-ignite-1"),
    ("cdeadmin-demo-apache-ignite-2",
     None, "cdeadmin-demo-ignite-2"),
)
TIKV_CONFIGURATION = "api-v2-four-store-control-plane-v1"
TIKV_CONFIG = ROOT / "config/tikv/api-v2.toml"
TIKV_PD = "cdeadmin-demo-pd"
TIKV_NODES = tuple(
    (
        "cdeadmin-demo-tikv" if index == 0 else
        f"cdeadmin-demo-tikv-{index + 1}",
        52016 + index,
        52180 + index,
        f"cdeadmin-demo-tikv-v2-{index + 1}",
    )
    for index in range(4)
)
CASSANDRA_NODES = (
    ("cdeadmin-demo-cassandra", "127.0.0.1",
     "cdeadmin-demo-cassandra", "rack1"),
    ("cdeadmin-demo-cassandra-2", "127.0.0.2",
     "cdeadmin-demo-cassandra-2", "rack2"),
    ("cdeadmin-demo-cassandra-3", "127.0.0.3",
     "cdeadmin-demo-cassandra-3", "rack3"),
)


ENGINE_ORDER = (
    "sqlite", "duckdb", "firebird", "redis", "postgresql", "mysql",
    "mariadb", "mongodb", "neo4j", "clickhouse", "opensearch",
    "opensearch_sql_ppl", "influxdb", "milvus", "cassandra",
    "apache_ignite", "cockroachdb", "dolt", "immudb", "xtdb",
    "foundationdb", "tikv", "tidb", "vitess", "yugabytedb",
    "yugabytedb_ycql",
)


CONTAINERS = {
    "firebird": {
        "image": "firebirdsql/firebird:5.0.4", "ports": ((53050, 3050),),
        "env": {
            "FIREBIRD_ROOT_PASSWORD": PASSWORD,
            "FIREBIRD_DATABASE": "/var/lib/firebird/data/cdeadmin_demo.fdb",
        },
        "volumes": (("cdeadmin-demo-firebird", "/var/lib/firebird/data"),),
    },
    "redis": {
        "image": "redis:8.6.2", "ports": ((56379, 6379),),
        "args": ("redis-server", "--appendonly", "yes"),
        "volumes": (("cdeadmin-demo-redis", "/data"),),
    },
    "mongodb": {
        "image": "mongo:8.2.6", "ports": ((57017, 27017),),
        "args": ("mongod", "--bind_ip_all"),
        "volumes": (("cdeadmin-demo-mongodb", "/data/db"),),
    },
    "postgresql": {
        "image": "postgres:18.3", "ports": ((55432, 5432),),
        "env": {"POSTGRES_USER": "cdeadmin_demo",
                "POSTGRES_PASSWORD": PASSWORD,
                "POSTGRES_DB": "cdeadmin_demo"},
        "volumes": (("cdeadmin-demo-postgresql", "/var/lib/postgresql"),),
    },
    "mysql": {
        "image": "mysql:9.7.0", "ports": ((53306, 3306),),
        "env": {"MYSQL_ROOT_PASSWORD": PASSWORD,
                "MYSQL_DATABASE": "cdeadmin_demo",
                "MYSQL_USER": "cdeadmin_demo",
                "MYSQL_PASSWORD": PASSWORD},
        "volumes": (("cdeadmin-demo-mysql", "/var/lib/mysql"),),
    },
    "mariadb": {
        "image": "mariadb:12.2.2", "ports": ((53307, 3306),),
        "configuration": "mariadb-12.2.2-row-binlog-v1",
        "env": {"MARIADB_ROOT_PASSWORD": PASSWORD,
                "MARIADB_DATABASE": "cdeadmin_demo",
                "MARIADB_USER": "cdeadmin_demo",
                "MARIADB_PASSWORD": PASSWORD},
        "args": ("--log-bin=mariadb-bin", "--server-id=1",
                 "--binlog-format=ROW"),
        "volumes": (("cdeadmin-demo-mariadb", "/var/lib/mysql"),),
    },
    "clickhouse": {
        "image": "clickhouse/clickhouse-server:25.12.10.7",
        "ports": ((58123, 8123), (59001, 9000)),
        "env": {"CLICKHOUSE_DB": "cdeadmin_demo",
                "CLICKHOUSE_USER": "cdeadmin_demo",
                "CLICKHOUSE_PASSWORD": PASSWORD,
                "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT": "1"},
        "volumes": (("cdeadmin-demo-clickhouse", "/var/lib/clickhouse"),),
    },
    "cockroachdb": {
        "image": "cockroachdb/cockroach:v26.1.3",
        "ports": ((56257, 26257), (58081, 8080)),
        "args": ("start-single-node", "--insecure",
                 "--store=/cockroach/cockroach-data"),
        "volumes": (("cdeadmin-demo-cockroachdb",
                     "/cockroach/cockroach-data"),),
    },
    "dolt": {
        "image": "dolthub/dolt-sql-server:1.86.6",
        "ports": ((53308, 3306),), "env": {"DOLT_ROOT_HOST": "%"},
        "volumes": (("cdeadmin-demo-dolt", "/var/lib/dolt"),),
    },
    "immudb": {
        "image": "codenotary/immudb:1.11.0",
        "ports": (
            (53322, 3322), (55435, 5432), (58082, 8080),
            (59497, 9497),
        ),
        "volumes": (("cdeadmin-demo-immudb", "/var/lib/immudb"),),
    },
    "influxdb": {
        "image": "influxdb:3.9.0-core", "ports": ((58181, 8181),),
        "args": ("influxdb3", "serve", "--node-id=cdeadmin-demo",
                 "--object-store=file", "--data-dir=/var/lib/influxdb3",
                 "--without-auth"),
        "volumes": (("cdeadmin-demo-influxdb", "/var/lib/influxdb3"),),
    },
    "neo4j": {
        "image": "neo4j:2026.04.0-enterprise",
        "ports": ((57474, 7474), (57687, 7687)),
        "env": {"NEO4J_AUTH": f"neo4j/{PASSWORD}",
                "NEO4J_ACCEPT_LICENSE_AGREEMENT": "yes",
                "NEO4J_PLUGINS": "[]",
                "NEO4J_server_memory_heap_initial__size": "256m",
                "NEO4J_server_memory_heap_max__size": "512m",
                "NEO4J_server_memory_pagecache_size": "256m"},
        "volumes": (("cdeadmin-demo-neo4j-data", "/data"),
                    ("cdeadmin-demo-neo4j-logs", "/logs")),
    },
    "opensearch": {
        "image": "opensearchproject/opensearch:3.6.0",
        "configuration": "filesystem-snapshot-repository-v2",
        "ports": ((59200, 9200), (59600, 9600)),
        "env": {"discovery.type": "single-node",
                "DISABLE_SECURITY_PLUGIN": "true",
                "path.repo": "/usr/share/opensearch/data/snapshots",
                "OPENSEARCH_JAVA_OPTS": "-Xms512m -Xmx512m"},
        "volumes": (("cdeadmin-demo-opensearch",
                     "/usr/share/opensearch/data"),),
    },
    "xtdb": {
        "image": "ghcr.io/xtdb/xtdb:2.1.0",
        "configuration": "native-healthz-port-v1",
        "ports": ((55434, 5432), (58083, 8080)),
        "volumes": (("cdeadmin-demo-xtdb", "/var/lib/xtdb"),),
    },
    "apache_ignite": {
        "image": "apacheignite/ignite:2.17.0",
        "ports": ((51080, 10800), (58080, 8080)),
        "env": {
            "JVM_OPTS": (
                "-Xms256m -Xmx768m -Djava.net.preferIPv4Stack=true"
            ),
            "OPTION_LIBS": "ignite-rest-http,ignite-json",
            "CONFIG_URI": (
                "/opt/ignite/apache-ignite/config/cdeadmin-demo.xml"
            ),
        },
        "volumes": (
            ("cdeadmin-demo-apache-ignite",
             "/opt/ignite/apache-ignite/work"),
            (str(ROOT / "config/ignite-config.xml"),
             "/opt/ignite/apache-ignite/config/cdeadmin-demo.xml"),
        ),
    },
    "yugabytedb": {
        "image": "yugabytedb/yugabyte:2025.2.2.2-b11",
        "configuration": "ycql-password-auth-v1",
        "ports": ((55433, 5433), (59042, 9042), (57000, 7000),
                  (59000, 9000)),
        "args": (
            "bin/yugabyted", "start", "--daemon=false", "--ui=false",
            "--use_cassandra_authentication=true",
        ),
        "volumes": (("cdeadmin-demo-yugabyte-disk0", "/mnt/disk0"),
                    ("cdeadmin-demo-yugabyte-disk1", "/mnt/disk1")),
    },
}


ALIASES = {
    "opensearch_sql_ppl": "opensearch",
    "yugabytedb_ycql": "yugabytedb",
}


def run(command, *, check=True, capture=False, env=None, cwd=None):
    rendered = " ".join(str(part) for part in command).replace(
        PASSWORD, "[demo-password]"
    )
    print("+", rendered, flush=True)
    return subprocess.run(
        [str(part) for part in command], check=check, text=True,
        capture_output=capture, env=env, cwd=cwd,
    )


def docker_exists(name):
    result = subprocess.run(
        ["docker", "container", "inspect", name], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def docker_running(name):
    result = subprocess.run(
        [
            "docker", "container", "inspect", "--format",
            "{{.State.Running}}", name,
        ],
        text=True, capture_output=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def ensure_network():
    result = subprocess.run(
        ["docker", "network", "inspect", NETWORK],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        run(["docker", "network", "create", NETWORK])


def container_name(engine):
    return f"cdeadmin-demo-{ALIASES.get(engine, engine).replace('_', '-')}"


def create_standard_container(engine):
    actual = ALIASES.get(engine, engine)
    spec = CONTAINERS[actual]
    name = container_name(actual)
    if docker_exists(name):
        inspected = run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", name],
            capture=True,
        ).stdout.strip()
        if inspected != spec["image"]:
            raise RuntimeError(
                f"{name} already exists with image {inspected}; expected "
                f"{spec['image']}. It was not replaced."
            )
        configuration = spec.get("configuration")
        if configuration is None:
            return
        observed = run([
            "docker", "inspect", "--format",
            "{{index .Config.Labels \"org.cdeadmin.demo.configuration\"}}",
            name,
        ], check=False, capture=True)
        if (
                observed.returncode == 0 and
                observed.stdout.strip() == configuration):
            return
        if docker_running(name):
            run(["docker", "stop", "--timeout", "30", name])
        # Configuration is disposable; all engine data remains in its named
        # volumes and is reattached below.
        run(["docker", "rm", name])
    ensure_network()
    command = ["docker", "create", "--name", name, "--network", NETWORK]
    if spec.get("configuration"):
        command += [
            "--label", "org.cdeadmin.demo.configuration=" +
            spec["configuration"],
        ]
    for host_port, container_port in spec.get("ports", ()):
        command += ["-p", f"127.0.0.1:{host_port}:{container_port}"]
    for key, value in spec.get("env", {}).items():
        command += ["-e", f"{key}={value}"]
    for volume, destination in spec.get("volumes", ()):
        command += ["-v", f"{volume}:{destination}"]
    command.append(spec["image"])
    command += list(spec.get("args", ()))
    run(command)


def wait_tcp(port, timeout=180):
    return wait_tcp_at("127.0.0.1", port, timeout)


def wait_tcp_at(host, port, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.closing(socket.socket()) as probe:
            probe.settimeout(1)
            if probe.connect_ex((host, port)) == 0:
                return
        time.sleep(1)
    raise TimeoutError(f"{host}:{port} did not become ready")


def wait_until(callable_value, timeout=180, interval=2):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            return callable_value()
        except Exception as exc:
            last_error = exc
            time.sleep(interval)
    raise last_error or TimeoutError("readiness check timed out")


READY_PORTS = {
    "firebird": 53050, "redis": 56379, "mongodb": 57017,
    "postgresql": 55432, "mysql": 53306,
    "mariadb": 53307,
    "clickhouse": 58123, "cockroachdb": 56257, "dolt": 53308,
    "immudb": 53322, "influxdb": 58181, "neo4j": 57687,
    "opensearch": 59200, "xtdb": 55434, "apache_ignite": 51080,
    "yugabytedb": 55433,
}


def start_standard(engine):
    actual = ALIASES.get(engine, engine)
    create_standard_container(actual)
    name = container_name(actual)
    if not docker_running(name):
        run(["docker", "start", name])
    wait_tcp(READY_PORTS[actual], timeout=300)
    if actual == "apache_ignite":
        wait_until(lambda: _activate_ignite(name), timeout=180, interval=3)
    elif actual == "xtdb":
        wait_until(_xtdb_health_ready, timeout=180, interval=1)


def _xtdb_health_ready():
    with urllib.request.urlopen(
            "http://127.0.0.1:58083/healthz/ready", timeout=5) as response:
        if response.status != 200 or response.read(256).strip() != b"Ready.":
            raise RuntimeError("XTDB native health service is not ready")
    return True


def _cassandra_container_current(name):
    if not docker_exists(name):
        return False
    result = run([
        "docker", "inspect", "--format",
        "{{index .Config.Labels \"org.cdeadmin.demo.configuration\"}}",
        name,
    ], check=False, capture=True)
    return (
        result.returncode == 0 and
        result.stdout.strip() == CASSANDRA_CONFIGURATION
    )


def _ensure_cassandra_trigger_jar():
    """Build deterministic trigger and tool-auth qualification classes."""
    sources = (CASSANDRA_TRIGGER_SOURCE, CASSANDRA_TOOL_AUTH_SOURCE)
    if any(not source.is_file() for source in sources):
        raise RuntimeError(
            "missing Cassandra qualification Java source"
        )
    if (
            CASSANDRA_TRIGGER_JAR.is_file() and
            CASSANDRA_TRIGGER_JAR.stat().st_mtime >=
            max(source.stat().st_mtime for source in sources)):
        return
    CASSANDRA_TRIGGER_RUNTIME.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix="cdeadmin-cassandra-trigger-", dir=RUNTIME) as temporary:
        temporary_path = Path(temporary)
        dependency = temporary_path / "apache-cassandra-5.0.8.jar"
        driver_dependency = (
            temporary_path / "cassandra-driver-core-3.12.1-shaded.jar"
        )
        classes = temporary_path / "classes"
        classes.mkdir()
        run([
            "docker", "run", "--rm", "--entrypoint", "/bin/cp",
            "-v", f"{temporary_path}:/output", CASSANDRA_IMAGE,
            "/opt/cassandra/lib/apache-cassandra-5.0.8.jar",
            "/output/apache-cassandra-5.0.8.jar",
        ])
        run([
            "docker", "run", "--rm", "--entrypoint", "/bin/cp",
            "-v", f"{temporary_path}:/output", CASSANDRA_IMAGE,
            "/opt/cassandra/lib/cassandra-driver-core-3.12.1-shaded.jar",
            "/output/cassandra-driver-core-3.12.1-shaded.jar",
        ])
        run([
            "javac", "--release", "17", "-cp",
            f"{dependency}{os.pathsep}{driver_dependency}",
            "-d", str(classes),
            *(str(source) for source in sources),
        ])
        run([
            "jar", "--create", "--file", str(CASSANDRA_TRIGGER_JAR),
            "-C", str(classes), ".",
        ])


def _create_cassandra_node(name, host, volume, rack):
    if _cassandra_container_current(name):
        return
    if docker_exists(name):
        if docker_running(name):
            run(["docker", "stop", "--timeout", "30", name])
        # Container configuration is disposable; the named data volume is
        # deliberately retained and reattached to the corrected topology.
        run(["docker", "rm", name])
    ensure_network()
    if not CASSANDRA_ENTRYPOINT.is_file():
        raise RuntimeError(
            f"missing Cassandra entrypoint: {CASSANDRA_ENTRYPOINT}"
        )
    environment = {
        "CASSANDRA_CLUSTER_NAME": "CDEadmin Demo",
        "CASSANDRA_SEEDS": "cdeadmin-demo-cassandra",
        "CASSANDRA_DC": "datacenter1",
        "CASSANDRA_RACK": rack,
        "CASSANDRA_ENDPOINT_SNITCH": "GossipingPropertyFileSnitch",
        "CDEADMIN_NATIVE_TRANSPORT_PORT": "19042",
        "CDEADMIN_STORAGE_PORT": "17000",
        "CASSANDRA_BROADCAST_RPC_ADDRESS": host,
        "MAX_HEAP_SIZE": "768M",
        "HEAP_NEWSIZE": "192M",
        "LOCAL_JMX": "no",
        "CDEADMIN_JMX_PORT": "17199",
        "CDEADMIN_JMX_USERNAME": "cassandra",
        "CDEADMIN_JMX_PASSWORD": PASSWORD,
        "JVM_EXTRA_OPTS": (
            f"-Djava.rmi.server.hostname={host} "
            "-Dcassandra.triggers_dir=/opt/cassandra/triggers "
            "-Dcom.sun.management.jmxremote.access.file="
            "/etc/cassandra/jmxremote.access"
        ),
    }
    command = [
        "docker", "create", "--name", name, "--network", NETWORK,
        "--label",
        f"org.cdeadmin.demo.configuration={CASSANDRA_CONFIGURATION}",
        "-p", f"{host}:19042:19042",
        "-p", f"{host}:17000:17000",
        "-p", f"{host}:17199:17199",
        "-v", f"{volume}:/var/lib/cassandra",
        "-v", f"{CASSANDRA_TRIGGER_RUNTIME.resolve()}:"
        "/opt/cassandra/triggers:ro",
        "-v", f"{CASSANDRA_ENTRYPOINT.resolve()}:"
        "/usr/local/bin/cdeadmin-cassandra-entrypoint:ro",
        "--entrypoint", "/usr/local/bin/cdeadmin-cassandra-entrypoint",
    ]
    for key, value in environment.items():
        command += ["-e", f"{key}={value}"]
    command += [CASSANDRA_IMAGE, "cassandra", "-f"]
    run(command)


def _cassandra_cql_ready(name):
    for password in (PASSWORD, "cassandra"):
        result = run([
            "docker", "exec", name, "/opt/cassandra/bin/cqlsh",
            "127.0.0.1", "19042", "-u", "cassandra", "-p", password,
            "-e", "SELECT release_version FROM system.local",
        ], check=False, capture=True)
        if result.returncode == 0 and "5.0.8" in result.stdout:
            return True
    raise RuntimeError("authenticated Cassandra CQL is not ready")


def _cassandra_topology_ready():
    result = run([
        "docker", "exec", "cdeadmin-demo-cassandra",
        "/opt/cassandra/bin/nodetool", "-u", "cassandra", "-pwf",
        "/etc/cassandra/jmxremote.password", "-p", "17199", "status",
    ], check=False, capture=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    if sum(1 for line in result.stdout.splitlines()
           if line.lstrip().startswith("UN  ")) != len(CASSANDRA_NODES):
        raise RuntimeError("Cassandra three-node topology is not ready")
    return True


def start_cassandra():
    # Start every persisted member before asking Cassandra's quorum-backed
    # authenticator to validate a login.  Waiting for authenticated CQL on the
    # first member while its peers are still stopped deadlocks a restarted
    # three-node fixture because system_auth is replicated at quorum.
    _ensure_cassandra_trigger_jar()
    for name, host, volume, rack in CASSANDRA_NODES:
        _create_cassandra_node(name, host, volume, rack)
        if not docker_running(name):
            run(["docker", "start", name])

    for name, host, _volume, _rack in CASSANDRA_NODES:
        wait_tcp_at(host, 19042, timeout=300)
        wait_until(
            lambda current=name: _cassandra_cql_ready(current),
            timeout=300, interval=3,
        )
    wait_until(_cassandra_topology_ready, timeout=300, interval=5)


def stop_cassandra():
    for name, _host, _volume, _rack in reversed(CASSANDRA_NODES):
        if docker_running(name):
            run(["docker", "stop", "--timeout", "30", name])


def _activate_ignite(name):
    activation = run([
        "docker", "exec", name,
        "/opt/ignite/apache-ignite/bin/control.sh",
        "--user", "ignite", "--password", "ignite",
        "--set-state", "ACTIVE", "--yes",
    ], check=False, capture=True)
    output = activation.stdout + activation.stderr
    if activation.returncode and "already" not in output.lower():
        raise RuntimeError(output.strip())
    return True


def _ignite_topology_ready(expected=2):
    query = urllib.parse.urlencode({
        "cmd": "top", "user": "ignite", "password": "ignite",
    })
    with urllib.request.urlopen(
            f"http://127.0.0.1:58080/ignite?{query}",
            timeout=5) as response:
        document = json.loads(response.read(1024 * 1024))
    if document.get("successStatus") != 0 or len(
            document.get("response") or []) != expected:
        raise RuntimeError(
            f"Ignite topology has not reached {expected} nodes")
    return True


def _ignite_container_current(name):
    if not docker_exists(name):
        return False
    observed = run([
        "docker", "inspect", "--format",
        "{{index .Config.Labels \"org.cdeadmin.demo.configuration\"}}",
        name,
    ], check=False, capture=True)
    return (
        observed.returncode == 0 and
        observed.stdout.strip() == IGNITE_CONFIGURATION
    )


def start_ignite():
    ensure_network()
    for required in (IGNITE_CONFIG, IGNITE_CONTROL, IGNITE_FIXTURE_JAR):
        if not required.is_file():
            raise RuntimeError(
                f"missing Ignite fixture asset: {required}; run "
                "bootstrap.py first"
            )
    for name, volume, consistent_id in IGNITE_NODES:
        if docker_exists(name) and not _ignite_container_current(name):
            if docker_running(name):
                run(["docker", "stop", "--timeout", "30", name])
            # Container configuration is disposable; persistent work state is
            # retained in the named volume and reattached below.
            run(["docker", "rm", name])
        if not docker_exists(name):
            command = [
                "docker", "create", "--name", name, "--hostname", name,
                "--network", NETWORK, "--label",
                "org.cdeadmin.demo.configuration=" + IGNITE_CONFIGURATION,
            ]
            if name == IGNITE_NODES[0][0]:
                command.extend((
                    "-p", "127.0.0.1:51080:10800",
                    "-p", "127.0.0.1:58080:8080",
                ))
            command.extend((
                "-e", "JVM_OPTS=-Xms256m -Xmx768m "
                "-Djava.net.preferIPv4Stack=true "
                f"-DIGNITE_CONSISTENT_ID={consistent_id}",
                "-e", "OPTION_LIBS=ignite-rest-http,ignite-json",
                "-e", "CONFIG_URI=/opt/ignite/apache-ignite/config/"
                "cdeadmin-demo.xml",
                "-v", f"{IGNITE_CONFIG}:/opt/ignite/apache-ignite/"
                "config/cdeadmin-demo.xml:ro",
                "-v", f"{IGNITE_FIXTURE_JAR}:/opt/ignite/apache-ignite/"
                "libs/cdeadmin-ignite-fixture.jar:ro",
                "apacheignite/ignite:2.17.0",
            ))
            work_mount = (
                ("-v", f"{volume}:/opt/ignite/apache-ignite/work")
                if volume is not None else
                ("--tmpfs", "/opt/ignite/apache-ignite/work:rw,size=1g")
            )
            command[-1:-1] = work_mount
            run(command)
    primary = IGNITE_NODES[0][0]
    secondary = IGNITE_NODES[1][0]
    if not docker_running(primary):
        if docker_running(secondary):
            run(["docker", "stop", "--timeout", "30", secondary])
        run(["docker", "start", primary])
    wait_tcp(51080, timeout=300)
    wait_tcp(58080, timeout=300)
    try:
        wait_until(lambda: _activate_ignite(primary), timeout=30, interval=3)
    except Exception:
        # A persistent primary must form or recover its own cluster before the
        # ephemeral worker can become a competing empty coordinator.
        if docker_running(secondary):
            run(["docker", "stop", "--timeout", "30", secondary])
        if docker_running(primary):
            run(["docker", "stop", "--timeout", "30", primary])
        run(["docker", "start", primary])
        wait_tcp(51080, timeout=300)
        wait_tcp(58080, timeout=300)
        wait_until(
            lambda: _activate_ignite(primary), timeout=240, interval=3)
    if not docker_running(secondary):
        run(["docker", "start", secondary])
    wait_until(_ignite_topology_ready, timeout=240, interval=3)


def stop_ignite():
    for name, _volume, _consistent_id in reversed(IGNITE_NODES):
        if docker_running(name):
            run(["docker", "stop", "--timeout", "30", name])


def stop_standard(engine):
    actual = ALIASES.get(engine, engine)
    name = container_name(actual)
    if docker_running(name):
        run(["docker", "stop", "--timeout", "30", name])


def start_tikv_stack():
    if not TIKV_CONFIG.exists():
        raise RuntimeError(f"missing TiKV API-v2 config: {TIKV_CONFIG}")
    ensure_network()
    # PD must advertise a TiKV address reachable by both host-native CDEadmin
    # and the TiDB container. This Docker daemon runs in a separate namespace,
    # so the workstation's LAN address is the shared rendezvous point.
    host_address = socket.gethostbyname(socket.gethostname())
    definitions = [
        (TIKV_PD, "pingcap/pd:v8.5.6",
         ((52379, 2379), (f"{host_address}:52379", 2379)),
         (("cdeadmin-demo-pd-v2", "/data/pd"),),
         (),
         ("--name=pd", "--data-dir=/data/pd",
          "--client-urls=http://0.0.0.0:2379",
          "--peer-urls=http://0.0.0.0:2380",
          f"--advertise-client-urls=http://{host_address}:52379",
          f"--advertise-peer-urls=http://{TIKV_PD}:2380",
          f"--initial-cluster=pd=http://{TIKV_PD}:2380")),
    ]
    for name, client_port, status_port, volume in TIKV_NODES:
        definitions.append((
            name, "pingcap/tikv:v8.5.6",
            ((f"{host_address}:{client_port}", 20160),
             (f"{host_address}:{status_port}", 20180)),
            ((volume, "/data/tikv"),),
            ((str(TIKV_CONFIG), "/etc/tikv/cdeadmin-api-v2.toml"),),
            ("--addr=0.0.0.0:20160",
             f"--advertise-addr={host_address}:{client_port}",
             "--status-addr=0.0.0.0:20180",
             f"--advertise-status-addr={host_address}:{status_port}",
             f"--pd={TIKV_PD}:2379", "--data-dir=/data/tikv",
             "--config=/etc/tikv/cdeadmin-api-v2.toml"),
        ))
    for name, image, ports, volumes, bind_mounts, args in definitions:
        if docker_exists(name):
            observed = run([
                "docker", "inspect", "--format",
                "{{index .Config.Labels "
                "\"org.cdeadmin.demo.configuration\"}}", name,
            ], check=False, capture=True)
            if observed.returncode or (
                    observed.stdout.strip() != TIKV_CONFIGURATION):
                if docker_running(name):
                    run(["docker", "stop", "--timeout", "30", name])
                # Container definitions are disposable. API-v1 volumes remain
                # untouched; this topology attaches distinct API-v2 volumes.
                run(["docker", "rm", name])
        if not docker_exists(name):
            command = ["docker", "create", "--name", name,
                       "--network", NETWORK, "--label",
                       "org.cdeadmin.demo.configuration=" +
                       TIKV_CONFIGURATION]
            for host_port, container_port in ports:
                binding = (str(host_port) if ":" in str(host_port)
                           else f"127.0.0.1:{host_port}")
                command += ["-p", f"{binding}:{container_port}"]
            for volume, destination in volumes:
                command += ["-v", f"{volume}:{destination}"]
            for source, destination in bind_mounts:
                command += ["-v", f"{source}:{destination}:ro"]
            command += [image, *args]
            run(command)
        if not docker_running(name):
            run(["docker", "start", name])
        ready_host = str(ports[0][0]).rsplit(":", 1)
        if len(ready_host) == 2:
            wait_host, wait_port = ready_host[0], int(ready_host[1])
            wait_tcp_at(wait_host, wait_port, timeout=300)
        else:
            wait_tcp(int(ready_host[0]), timeout=300)
    wait_until(_tikv_topology_ready, timeout=300, interval=2)


def _tikv_topology_ready():
    with urllib.request.urlopen(
            "http://127.0.0.1:52379/pd/api/v1/stores",
            timeout=5) as response:
        document = json.load(response)
    stores = [
        item.get("store") or {} for item in document.get("stores", [])
        if isinstance(item, dict)
    ]
    if len(stores) != len(TIKV_NODES) or any(
            item.get("state_name") != "Up" for item in stores):
        raise RuntimeError("TiKV four-store topology is not Up")
    with urllib.request.urlopen(
            "http://127.0.0.1:52379/pd/api/v1/regions",
            timeout=5) as response:
        regions = json.load(response).get("regions", [])
    if not any(len(item.get("peers") or []) == 3 for item in regions):
        raise RuntimeError("TiKV three-voter Region is not ready")
    return True


def start_tidb():
    start_tikv_stack()
    name = "cdeadmin-demo-tidb"
    if not docker_exists(name):
        run([
            "docker", "create", "--name", name, "--network", NETWORK,
            "-p", "127.0.0.1:54000:4000",
            "-p", "127.0.0.1:51000:10080", "pingcap/tidb:v8.5.6",
            "--store=tikv", "--path=cdeadmin-demo-pd:2379",
            "--host=0.0.0.0", "--status=10080",
        ])
    if not docker_running(name):
        run(["docker", "start", name])
    wait_tcp(54000, timeout=300)


def stop_tikv_stack(include_tidb=False):
    names = [name for name, _client, _status, _volume in TIKV_NODES]
    names.append(TIKV_PD)
    if include_tidb:
        names.insert(0, "cdeadmin-demo-tidb")
    for name in names:
        if docker_running(name):
            run(["docker", "stop", "--timeout", "30", name])


def start_milvus():
    """Start the Milvus standalone topology with persistent metadata/data."""
    ensure_network()
    definitions = (
        (
            "cdeadmin-demo-milvus-etcd", "quay.io/coreos/etcd:v3.5.18",
            (), (("cdeadmin-demo-milvus-etcd", "/etcd"),), {},
            ("etcd", "--advertise-client-urls=http://127.0.0.1:2379",
             "--listen-client-urls=http://0.0.0.0:2379",
             "--data-dir=/etcd"),
        ),
        (
            "cdeadmin-demo-milvus-minio",
            "minio/minio:RELEASE.2024-12-18T13-15-44Z", (),
            (("cdeadmin-demo-milvus-minio", "/minio_data"),),
            {"MINIO_ACCESS_KEY": "minioadmin",
             "MINIO_SECRET_KEY": "minioadmin"},
            ("minio", "server", "/minio_data", "--console-address", ":9001"),
        ),
        (
            "cdeadmin-demo-milvus", "milvusdb/milvus:v2.6.5",
            ((59530, 19530), (59091, 9091)),
            (("cdeadmin-demo-milvus", "/var/lib/milvus"),),
            {"ETCD_ENDPOINTS": "cdeadmin-demo-milvus-etcd:2379",
             "MINIO_ADDRESS": "cdeadmin-demo-milvus-minio:9000"},
            ("milvus", "run", "standalone"),
        ),
    )
    for name, image, ports, volumes, environment, args in definitions:
        if not docker_exists(name):
            command = ["docker", "create", "--name", name,
                       "--network", NETWORK]
            for host_port, container_port in ports:
                command += ["-p", f"127.0.0.1:{host_port}:{container_port}"]
            for volume, destination in volumes:
                command += ["-v", f"{volume}:{destination}"]
            for key, value in environment.items():
                command += ["-e", f"{key}={value}"]
            command += [image, *args]
            run(command)
        if not docker_running(name):
            run(["docker", "start", name])
    wait_tcp(59530, timeout=300)


def stop_milvus():
    for name in (
        "cdeadmin-demo-milvus", "cdeadmin-demo-milvus-minio",
        "cdeadmin-demo-milvus-etcd",
    ):
        if docker_running(name):
            run(["docker", "stop", "--timeout", "30", name])


def start_vitess():
    if not VITESS_COMPOSE.exists():
        raise RuntimeError(f"missing Vitess topology: {VITESS_COMPOSE}")
    run([
        "docker", "compose", "-p", VITESS_PROJECT, "-f", VITESS_COMPOSE,
        "up", "-d",
    ], cwd=VITESS_COMPOSE.parent)
    wait_tcp(15306, timeout=600)


def stop_vitess():
    if VITESS_COMPOSE.exists():
        run([
            "docker", "compose", "-p", VITESS_PROJECT, "-f",
            VITESS_COMPOSE, "stop", "--timeout", "30",
        ], cwd=VITESS_COMPOSE.parent)


def start_foundationdb():
    name = "cdeadmin-demo-foundationdb"
    cluster = RUNTIME / "fdb.cluster"
    expected_cluster = (
        "cdeadmin_demo:0123456789abcdef0123456789abcdef@"
        "127.0.0.1:54500\n"
    )
    if not cluster.exists() or cluster.read_text(
            encoding="utf-8") != expected_cluster:
        cluster.write_text(expected_cluster, encoding="utf-8")
    current = False
    if docker_exists(name):
        observed = run([
            "docker", "inspect", "--format",
            "{{index .Config.Labels \"org.cdeadmin.demo.configuration\"}}",
            name,
        ], check=False, capture=True)
        current = (
            observed.returncode == 0 and
            observed.stdout.strip() == FOUNDATIONDB_CONFIGURATION
        )
        if not current:
            if docker_running(name):
                run(["docker", "stop", "--timeout", "30", name])
            # The container definition is disposable. The named database
            # volume is retained and reattached to the safe topology.
            run(["docker", "rm", name])
    if not docker_exists(name):
        if not FOUNDATIONDB_ENTRYPOINT.is_file():
            raise RuntimeError(
                f"missing FoundationDB entrypoint: {FOUNDATIONDB_ENTRYPOINT}"
            )
        run([
            "docker", "create", "--name", name, "--network", NETWORK,
            "--label", "org.cdeadmin.demo.configuration=" +
            FOUNDATIONDB_CONFIGURATION,
            "-p", "127.0.0.1:54500:54500",
            "-p", "127.0.0.1:54501:54501",
            "-p", "127.0.0.1:54502:54502",
            "-p", "127.0.0.1:54503:54503",
            "-v", "cdeadmin-demo-foundationdb:/var/fdb/data",
            "-v", f"{cluster}:/var/fdb/fdb.cluster:ro", "--entrypoint",
            "/bin/bash", "-v", f"{FOUNDATIONDB_ENTRYPOINT}:"
            "/usr/local/bin/cdeadmin-foundationdb-entrypoint:ro",
            "foundationdb/foundationdb:7.3.77",
            "/usr/local/bin/cdeadmin-foundationdb-entrypoint",
        ])
    if not docker_running(name):
        run(["docker", "start", name])
    for port in range(54500, 54504):
        wait_tcp(port, timeout=120)
    cli = RUNTIME / "foundationdb/bin/fdbcli"
    if not cli.exists():
        raise RuntimeError("FoundationDB client is not bootstrapped; run "
                           "bootstrap.py first")
    status = run([cli, "-C", cluster, "--exec", "status minimal"],
                 check=False, capture=True)
    if "unavailable" in status.stdout.lower() or status.returncode:
        run([cli, "-C", cluster, "--exec",
             "configure new single ssd"])
    wait_until(lambda: _foundationdb_available(cluster), timeout=180)


def _foundationdb_available(cluster):
    cli = RUNTIME / "foundationdb/bin/fdbcli"
    status = run(
        [cli, "-C", cluster, "--exec", "status minimal"],
        check=False, capture=True,
    )
    if status.returncode or "unavailable" in status.stdout.lower():
        raise RuntimeError(status.stdout.strip() or status.stderr.strip())
    return True


def start(engine):
    actual = ALIASES.get(engine, engine)
    if actual == "cassandra":
        start_cassandra()
    elif actual == "apache_ignite":
        start_ignite()
    elif actual in CONTAINERS:
        start_standard(actual)
    elif actual == "foundationdb":
        start_foundationdb()
    elif actual == "tikv":
        start_tikv_stack()
    elif actual == "tidb":
        start_tidb()
    elif actual == "milvus":
        start_milvus()
    elif actual == "vitess":
        start_vitess()
    elif actual in {"sqlite", "duckdb"}:
        return
    else:
        raise KeyError(engine)


def stop(engine):
    actual = ALIASES.get(engine, engine)
    if actual == "cassandra":
        stop_cassandra()
    elif actual == "apache_ignite":
        stop_ignite()
    elif actual in CONTAINERS:
        stop_standard(actual)
    elif actual == "foundationdb":
        if docker_running("cdeadmin-demo-foundationdb"):
            run(["docker", "stop", "--timeout", "30",
                 "cdeadmin-demo-foundationdb"])
    elif actual == "tikv":
        stop_tikv_stack()
    elif actual == "tidb":
        stop_tikv_stack(include_tidb=True)
    elif actual == "milvus":
        stop_milvus()
    elif actual == "vitess":
        stop_vitess()
    elif actual in {"sqlite", "duckdb"}:
        return


def write_evidence(engine, payload):
    document = {
        "schema": "cdeadmin.reference-engine-demo-evidence.v1",
        "engine": engine, "recorded_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **payload,
    }
    path = EVIDENCE / f"{engine}.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return document


def status():
    values = []
    for engine in ENGINE_ORDER:
        actual = ALIASES.get(engine, engine)
        if actual == "cassandra":
            states = [
                "running" if docker_running(name) else (
                    "stopped" if docker_exists(name) else "absent"
                ) for name, _host, _volume, _rack in CASSANDRA_NODES
            ]
            state = (
                "running" if all(item == "running" for item in states)
                else "absent" if all(item == "absent" for item in states)
                else "partial:" + ",".join(states)
            )
        elif actual == "apache_ignite":
            states = [
                "running" if docker_running(name) else (
                    "stopped" if docker_exists(name) else "absent"
                ) for name, _volume, _consistent_id in IGNITE_NODES
            ]
            state = (
                "running" if all(item == "running" for item in states)
                else "absent" if all(item == "absent" for item in states)
                else "partial:" + ",".join(states)
            )
        elif actual in CONTAINERS:
            state = "running" if docker_running(container_name(actual)) else (
                "stopped" if docker_exists(container_name(actual))
                else "absent"
            )
        elif actual == "foundationdb":
            state = "running" if docker_running(
                "cdeadmin-demo-foundationdb") else "stopped"
        elif actual in {"tikv", "tidb"}:
            name = (
                "cdeadmin-demo-tidb" if actual == "tidb"
                else TIKV_NODES[0][0]
            )
            state = "running" if docker_running(name) else (
                "stopped" if docker_exists(name) else "absent"
            )
        elif actual == "milvus":
            state = "running" if docker_running(
                "cdeadmin-demo-milvus") else (
                    "stopped" if docker_exists("cdeadmin-demo-milvus")
                    else "absent"
            )
        elif actual == "vitess":
            state = "running" if docker_running(
                f"{VITESS_PROJECT}-vtgate-1") else (
                    "stopped" if docker_exists(
                        f"{VITESS_PROJECT}-vtgate-1") else "absent"
            )
        else:
            state = "file" if actual in {"sqlite", "duckdb"} else "adapter"
        seeded = (EVIDENCE / f"{engine}.json").exists()
        values.append({"engine": engine, "runtime": state, "seeded": seeded})
    print(json.dumps(values, indent=2))


def seed(engine):
    from seed_demo import seed_engine
    start(engine)
    result = seed_engine(engine)
    print(json.dumps(write_evidence(engine, {
        "status": "passed", "sample": result,
    }), indent=2))


def verify(engine):
    from seed_demo import verify_engine
    start(engine)
    result = verify_engine(engine)
    print(json.dumps(write_evidence(engine, {
        "status": "passed", "verification": result,
    }), indent=2))


def prepare(group):
    if group == "embedded":
        engines = ("sqlite", "duckdb")
    elif group == "local":
        engines = ("firebird", "redis", "mongodb", "cassandra")
    else:
        engines = ENGINE_ORDER
    failures = {}
    for engine in engines:
        actual = ALIASES.get(engine, engine)
        print(f"\n=== {engine} ===", flush=True)
        try:
            seed(engine)
            verify(engine)
        except Exception as exc:
            failures[engine] = f"{type(exc).__name__}: {exc}"
            write_evidence(engine, {
                "status": "failed", "error": failures[engine],
            })
            print(f"FAILED {engine}: {failures[engine]}", file=sys.stderr)
        finally:
            if group == "all" and actual not in {"sqlite", "duckdb"}:
                with contextlib.suppress(Exception):
                    stop(engine)
    if failures:
        raise RuntimeError(json.dumps(failures, sort_keys=True))


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    subparsers.add_parser("status")
    for name in ("start", "stop"):
        child = subparsers.add_parser(name)
        child.add_argument(
            "engine", choices=(*ENGINE_ORDER, "embedded", "local", "all")
        )
    for name in ("seed", "verify"):
        child = subparsers.add_parser(name)
        child.add_argument("engine", choices=ENGINE_ORDER)
    child = subparsers.add_parser("prepare")
    child.add_argument("group", choices=("embedded", "local", "all"))
    return parser.parse_args()


def main():
    DATA.mkdir(exist_ok=True)
    RUNTIME.mkdir(exist_ok=True)
    EVIDENCE.mkdir(exist_ok=True)
    mysql_lib = RUNTIME / "mysql/lib64"
    client_lib = RUNTIME / "foundationdb/lib"
    sqlite_lib = RUNTIME / "sqlite-3.53.0/lib"
    private_libraries = [
        path for path in (mysql_lib, sqlite_lib, client_lib) if path.exists()
    ]
    if private_libraries and not os.environ.get("CDEADMIN_DEMO_CLIENT_ENV"):
        environment = dict(os.environ)
        existing = environment.get("LD_LIBRARY_PATH")
        library_path = ":".join(str(path) for path in private_libraries)
        environment["LD_LIBRARY_PATH"] = (
            f"{library_path}:{existing}" if existing else library_path
        )
        environment["CDEADMIN_DEMO_CLIENT_ENV"] = "1"
        os.execve(sys.executable, [sys.executable, *sys.argv], environment)
    args = arguments()
    if args.command == "list":
        print("\n".join(ENGINE_ORDER))
    elif args.command == "status":
        status()
    elif args.command == "prepare":
        prepare(args.group)
    elif args.command in {"start", "stop"} and args.engine in {
            "embedded", "local", "all"}:
        groups = {
            "embedded": ("sqlite", "duckdb"),
            "local": ("firebird", "redis", "mongodb", "cassandra"),
            "all": ENGINE_ORDER,
        }
        engines = groups[args.engine]
        if args.command == "stop":
            engines = tuple(reversed(engines))
        processed = set()
        for engine in engines:
            actual = ALIASES.get(engine, engine)
            if actual in processed:
                continue
            processed.add(actual)
            globals()[args.command](actual)
    else:
        globals()[args.command](args.engine)


if __name__ == "__main__":
    main()
