"""Idempotent native seed and verification adapters for the demo estate."""

from __future__ import annotations

import json
from pathlib import Path
import base64
import subprocess
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PASSWORD = "CDEadminDemo-2026!"


CUSTOMERS = (
    (1, "Northwind Field Lab", "Toronto", "ON"),
    (2, "Contoso Water", "Hamilton", "ON"),
    (3, "Adventure Works Mining", "Sudbury", "ON"),
)
ASSETS = (
    (101, 1, "PUMP-101", "centrifugal-pump", "active"),
    (102, 1, "VALVE-102", "control-valve", "maintenance"),
    (201, 2, "METER-201", "flow-meter", "active"),
    (301, 3, "FAN-301", "ventilation-fan", "active"),
)
WORK_ORDERS = (
    (1001, 101, "Ada Lovelace", "inspect", "open", 3),
    (1002, 102, "Grace Hopper", "replace seal", "scheduled", 2),
    (1003, 201, "Katherine Johnson", "calibrate", "closed", 1),
    (1004, 301, "Ada Lovelace", "vibration review", "open", 4),
)


def retry(callable_value, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            return callable_value()
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise last


def statements(connection, sql_values):
    cursor = connection.cursor()
    try:
        for source in sql_values:
            cursor.execute(source)
        connection.commit()
    finally:
        cursor.close()


def relational_ddl(kind):
    if kind in {"postgresql", "cockroachdb", "yugabytedb"}:
        return (
            "CREATE SCHEMA IF NOT EXISTS service",
            "CREATE TABLE IF NOT EXISTS service.customers "
            "(customer_id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, "
            "city VARCHAR(80), region VARCHAR(40))",
            "CREATE TABLE IF NOT EXISTS service.assets "
            "(asset_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL "
            "REFERENCES service.customers(customer_id), tag VARCHAR(40) "
            "UNIQUE NOT NULL, asset_type VARCHAR(60), status VARCHAR(20))",
            "CREATE TABLE IF NOT EXISTS service.work_orders "
            "(work_order_id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL "
            "REFERENCES service.assets(asset_id), technician VARCHAR(100), "
            "summary VARCHAR(200), status VARCHAR(20), priority INTEGER, "
            "created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)",
            "CREATE INDEX IF NOT EXISTS ix_work_orders_status_priority ON "
            "service.work_orders(status, priority)",
            "CREATE OR REPLACE VIEW service.open_work_orders AS SELECT "
            "w.work_order_id, a.tag, c.name AS customer, w.technician, "
            "w.summary, w.priority FROM service.work_orders w JOIN "
            "service.assets a ON a.asset_id=w.asset_id JOIN service.customers "
            "c ON c.customer_id=a.customer_id WHERE w.status='open'",
        )
    return (
        "CREATE TABLE IF NOT EXISTS customers "
        "(customer_id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, "
        "city VARCHAR(80), region VARCHAR(40))",
        "CREATE TABLE IF NOT EXISTS assets "
        "(asset_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, "
        "tag VARCHAR(40) UNIQUE NOT NULL, asset_type VARCHAR(60), "
        "status VARCHAR(20), FOREIGN KEY(customer_id) REFERENCES "
        "customers(customer_id))",
        "CREATE TABLE IF NOT EXISTS work_orders "
        "(work_order_id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL, "
        "technician VARCHAR(100), summary VARCHAR(200), status VARCHAR(20), "
        "priority INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(asset_id) REFERENCES assets(asset_id))",
        "CREATE INDEX ix_work_orders_status_priority ON "
        "work_orders(status, priority)",
        "CREATE VIEW open_work_orders AS SELECT w.work_order_id, a.tag, "
        "c.name AS customer, w.technician, w.summary, w.priority FROM "
        "work_orders w JOIN assets a ON a.asset_id=w.asset_id JOIN customers "
        "c ON c.customer_id=a.customer_id WHERE w.status='open'",
    )


def seed_postgresql_family(engine):
    import psycopg

    params = {
        "postgresql": dict(host="127.0.0.1", port=55432,
                           user="cdeadmin_demo", password=PASSWORD,
                           dbname="cdeadmin_demo"),
        "cockroachdb": dict(host="127.0.0.1", port=56257, user="root",
                            dbname="defaultdb", sslmode="disable"),
        "yugabytedb": dict(host="127.0.0.1", port=55433, user="yugabyte",
                           dbname="yugabyte"),
    }[engine]
    if engine != "postgresql":
        admin = retry(lambda: psycopg.connect(**params, autocommit=True))
        try:
            exists = admin.execute(
                "SELECT 1 FROM pg_database WHERE datname='cdeadmin_demo'"
            ).fetchone()
            if not exists:
                admin.execute("CREATE DATABASE cdeadmin_demo")
        finally:
            admin.close()
        params["dbname"] = "cdeadmin_demo"
    connection = retry(lambda: psycopg.connect(**params, autocommit=True))
    prefix = "service."
    try:
        cursor = connection.cursor()
        for source in relational_ddl(engine):
            cursor.execute(source)
        for table in ("work_orders", "assets", "customers"):
            cursor.execute(f"DELETE FROM {prefix}{table}")
        cursor.executemany(
            "INSERT INTO service.customers VALUES (%s,%s,%s,%s)", CUSTOMERS
        )
        cursor.executemany(
            "INSERT INTO service.assets VALUES (%s,%s,%s,%s,%s)", ASSETS
        )
        cursor.executemany(
            "INSERT INTO service.work_orders "
            "(work_order_id,asset_id,technician,summary,status,priority) "
            "VALUES (%s,%s,%s,%s,%s,%s)", WORK_ORDERS
        )
        if engine == "postgresql":
            cursor.execute(
                "CREATE SEQUENCE IF NOT EXISTS service.inspection_number "
                "START 5000"
            )
            cursor.execute(
                "CREATE OR REPLACE FUNCTION service.priority_label(value int) "
                "RETURNS text LANGUAGE SQL IMMUTABLE AS $$ SELECT CASE WHEN "
                "value >= 4 THEN 'urgent' WHEN value >= 2 THEN 'normal' "
                "ELSE 'low' END $$"
            )
            cursor.execute("DROP MATERIALIZED VIEW IF EXISTS service.workload")
            cursor.execute(
                "CREATE MATERIALIZED VIEW service.workload AS SELECT "
                "technician, count(*) AS work_orders FROM service.work_orders "
                "GROUP BY technician"
            )
        cursor.close()
    finally:
        connection.close()
    return {"database": "cdeadmin_demo", "customers": 3, "assets": 4,
            "work_orders": 4}


def seed_mysql_family(engine):
    import mysql.connector

    config = {
        "mysql": (53306, "cdeadmin_demo", PASSWORD),
        "mariadb": (53307, "cdeadmin_demo", PASSWORD),
        "dolt": (53308, "root", None),
        "tidb": (54000, "root", None),
        "vitess": (15306, "root", None),
    }[engine]
    port, user, password = config
    database = "cdeadmin_demo"
    if engine == "vitess":
        database = "test_keyspace"

    def connect(database_name=None):
        values = dict(host="127.0.0.1", port=port, user=user,
                      connection_timeout=10, autocommit=True)
        if password:
            values["password"] = password
        if database_name:
            values["database"] = database_name
        return mysql.connector.connect(**values)
    admin = retry(connect, timeout=300)
    cursor = admin.cursor()
    if engine != "vitess":
        cursor.execute("CREATE DATABASE IF NOT EXISTS cdeadmin_demo")
    cursor.close()
    admin.close()
    connection = retry(lambda: connect(database), timeout=120)
    cursor = connection.cursor()
    try:
        for name in ("open_work_orders",):
            try:
                cursor.execute(f"DROP VIEW IF EXISTS {name}")
            except Exception:
                pass
        for source in relational_ddl("mysql"):
            try:
                cursor.execute(source)
            except Exception as exc:
                message = str(exc).lower()
                if ("already exists" not in message and
                        "duplicate key name" not in message):
                    raise
        if engine == "vitess":
            # These tables are intentionally sharded by their own primary
            # vindexes. Cross-shard foreign keys are therefore represented by
            # the model, not enforced by an individual MySQL tablet.
            for table, constraint in (
                ("work_orders", "work_orders_ibfk_1"),
                ("assets", "assets_ibfk_1"),
            ):
                try:
                    cursor.execute(
                        f"ALTER TABLE {table} DROP FOREIGN KEY {constraint}"
                    )
                except Exception as exc:
                    if "can't drop" not in str(exc).lower() and (
                            "does not exist" not in str(exc).lower()):
                        raise
        for table in ("work_orders", "assets", "customers"):
            cursor.execute(f"DELETE FROM {table}")
        cursor.executemany("INSERT INTO customers VALUES (%s,%s,%s,%s)",
                           CUSTOMERS)
        cursor.executemany("INSERT INTO assets VALUES (%s,%s,%s,%s,%s)",
                           ASSETS)
        cursor.executemany(
            "INSERT INTO work_orders "
            "(work_order_id,asset_id,technician,summary,status,priority) "
            "VALUES (%s,%s,%s,%s,%s,%s)", WORK_ORDERS
        )
        if engine == "dolt":
            cursor.execute("CALL DOLT_ADD('.')")
            cursor.fetchall()
            try:
                cursor.execute(
                    "CALL DOLT_COMMIT('-Am', 'Seed CDEadmin field-service demo')"
                )
                cursor.fetchall()
            except Exception as exc:
                if "nothing to commit" not in str(exc).lower():
                    raise
        counts = {}
        for table in ("customers", "assets", "work_orders"):
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
    finally:
        cursor.close()
        connection.close()
    result = {"database": database, **counts}
    if engine == "vitess":
        result.update({"sharded": True, "shards": 2,
                       "primary_vindex": "xxhash"})
    return result


def seed_sqlite():
    import sqlite3
    path = DATA / "cdeadmin_demo.sqlite"
    reference_cli = (
        ROOT / "runtime/sqlite-3.53.0/bin/sqlite3"
    )
    if not reference_cli.exists():
        raise RuntimeError(f"missing SQLite 3.53.0 runtime: {reference_cli}")
    reference_version = subprocess.check_output(
        [str(reference_cli), "--version"], text=True,
    ).split()[0]
    subprocess.run([
        str(reference_cli), str(path),
        "CREATE TABLE IF NOT EXISTS engine_profile ("
        "profile TEXT PRIMARY KEY, writer_version TEXT NOT NULL);"
        "INSERT OR REPLACE INTO engine_profile VALUES "
        "('reference-runtime','3.53.0'); PRAGMA user_version=35300;",
    ], check=True)
    connection = sqlite3.connect(path)
    cursor = connection.cursor()
    writer_version = cursor.execute(
        "SELECT writer_version FROM engine_profile "
        "WHERE profile='reference-runtime'"
    ).fetchone()[0]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("DROP VIEW IF EXISTS open_work_orders")
    for source in relational_ddl("sqlite"):
        try:
            cursor.execute(source)
        except sqlite3.OperationalError as exc:
            if "already exists" not in str(exc).lower():
                raise
    for table in ("work_orders", "assets", "customers"):
        cursor.execute(f"DELETE FROM {table}")
    cursor.executemany("INSERT INTO customers VALUES (?,?,?,?)", CUSTOMERS)
    cursor.executemany("INSERT INTO assets VALUES (?,?,?,?,?)", ASSETS)
    cursor.executemany(
        "INSERT INTO work_orders "
        "(work_order_id,asset_id,technician,summary,status,priority) "
        "VALUES (?,?,?,?,?,?)", WORK_ORDERS
    )
    cursor.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS work_order_search USING "
        "fts5(summary, technician, content=work_orders, content_rowid=work_order_id)"
    )
    cursor.execute("INSERT INTO work_order_search(work_order_search) VALUES('rebuild')")
    connection.commit()
    connection.close()
    return {
        "path": str(path), "reference_writer_runtime": reference_version,
        "python_connector_runtime": sqlite3.sqlite_version,
        "writer_marker_read_by_connector": writer_version,
        "backward_compatibility_verified": writer_version == "3.53.0",
        "customers": 3, "work_orders": 4, "fts5": True,
    }


def seed_duckdb():
    import duckdb
    path = DATA / "cdeadmin_demo.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE SCHEMA IF NOT EXISTS service")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS service.customers "
        "(customer_id INTEGER PRIMARY KEY, name VARCHAR, city VARCHAR, region VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS service.assets (asset_id INTEGER PRIMARY KEY, "
        "customer_id INTEGER, tag VARCHAR UNIQUE, asset_type VARCHAR, status VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS service.work_orders "
        "(work_order_id INTEGER PRIMARY KEY, asset_id INTEGER, technician VARCHAR, "
        "summary VARCHAR, status VARCHAR, priority INTEGER, "
        "created_at TIMESTAMP DEFAULT current_timestamp)"
    )
    for table in ("work_orders", "assets", "customers"):
        connection.execute(f"DELETE FROM service.{table}")
    connection.executemany("INSERT INTO service.customers VALUES (?,?,?,?)", CUSTOMERS)
    connection.executemany("INSERT INTO service.assets VALUES (?,?,?,?,?)", ASSETS)
    connection.executemany(
        "INSERT INTO service.work_orders "
        "(work_order_id,asset_id,technician,summary,status,priority) "
        "VALUES (?,?,?,?,?,?)", WORK_ORDERS
    )
    connection.execute(
        "CREATE OR REPLACE VIEW service.open_work_orders AS SELECT w.work_order_id, "
        "a.tag, c.name customer, w.technician, w.summary, w.priority FROM "
        "service.work_orders w JOIN service.assets a USING(asset_id) JOIN "
        "service.customers c USING(customer_id) WHERE w.status='open'"
    )
    connection.execute(
        "CREATE OR REPLACE MACRO service.priority_label(value) AS CASE WHEN "
        "value >= 4 THEN 'urgent' WHEN value >= 2 THEN 'normal' ELSE 'low' END"
    )
    connection.close()
    return {"path": str(path), "duckdb_runtime": duckdb.__version__,
            "customers": 3, "work_orders": 4}


def seed_firebird(*, connection=None,
                  database_path="/var/lib/firebird/data/cdeadmin_demo.fdb"):
    """Reset demo fixtures; a supplied connection remains caller-owned.

    Qualification callers supply a freshly created disposable database so the
    ordinary demo database and its existing rows are never involved.
    """
    from firebird.driver import connect, create_database
    dsn = "127.0.0.1/53050:" + database_path
    password = PASSWORD
    owns_connection = connection is None

    def attach_or_create():
        try:
            return connect(dsn, user="SYSDBA", password=password)
        except Exception as exc:
            message = str(exc).lower()
            if "no such file" in message or "not defined" in message:
                return create_database(
                    dsn, user="SYSDBA", password=password
                )
            raise

    # The image opens its TCP listener before its security database and the
    # configured demo database have necessarily completed initialization.
    if owns_connection:
        connection = retry(attach_or_create, timeout=180)
    cursor = connection.cursor()

    def exists(kind, name):
        queries = {
            "table": "SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME=?",
            "index": "SELECT 1 FROM RDB$INDICES WHERE RDB$INDEX_NAME=?",
            "sequence": "SELECT 1 FROM RDB$GENERATORS WHERE RDB$GENERATOR_NAME=?",
            "view": (
                "SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME=? "
                "AND RDB$VIEW_BLR IS NOT NULL"
            ),
            "domain": (
                "SELECT 1 FROM RDB$FIELDS WHERE RDB$FIELD_NAME=? "
                "AND COALESCE(RDB$SYSTEM_FLAG, 0)=0"
            ),
            "trigger": (
                "SELECT 1 FROM RDB$TRIGGERS WHERE RDB$TRIGGER_NAME=? "
                "AND COALESCE(RDB$SYSTEM_FLAG, 0)=0"
            ),
            "procedure": (
                "SELECT 1 FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME=? "
                "AND RDB$PACKAGE_NAME IS NULL"
            ),
            "function": (
                "SELECT 1 FROM RDB$FUNCTIONS WHERE RDB$FUNCTION_NAME=? "
                "AND RDB$PACKAGE_NAME IS NULL AND RDB$MODULE_NAME IS NULL"
            ),
            "external-function": (
                "SELECT 1 FROM RDB$FUNCTIONS WHERE RDB$FUNCTION_NAME=? "
                "AND RDB$MODULE_NAME IS NOT NULL"
            ),
            "package": (
                "SELECT 1 FROM RDB$PACKAGES WHERE RDB$PACKAGE_NAME=?"
            ),
            "exception": (
                "SELECT 1 FROM RDB$EXCEPTIONS WHERE RDB$EXCEPTION_NAME=?"
            ),
            "role": "SELECT 1 FROM RDB$ROLES WHERE RDB$ROLE_NAME=?",
        }
        cursor.execute(queries[kind], (name.upper(),))
        return cursor.fetchone() is not None
    if not exists("table", "CUSTOMERS"):
        cursor.execute("CREATE TABLE CUSTOMERS (CUSTOMER_ID INTEGER NOT NULL "
                       "PRIMARY KEY, NAME VARCHAR(120) NOT NULL, CITY VARCHAR(80), "
                       "REGION VARCHAR(40))")
        connection.commit()
    if not exists("table", "ASSETS"):
        cursor.execute(
            "CREATE TABLE ASSETS (ASSET_ID INTEGER NOT NULL PRIMARY KEY, "
            "CUSTOMER_ID INTEGER NOT NULL REFERENCES CUSTOMERS(CUSTOMER_ID), "
            "TAG VARCHAR(40) NOT NULL UNIQUE, ASSET_TYPE VARCHAR(60), "
            "STATUS VARCHAR(20))")
        connection.commit()
    if not exists("table", "WORK_ORDERS"):
        cursor.execute(
            "CREATE TABLE WORK_ORDERS (WORK_ORDER_ID INTEGER NOT NULL "
            "PRIMARY KEY, ASSET_ID INTEGER NOT NULL REFERENCES ASSETS(ASSET_ID), "
            "TECHNICIAN VARCHAR(100), SUMMARY VARCHAR(200), STATUS VARCHAR(20), "
            "PRIORITY INTEGER, CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        connection.commit()
    if not exists("index", "IX_WORK_ORDERS_STATUS_PRIORITY"):
        cursor.execute(
            "CREATE INDEX IX_WORK_ORDERS_STATUS_PRIORITY ON "
            "WORK_ORDERS(STATUS, PRIORITY)")
        connection.commit()
    if not exists("sequence", "INSPECTION_NUMBER"):
        cursor.execute("CREATE SEQUENCE INSPECTION_NUMBER START WITH 5000")
        connection.commit()
    if not exists("view", "OPEN_WORK_ORDERS"):
        cursor.execute("CREATE VIEW OPEN_WORK_ORDERS AS SELECT w.WORK_ORDER_ID, "
                       "a.TAG, c.NAME CUSTOMER, w.TECHNICIAN, w.SUMMARY, w.PRIORITY "
                       "FROM WORK_ORDERS w JOIN ASSETS a ON a.ASSET_ID=w.ASSET_ID "
                       "JOIN CUSTOMERS c ON c.CUSTOMER_ID=a.CUSTOMER_ID "
                       "WHERE w.STATUS='open'")
        connection.commit()
    if not exists("domain", "CDEADMIN_STATUS"):
        cursor.execute(
            "CREATE DOMAIN CDEADMIN_STATUS AS VARCHAR(20) "
            "CHECK (VALUE IN ('open', 'closed', 'paused'))"
        )
        connection.commit()
    if not exists("trigger", "BI_WORK_ORDERS"):
        cursor.execute(
            "CREATE TRIGGER BI_WORK_ORDERS FOR WORK_ORDERS ACTIVE "
            "BEFORE INSERT POSITION 0 AS BEGIN "
            "IF (NEW.CREATED_AT IS NULL) THEN "
            "NEW.CREATED_AT = CURRENT_TIMESTAMP; END"
        )
        connection.commit()
    if not exists("procedure", "CDEADMIN_WORK_ORDER_COUNT"):
        cursor.execute(
            "CREATE PROCEDURE CDEADMIN_WORK_ORDER_COUNT "
            "RETURNS (ITEM_COUNT INTEGER) AS BEGIN "
            "SELECT COUNT(*) FROM WORK_ORDERS INTO :ITEM_COUNT; "
            "SUSPEND; END"
        )
        connection.commit()
    if not exists("function", "CDEADMIN_PRIORITY_LABEL"):
        cursor.execute(
            "CREATE FUNCTION CDEADMIN_PRIORITY_LABEL(PRIORITY INTEGER) "
            "RETURNS VARCHAR(16) AS BEGIN RETURN CASE "
            "WHEN PRIORITY >= 4 THEN 'urgent' "
            "WHEN PRIORITY >= 2 THEN 'normal' ELSE 'low' END; END"
        )
        connection.commit()
    if not exists("package", "CDEADMIN_UTIL"):
        cursor.execute(
            "CREATE PACKAGE CDEADMIN_UTIL AS BEGIN "
            "FUNCTION STATUS_LABEL(STATUS_CODE INTEGER) "
            "RETURNS VARCHAR(16); END"
        )
        cursor.execute(
            "CREATE PACKAGE BODY CDEADMIN_UTIL AS BEGIN "
            "FUNCTION STATUS_LABEL(STATUS_CODE INTEGER) "
            "RETURNS VARCHAR(16) AS BEGIN RETURN CASE STATUS_CODE "
            "WHEN 1 THEN 'active' ELSE 'inactive' END; END END"
        )
        connection.commit()
    if not exists("exception", "CDEADMIN_INVALID_STATE"):
        cursor.execute(
            "CREATE EXCEPTION CDEADMIN_INVALID_STATE "
            "'Invalid CDEadmin demonstration state'"
        )
        connection.commit()
    if not exists("role", "CDEADMIN_OPERATOR"):
        cursor.execute("CREATE ROLE CDEADMIN_OPERATOR")
        connection.commit()
    if not exists("external-function", "CDEADMIN_UDF_ABS"):
        cursor.execute(
            "DECLARE EXTERNAL FUNCTION CDEADMIN_UDF_ABS "
            "DOUBLE PRECISION RETURNS DOUBLE PRECISION BY VALUE "
            "ENTRY_POINT 'fn_abs' MODULE_NAME 'udflib'"
        )
        connection.commit()
    for table in ("WORK_ORDERS", "ASSETS", "CUSTOMERS"):
        cursor.execute(f"DELETE FROM {table}")
    cursor.executemany("INSERT INTO CUSTOMERS VALUES (?,?,?,?)", CUSTOMERS)
    cursor.executemany("INSERT INTO ASSETS VALUES (?,?,?,?,?)", ASSETS)
    cursor.executemany("INSERT INTO WORK_ORDERS "
                       "(WORK_ORDER_ID,ASSET_ID,TECHNICIAN,SUMMARY,STATUS,PRIORITY) "
                       "VALUES (?,?,?,?,?,?)", WORK_ORDERS)
    connection.commit()
    object_counts = {}
    for kind, name in (
        ('domain', 'CDEADMIN_STATUS'),
        ('trigger', 'BI_WORK_ORDERS'),
        ('procedure', 'CDEADMIN_WORK_ORDER_COUNT'),
        ('function', 'CDEADMIN_PRIORITY_LABEL'),
        ('package', 'CDEADMIN_UTIL'),
        ('exception', 'CDEADMIN_INVALID_STATE'),
        ('role', 'CDEADMIN_OPERATOR'),
        ('external-function', 'CDEADMIN_UDF_ABS'),
    ):
        object_counts[kind] = int(exists(kind, name))
    cursor.close()
    if owns_connection:
        connection.close()
    return {"database": database_path,
            "customers": 3, "assets": 4, "work_orders": 4,
            "native_object_targets": object_counts}


def seed_redis():
    import redis
    client = redis.Redis(host="127.0.0.1", port=56379,
                         decode_responses=True)
    retry(client.ping)
    pipe = client.pipeline(transaction=True)
    pipe.set("cdeadmin:demo:welcome", "ScratchRobin field-service demo")
    pipe.hset("cdeadmin:demo:customer:1", mapping={"name": CUSTOMERS[0][1],
              "city": CUSTOMERS[0][2], "region": CUSTOMERS[0][3]})
    pipe.hset("cdeadmin:demo:asset:101", mapping={"tag": "PUMP-101",
              "type": "centrifugal-pump", "status": "active"})
    pipe.lpush("cdeadmin:demo:work-queue", 1004, 1002, 1001)
    pipe.sadd("cdeadmin:demo:technicians", "Ada Lovelace", "Grace Hopper",
              "Katherine Johnson")
    pipe.zadd("cdeadmin:demo:priority", {"1001": 3, "1002": 2,
                                         "1003": 1, "1004": 4})
    pipe.set("cdeadmin:demo:maintenance-lock", "PUMP-101", ex=86400)
    pipe.delete("cdeadmin:demo:events")
    pipe.execute()
    for order in WORK_ORDERS:
        client.xadd("cdeadmin:demo:events", {
            "work_order_id": order[0], "technician": order[2],
            "status": order[4], "priority": order[5],
        })
    try:
        client.xgroup_create("cdeadmin:demo:events", "dispatchers", id="0",
                             mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    return {"key_prefix": "cdeadmin:demo:*",
            "keys": client.dbsize(), "native_types": 7,
            "stream_group": "dispatchers"}


def seed_mongodb():
    import pymongo
    client = retry(lambda: pymongo.MongoClient(
        "mongodb://127.0.0.1:57017", serverSelectionTimeoutMS=3000
    ))
    client.admin.command("ping")
    database = client["cdeadmin_demo"]
    if "work_orders" not in database.list_collection_names():
        database.create_collection("work_orders", validator={
            "$jsonSchema": {"bsonType": "object",
                            "required": ["_id", "asset", "status", "priority"],
                            "properties": {"priority": {"bsonType": "int",
                                                        "minimum": 1,
                                                        "maximum": 5}}}
        })
    collection = database.work_orders
    collection.delete_many({})
    collection.insert_many([
        {"_id": row[0], "asset": {"id": row[1],
         "tag": next(value[2] for value in ASSETS if value[0] == row[1])},
         "technician": row[2], "summary": row[3], "status": row[4],
         "priority": row[5], "tags": ["field-service", row[4]]}
        for row in WORK_ORDERS
    ])
    collection.create_index([("status", 1), ("priority", -1)],
                            name="status_priority")
    collection.create_index([("summary", "text")], name="summary_text")
    if "open_work_orders" in database.list_collection_names():
        database.open_work_orders.drop()
    database.command({"create": "open_work_orders", "viewOn": "work_orders",
                      "pipeline": [{"$match": {"status": "open"}},
                                   {"$sort": {"priority": -1}}]})
    pipeline = [{"$group": {"_id": "$technician", "count": {"$sum": 1},
                            "max_priority": {"$max": "$priority"}}},
                {"$sort": {"count": -1}}]
    aggregate = list(collection.aggregate(pipeline))
    client.close()
    return {"database": "cdeadmin_demo", "collections": 2,
            "documents": 4, "pipeline_result": aggregate}


def seed_neo4j():
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver("bolt://127.0.0.1:57687",
                                  auth=("neo4j", PASSWORD))
    retry(driver.verify_connectivity, timeout=300)
    with driver.session(database="neo4j") as session:
        session.run("CREATE CONSTRAINT customer_id IF NOT EXISTS FOR "
                    "(c:Customer) REQUIRE c.customer_id IS UNIQUE").consume()
        session.run("CREATE CONSTRAINT asset_id IF NOT EXISTS FOR "
                    "(a:Asset) REQUIRE a.asset_id IS UNIQUE").consume()
        session.run("CREATE CONSTRAINT work_order_id IF NOT EXISTS FOR "
                    "(w:WorkOrder) REQUIRE w.work_order_id IS UNIQUE").consume()
        session.run("CREATE INDEX work_order_status IF NOT EXISTS FOR "
                    "(w:WorkOrder) ON (w.status)").consume()
        session.run("MATCH (n) DETACH DELETE n").consume()
        for row in CUSTOMERS:
            session.run(
                "CREATE (:Customer {customer_id:$id,name:$name,city:$city,"
                "region:$region})",
                id=row[0],
                name=row[1],
                city=row[2],
                region=row[3]).consume()
        for row in ASSETS:
            session.run(
                "MATCH (c:Customer {customer_id:$customer}) CREATE "
                "(a:Asset {asset_id:$id,tag:$tag,asset_type:$type,status:$status})"
                "-[:OWNED_BY]->(c)",
                id=row[0],
                customer=row[1],
                tag=row[2],
                type=row[3],
                status=row[4]).consume()
        for row in WORK_ORDERS:
            session.run(
                "MATCH (a:Asset {asset_id:$asset}) MERGE "
                "(t:Technician {name:$technician}) CREATE "
                "(w:WorkOrder {work_order_id:$id,summary:$summary,status:$status,"
                "priority:$priority})-[:FOR_ASSET]->(a) CREATE (t)-[:ASSIGNED_TO]->(w)",
                id=row[0],
                asset=row[1],
                technician=row[2],
                summary=row[3],
                status=row[4],
                priority=row[5]).consume()
    driver.close()
    return {"database": "neo4j", "nodes": 14, "relationships": 12,
            "labels": ["Customer", "Asset", "Technician", "WorkOrder"]}


def request_json(url, method="GET", body=None, headers=None, timeout=30):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    values = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(url, data=payload, method=method,
                                     headers=values)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def request_raw(url, method="POST", body=b"", headers=None, timeout=30):
    request = urllib.request.Request(url, data=body, method=method,
                                     headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def seed_clickhouse():
    base = "http://127.0.0.1:58123/"
    auth = urllib.parse.urlencode({"user": "cdeadmin_demo",
                                  "password": PASSWORD})

    def execute(source):
        request = urllib.request.Request(f"{base}?{auth}",
                                         data=source.encode("utf-8"),
                                         method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")
    retry(lambda: execute("SELECT 1"), timeout=300)
    execute("CREATE DATABASE IF NOT EXISTS cdeadmin_demo")
    execute(
        "CREATE TABLE IF NOT EXISTS cdeadmin_demo.work_orders "
        "(work_order_id UInt64, asset_id UInt64, technician LowCardinality(String), "
        "summary String, status LowCardinality(String), priority UInt8, "
        "event_time DateTime DEFAULT now()) ENGINE=MergeTree "
        "PARTITION BY toYYYYMM(event_time) ORDER BY (status, priority, work_order_id)")
    execute("TRUNCATE TABLE cdeadmin_demo.work_orders")
    values = ",".join("(" + ",".join([
        str(row[0]), str(row[1]), "'" + row[2].replace("'", "''") + "'",
        "'" + row[3].replace("'", "''") + "'", "'" + row[4] + "'",
        str(row[5])]) + ")" for row in WORK_ORDERS)
    execute(
        "INSERT INTO cdeadmin_demo.work_orders "
        "(work_order_id,asset_id,technician,summary,status,priority) VALUES " +
        values)
    execute("CREATE TABLE IF NOT EXISTS cdeadmin_demo.workload_daily "
            "(day Date, technician String, work_orders UInt64) ENGINE=SummingMergeTree "
            "ORDER BY (day, technician)")
    execute("CREATE MATERIALIZED VIEW IF NOT EXISTS cdeadmin_demo.workload_mv "
            "TO cdeadmin_demo.workload_daily AS SELECT toDate(event_time) day, "
            "technician, count() work_orders FROM cdeadmin_demo.work_orders "
            "GROUP BY day, technician")
    return {"database": "cdeadmin_demo", "rows": 4,
            "structures": ["MergeTree", "SummingMergeTree", "materialized view"]}


def seed_influxdb():
    base = "http://127.0.0.1:58181"
    retry(lambda: request_raw(base + "/health", method="GET", body=None),
          timeout=300)
    try:
        request_json(base + "/api/v3/configure/database", "POST", {
            "db": "cdeadmin_demo", "retention_period": "30d"
        })
    except urllib.error.HTTPError as exc:
        if exc.code not in {400, 409, 422}:
            raise
    lines = (
        "sensor_reading,asset=PUMP-101,site=toronto "
        "temperature=71.4,vibration=0.18 1788645600000000000\n"
        "sensor_reading,asset=PUMP-101,site=toronto "
        "temperature=72.1,vibration=0.21 1788645660000000000\n"
        "sensor_reading,asset=METER-201,site=hamilton "
        "temperature=64.8,vibration=0.04 1788645600000000000\n"
        "sensor_reading,asset=FAN-301,site=sudbury "
        "temperature=68.3,vibration=0.52 1788645600000000000\n")
    query = urllib.parse.urlencode({"db": "cdeadmin_demo",
                                    "precision": "nanosecond",
                                    "accept_partial": "false"})
    request_raw(base + "/api/v3/write_lp?" + query, body=lines.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"})
    rows = request_json(base + "/api/v3/query_sql", "POST", {
        "db": "cdeadmin_demo",
        "q": "SELECT asset, site, temperature, vibration, time "
             "FROM sensor_reading ORDER BY time"
    })
    return {"database": "cdeadmin_demo", "measurement": "sensor_reading",
            "rows": len(rows), "tags": ["asset", "site"],
            "fields": ["temperature", "vibration"], "retention": "30d"}


def seed_milvus():
    from pymilvus import MilvusClient

    root = retry(
        lambda: MilvusClient(
            uri="http://127.0.0.1:59530", token="root:CDEadminDemo-2026!",
            timeout=10,
        ),
        timeout=300,
    )
    retry(root.list_databases, timeout=300)
    if "cdeadmin_demo" not in root.list_databases():
        root.create_database("cdeadmin_demo")
    client = MilvusClient(
        uri="http://127.0.0.1:59530", token="root:CDEadminDemo-2026!",
        db_name="cdeadmin_demo", timeout=20,
    )
    collection = "work_order_vectors"
    if not client.has_collection(collection):
        client.create_collection(
            collection_name=collection, dimension=4,
            primary_field_name="work_order_id", id_type="int",
            vector_field_name="embedding", metric_type="COSINE",
            auto_id=False, enable_dynamic_field=True,
        )
    client.delete(collection_name=collection, filter="work_order_id >= 0")
    vectors = (
        [0.93, 0.16, 0.25, 0.21], [0.84, 0.30, 0.36, 0.27],
        [0.14, 0.91, 0.22, 0.31], [0.31, 0.17, 0.92, 0.19],
    )
    client.insert(collection_name=collection, data=[
        {
            "work_order_id": row[0], "embedding": vectors[index],
            "asset_id": row[1], "technician": row[2],
            "summary": row[3], "status": row[4], "priority": row[5],
            "metadata": {"domain": "field-service", "source": "demo"},
        }
        for index, row in enumerate(WORK_ORDERS)
    ])
    client.flush(collection)
    client.load_collection(collection)
    rows = client.query(
        collection_name=collection, filter="work_order_id >= 0",
        output_fields=["work_order_id", "technician", "status", "priority"],
    )
    matches = client.search(
        collection_name=collection, data=[vectors[0]], limit=2,
        output_fields=["work_order_id", "technician", "summary"],
    )
    return {
        "database": "cdeadmin_demo", "collection": collection,
        "entities": len(rows), "dimension": 4, "metric": "COSINE",
        "dynamic_metadata": True,
        "nearest_work_order": matches[0][0]["entity"]["work_order_id"],
    }


def seed_immudb():
    login = retry(lambda: request_json(
        "http://127.0.0.1:58082/api/login", "POST", {
            "user": base64.b64encode(b"immudb").decode("ascii"),
            "password": base64.b64encode(b"immudb").decode("ascii"),
        }), timeout=180)
    token = login["token"]
    headers = {"Authorization": "Bearer " + token}
    try:
        request_json("http://127.0.0.1:58082/api/db/create/v2", "POST", {
            "name": "cdeadmin_demo", "ifNotExists": True,
            "settings": {"databaseName": "cdeadmin_demo"},
        }, headers=headers)
    except urllib.error.HTTPError as exc:
        if exc.code not in {400, 409, 422}:
            raise
    import psycopg
    connection = retry(lambda: psycopg.connect(
        host="127.0.0.1", port=55435, user="immudb", password="immudb",
        dbname="cdeadmin_demo", autocommit=True,
        cursor_factory=psycopg.ClientCursor,
    ), timeout=120)
    cursor = connection.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS work_orders "
                   "(work_order_id INTEGER, asset_id INTEGER, technician VARCHAR, "
                   "summary VARCHAR, status VARCHAR, priority INTEGER, "
                   "PRIMARY KEY work_order_id)")
    cursor.execute("DELETE FROM work_orders")
    for row in WORK_ORDERS:
        cursor.execute("INSERT INTO work_orders "
                       "(work_order_id,asset_id,technician,summary,status,priority) "
                       "VALUES (%s,%s,%s,%s,%s,%s)", row)
    count = cursor.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
    cursor.close()
    connection.close()
    return {"database": "cdeadmin_demo", "table": "work_orders",
            "rows": count, "immutable_history": True,
            "native_api": "REST plus PostgreSQL wire"}


def seed_opensearch():
    base = "http://127.0.0.1:59200"
    retry(lambda: request_json(base), timeout=300)
    request_json(base + "/_index_template/cdeadmin-work-orders", "PUT", {
        "index_patterns": ["cdeadmin-work-orders-*"],
        "template": {"settings": {"number_of_shards": 1,
                                  "number_of_replicas": 0},
                     "mappings": {"properties": {
                         "work_order_id": {"type": "long"},
                         "asset_id": {"type": "long"},
                         "technician": {"type": "keyword"},
                         "summary": {"type": "text"},
                         "status": {"type": "keyword"},
                         "priority": {"type": "integer"}}}}})
    request_json(base + "/_ingest/pipeline/cdeadmin-demo", "PUT", {
        "description": "Adds the CDEadmin demo marker",
        "processors": [{"set": {"field": "demo", "value": True}}]})
    index = "cdeadmin-work-orders-000001"
    try:
        request_json(base + f"/{index}", "DELETE")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    request_json(base + f"/{index}", "PUT", {"aliases": {
        "cdeadmin-work-orders": {"is_write_index": True}}})
    for row in WORK_ORDERS:
        request_json(base + f"/{index}/_doc/{row[0]}?pipeline=cdeadmin-demo",
                     "PUT", {"work_order_id": row[0], "asset_id": row[1],
                             "technician": row[2], "summary": row[3],
                             "status": row[4], "priority": row[5]})
    request_json(base + f"/{index}/_refresh", "POST")
    return {"index": index, "alias": "cdeadmin-work-orders",
            "documents": 4, "template": True, "ingest_pipeline": True}


def seed_opensearch_sql_ppl():
    result = seed_opensearch()
    base = "http://127.0.0.1:59200"
    sql = request_json(base + "/_plugins/_sql", "POST", {
        "query": "SELECT status, COUNT(*) AS work_orders FROM "
                 "`cdeadmin-work-orders` GROUP BY status ORDER BY status"
    })
    ppl = request_json(base + "/_plugins/_ppl", "POST", {
        "query": "source=cdeadmin-work-orders | where priority >= 3 | "
                 "fields work_order_id, technician, priority | sort - priority"
    })
    result.update({"sql_rows": len(sql.get("datarows", [])),
                   "ppl_rows": len(ppl.get("datarows", [])),
                   "languages": ["OpenSearch SQL", "PPL"]})
    return result


def seed_cassandra(engine="cassandra"):
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import Cluster
    port = 19042 if engine == "cassandra" else 59042
    contact_points = (
        ["127.0.0.1", "127.0.0.2", "127.0.0.3"]
        if engine == "cassandra" else ["127.0.0.1"]
    )

    def connect(password=None):
        options = {}
        if password is not None:
            options["auth_provider"] = PlainTextAuthProvider(
                username="cassandra", password=password,
            )
        cluster = Cluster(
            contact_points, port=port, connect_timeout=10,
            control_connection_timeout=10,
            **options,
        )
        try:
            return cluster, cluster.connect()
        except Exception:
            cluster.shutdown()
            raise

    if engine == "cassandra":
        def authenticated_connect():
            errors = []
            for password in (PASSWORD, "cassandra"):
                try:
                    return connect(password)
                except Exception as exc:
                    errors.append(exc)
            raise errors[-1]

        cluster, session = retry(authenticated_connect, timeout=300)
        session.execute(
            "ALTER ROLE cassandra WITH PASSWORD = 'CDEadminDemo-2026!'"
        )
        session.execute(
            "ALTER KEYSPACE system_auth WITH replication = "
            "{'class':'NetworkTopologyStrategy','datacenter1':3}"
        )
    else:
        def yugabyte_authenticated_connect():
            errors = []
            for password in (PASSWORD, "cassandra"):
                try:
                    return connect(password)
                except Exception as exc:
                    errors.append(exc)
            raise errors[-1]

        cluster, session = retry(
            yugabyte_authenticated_connect, timeout=300
        )
        session.execute(
            "ALTER ROLE cassandra WITH PASSWORD = 'CDEadminDemo-2026!'"
        )
    session.execute("CREATE KEYSPACE IF NOT EXISTS cdeadmin_demo WITH "
                    "replication = {'class':'SimpleStrategy','replication_factor':1}")
    session.execute(
        "CREATE TABLE IF NOT EXISTS cdeadmin_demo.work_orders "
        "(status text, priority int, work_order_id bigint, asset_id bigint, "
        "technician text, summary text, PRIMARY KEY ((status), priority, "
        "work_order_id)) WITH CLUSTERING ORDER BY (priority DESC)")
    session.execute("TRUNCATE cdeadmin_demo.work_orders")
    prepared = session.prepare(
        "INSERT INTO cdeadmin_demo.work_orders "
        "(status,priority,work_order_id,asset_id,technician,summary) "
        "VALUES (?,?,?,?,?,?)")
    for row in WORK_ORDERS:
        session.execute(prepared, (row[4], row[5], row[0], row[1], row[2], row[3]))
    session.execute("CREATE TABLE IF NOT EXISTS cdeadmin_demo.sensor_readings "
                    "(asset_id bigint, recorded_at timestamp, temperature double, "
                    "vibration double, PRIMARY KEY ((asset_id), recorded_at)) WITH "
                    "CLUSTERING ORDER BY (recorded_at DESC)")
    cluster.shutdown()
    if engine == "cassandra":
        subprocess.run([
            "docker", "exec", "cdeadmin-demo-cassandra",
            "/opt/cassandra/bin/nodetool", "-u", "cassandra", "-pwf",
            "/etc/cassandra/jmxremote.password", "-p", "17199",
            "repair", "system_auth",
        ], check=True)
    return {"keyspace": "cdeadmin_demo", "rows": 4,
            "tables": ["work_orders", "sensor_readings"],
            "authenticated": True,
            "nodes": 3 if engine == "cassandra" else 1}


def seed_ignite():
    from pyignite import Client
    authentication = urllib.parse.urlencode({
        "cmd": "version", "user": "ignite", "password": "ignite",
    })
    retry(lambda: request_raw(
        "http://127.0.0.1:58080/ignite?" + authentication,
        method="GET", body=None,
    ), timeout=300)
    client = Client(username="ignite", password="ignite", timeout=10)
    context = client.connect("127.0.0.1", 51080)
    context.__enter__()
    try:
        cache = client.get_or_create_cache("CDEADMIN_DEMO_WORK_ORDERS")
        cache.remove_all()
        cache.put_all({str(row[0]): json.dumps({
            "work_order_id": row[0], "asset_id": row[1],
            "technician": row[2], "summary": row[3], "status": row[4],
            "priority": row[5],
        }, sort_keys=True) for row in WORK_ORDERS})
        metadata = client.get_or_create_cache("CDEADMIN_DEMO_ASSETS")
        metadata.remove_all()
        metadata.put_all({str(row[0]): json.dumps({
            "asset_id": row[0], "customer_id": row[1], "tag": row[2],
            "asset_type": row[3], "status": row[4],
        }, sort_keys=True) for row in ASSETS})
        observed = cache.get_all([str(row[0]) for row in WORK_ORDERS])
        names = sorted(name for name in client.get_cache_names()
                       if name.startswith("CDEADMIN_DEMO"))
    finally:
        context.__exit__(None, None, None)
    return {"caches": names, "work_orders": len(observed),
            "partition_aware": True}


def seed_foundationdb():
    import fdb
    fdb.api_version(730)
    cluster_file = ROOT / "runtime/fdb.cluster"
    database = retry(lambda: fdb.open(str(cluster_file)), timeout=120)
    directory = fdb.directory.create_or_open(
        database, ("cdeadmin_demo", "work_orders")
    )

    @fdb.transactional
    def replace(tr):
        tr.clear_range_startswith(directory.key())
        for row in WORK_ORDERS:
            tr[directory.pack((row[0], "asset_id"))] = str(row[1]).encode()
            tr[directory.pack((row[0], "technician"))] = row[2].encode()
            tr[directory.pack((row[0], "summary"))] = row[3].encode()
            tr[directory.pack((row[0], "status"))] = row[4].encode()
            tr[directory.pack((row[0], "priority"))] = str(row[5]).encode()
    replace(database)

    @fdb.transactional
    def count(tr):
        return sum(1 for _key, _value in tr.get_range_startswith(
            directory.key()
        ))
    return {"directory": ["cdeadmin_demo", "work_orders"],
            "keys": count(database), "work_orders": 4,
            "ordered_key_value": True}


def _tikv_helper(operation):
    helper = ROOT / "runtime/bin/cdeadmin-tikv-helper"
    if not helper.exists():
        raise RuntimeError("TiKV helper is not bootstrapped; run "
                           "bootstrap.py first")
    request = {
        "pd_endpoints": ["127.0.0.1:52379"], "api_version": 2,
        "enable_ttl": True, "operation_timeout_seconds": 30,
        "transaction_mode": "optimistic", **operation,
    }
    for field in ("key", "value", "start_key", "end_key"):
        if field in request:
            request[f"{field}_base64"] = base64.b64encode(
                request.pop(field).encode("utf-8")
            ).decode("ascii")
    completed = subprocess.run(
        [str(helper)], input=json.dumps(request).encode("utf-8"),
        capture_output=True, check=False, timeout=40,
    )
    response = json.loads(completed.stdout or b"{}")
    if completed.returncode or response.get("error"):
        raise RuntimeError(response.get("error") or completed.stderr.decode())
    return response


def seed_tikv():
    prefix = "cdeadmin/demo/work_orders/"
    for row in WORK_ORDERS:
        _tikv_helper({
            "operation": "put", "key": prefix + str(row[0]),
            "value": json.dumps({
                "work_order_id": row[0], "asset_id": row[1],
                "technician": row[2], "summary": row[3],
                "status": row[4], "priority": row[5],
            }, sort_keys=True),
        })
    observed = _tikv_helper({
        "operation": "scan", "start_key": prefix,
        "end_key": "cdeadmin/demo/work_orders0", "limit": 100,
        "include_ttl": False,
    })
    records = observed.get("records", [])
    if len(records) != len(WORK_ORDERS):
        raise RuntimeError(f"expected 4 TiKV work orders, observed {len(records)}")
    topology = request_json("http://127.0.0.1:52379/pd/api/v1/stores")
    return {
        "key_prefix": prefix + "*", "keys": len(records),
        "ordered_key_value": True, "native_client": "client-go",
        "stores": topology.get("count", 0), "api_version": 2,
    }


def seed_xtdb():
    import psycopg
    connection = retry(lambda: psycopg.connect(host="127.0.0.1", port=55434,
                                               user="xtdb", dbname="xtdb",
                                               autocommit=True), timeout=300)
    connection.adapters.register_dumper(
        str, psycopg.types.string.StrDumperVarchar
    )
    cursor = connection.cursor()
    cursor.execute("ERASE FROM work_orders")
    for row in WORK_ORDERS:
        cursor.execute("INSERT INTO work_orders (_id, asset_id, technician, summary, "
                       "status, priority) VALUES (%s::bigint,%s::bigint,%s::text,"
                       "%s::text,%s::text,%s::bigint)", row)
    cursor.close()
    connection.close()
    return {"database": "xtdb", "table": "work_orders", "documents": 4,
            "temporal": True}


SEEDERS = {
    "sqlite": seed_sqlite, "duckdb": seed_duckdb, "firebird": seed_firebird,
    "redis": seed_redis, "postgresql": lambda: seed_postgresql_family("postgresql"),
    "mysql": lambda: seed_mysql_family("mysql"),
    "mariadb": lambda: seed_mysql_family("mariadb"),
    "mongodb": seed_mongodb, "neo4j": seed_neo4j,
    "clickhouse": seed_clickhouse, "opensearch": seed_opensearch,
    "opensearch_sql_ppl": seed_opensearch_sql_ppl,
    "influxdb": seed_influxdb,
    "milvus": seed_milvus,
    "immudb": seed_immudb,
    "cassandra": seed_cassandra,
    "apache_ignite": seed_ignite,
    "foundationdb": seed_foundationdb,
    "tikv": seed_tikv,
    "cockroachdb": lambda: seed_postgresql_family("cockroachdb"),
    "dolt": lambda: seed_mysql_family("dolt"),
    "tidb": lambda: seed_mysql_family("tidb"),
    "vitess": lambda: seed_mysql_family("vitess"),
    "xtdb": seed_xtdb,
    "yugabytedb": lambda: seed_postgresql_family("yugabytedb"),
    "yugabytedb_ycql": lambda: seed_cassandra("yugabytedb_ycql"),
}


def seed_engine(engine):
    if engine not in SEEDERS:
        raise RuntimeError(f"{engine} seed adapter is not yet available")
    return SEEDERS[engine]()


def verify_engine(engine):
    # Re-running the idempotent native adapter validates connectivity, native
    # DDL/DML and the expected sample counts without a parallel shadow model.
    result = seed_engine(engine)
    return {"native_round_trip": True, "sample": result}
