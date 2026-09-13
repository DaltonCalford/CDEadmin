#!/usr/bin/env python3
"""Idempotently register the reference demos for one CDEadmin user."""

from __future__ import annotations

import argparse
import builtins
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
WEB = REPOSITORY / "web"
sys.path.insert(0, str(WEB))

import config  # noqa: E402

builtins.SERVER_MODE = None


PROFILE_OVERRIDES = {
    "opensearch_sql_ppl": "opensearch-sql-ppl",
    "yugabytedb": "yugabytedb-native",
    "yugabytedb_ycql": "yugabytedb-ycql",
}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="CDEadmin user email")
    parser.add_argument(
        "--profiles", type=Path,
        default=ROOT / "runtime/connection_profiles.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    return parser.parse_args()


def backup_database(path):
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = path.with_name(f"{path.name}.demo-profiles-{timestamp}.bak")
    with sqlite3.connect(path) as source, sqlite3.connect(target) as backup:
        source.backup(backup)
    return target


def load_profiles(path):
    if not path.is_file():
        raise RuntimeError(
            f"generated profiles are missing: {path}; run bootstrap.py first"
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    profiles = document.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 26:
        raise RuntimeError("expected exactly 26 generated demo profiles")
    return document.get("host", "127.0.0.1"), profiles


def registration_for(item, registrations):
    profile_id = PROFILE_OVERRIDES.get(
        item["engine"], f"{item['engine'].replace('_', '-')}-native"
    )
    try:
        return registrations[profile_id]
    except KeyError as exc:
        raise RuntimeError(
            f"no active CDEadmin registration profile for {item['engine']}"
        ) from exc


def route_for(item, host, registration, provider_route_options):
    item = dict(item)
    engine = item['engine']
    if engine == 'tikv' and item.get('api_version') == 2:
        item.pop('enable_ttl', None)  # UI switch is API-v1 TTL encoding only.
    if item.get('user'):
        item.setdefault('username', item['user'])
    if engine == 'milvus' and item.get('password'):
        item.setdefault('auth_kind', 'basic')
    if engine == 'redis' and not item.get('password'):
        item.pop('username', None)
        item['auth_mode'] = 'none'
    if engine in {'clickhouse', 'influxdb', 'opensearch',
                  'opensearch_sql_ppl', 'milvus', 'xtdb'}:
        item.setdefault('tls_mode', 'disable')
    if engine in {'cassandra', 'yugabytedb_ycql'}:
        item.setdefault('tls_mode', 'disabled')
        item.setdefault('compression', 'none')
    if engine == 'neo4j':
        item.setdefault('tls_mode', 'disabled')
        item.setdefault('routing', False)
    if engine == 'mysql':
        # MySQL's caching_sha2_password authentication needs a protected
        # exchange; the shipped image supports TLS with its generated cert.
        item.setdefault('ssl_disabled', False)
        item.setdefault('ssl_verify_cert', False)
        item.setdefault('ssl_verify_identity', False)
    if engine in {'dolt', 'tidb', 'vitess'}:
        item.setdefault('ssl_disabled', True)
        item.setdefault('ssl_verify_cert', False)
        item.setdefault('ssl_verify_identity', False)
    if engine == 'mariadb':
        item.setdefault('ssl', False)
        item.setdefault('ssl_verify_cert', False)
    if engine in {'postgresql', 'cockroachdb', 'yugabytedb', 'immudb'}:
        item.setdefault('sslmode', 'disable')
    if item['engine'] == 'vitess':
        item.setdefault('vtgate_http_port', item['http_port'])
        item.setdefault('vtgate_http_host', host)
        item.setdefault('vtgate_http_tls_mode', 'disable')
    database = item.get("database")
    if database is None:
        database = "default"
    if registration["route_kind"] == "embedded_file":
        database_path = str(Path(database).expanduser().resolve())
        route = {
            "database": database_path,
            "filesystem_root": str(Path(database_path).parent),
            "database_create_root": str(Path(database_path).parent),
        }
    else:
        route = {
            "host": host,
            "port": item.get("port", registration["default_port"]),
            "user": item.get("user") or "anonymous",
            "database": database,
            "connection_timeout": 10,
        }
    form = {}
    for field in registration.get("connection_fields", []):
        field_id = field["field_id"]
        if field_id not in item:
            continue
        value = item[field_id]
        if field["control"] == "text" and isinstance(value, list):
            value = ",".join(str(part) for part in value)
        form[f"cde_route_{field_id}"] = value
    route = provider_route_options(registration, form, route)
    # Generic server fields are not native transport arguments. Retain the
    # declared options, but discard base keys rejected by these adapters.
    if engine in {'apache_ignite', 'cassandra', 'yugabytedb_ycql',
                  'foundationdb', 'tikv', 'opensearch', 'opensearch_sql_ppl'}:
        route.pop('database', None)
    if engine in {'clickhouse', 'influxdb', 'opensearch', 'opensearch_sql_ppl',
                  'milvus', 'xtdb', 'apache_ignite', 'cassandra',
                  'yugabytedb_ycql', 'neo4j'}:
        route.pop('user', None)
        route.pop('connection_timeout', None)
    if engine == 'redis' and not item.get('password'):
        route.pop('user', None)
    return route


def update_visibility(db, Preferences, UserPreference, user_id):
    preferences = Preferences.query.filter(
        Preferences.name.like("show_connector_%")
    ).all()
    for preference in preferences:
        current = UserPreference.query.filter_by(
            pid=preference.id, uid=user_id
        ).first()
        if current is None:
            db.session.add(UserPreference(
                pid=preference.id, uid=user_id, value="True"
            ))
        else:
            current.value = "True"
    return len(preferences)


def database_target_configuration(item, route, registration):
    """Move provider database-form values out of the server route."""
    fields = registration['form_contract']['database']['forms'][
        'define'
    ]['fields']
    configuration = {}
    for field in fields:
        field_id = field['field_id']
        if field_id in {'database', 'display_name', 'target_id'}:
            continue
        if field_id in item:
            configuration[field_id] = item[field_id]
        elif field_id in route:
            configuration[field_id] = route.pop(field_id)
        elif 'default' in field:
            configuration[field_id] = field['default']
    return configuration


def retain_database_target(
        db, EndpointDatabaseTarget, server, item, route, registration):
    """Persist the sample database below its server/instance endpoint."""
    endpoint = server.endpoint_profile
    database = route.get('database', item.get('database'))
    if database is None:
        database = 'default'
    database = str(database)
    name = Path(database).name if '/' in database else database
    configuration = database_target_configuration(
        item, route, registration
    )
    existing = next((
        target for target in endpoint.database_targets
        if target.database == database
    ), None)
    for target in endpoint.database_targets:
        target.active = False
    if existing is None:
        existing = EndpointDatabaseTarget(
            id=str(uuid.uuid4()), endpoint_id=endpoint.id,
            display_name=name, database=database,
            configuration=json.dumps(
                configuration, sort_keys=True, separators=(',', ':')
            ), active=True,
        )
        db.session.add(existing)
    else:
        existing.display_name = name
        existing.configuration = json.dumps(
            configuration, sort_keys=True, separators=(',', ':')
        )
        existing.active = True
    if registration['route_kind'] == 'network':
        route.pop('database', None)
    endpoint.routes[0].configuration = json.dumps(
        route, sort_keys=True, separators=(',', ':')
    )
    endpoint.profile_generation = str(uuid.uuid4())


def register(args, app=None):
    host, profile_items = load_profiles(args.profiles.resolve())
    from pgadmin import create_app

    app = app or create_app(config.APP_NAME + "-demo-profile-registration")
    with app.app_context():
        from pgadmin.cdeadmin.endpoints import (
            provider_route_options, registration_profiles,
        )
        from pgadmin.model import (
            db, EndpointDatabaseTarget, EndpointProfile, Preferences,
            Server, ServerGroup, User, UserPreference,
        )

        user = User.query.filter_by(email=args.user).first()
        if user is None:
            raise RuntimeError(f"CDEadmin user does not exist: {args.user}")
        group = ServerGroup.query.filter_by(user_id=user.id).order_by(
            ServerGroup.id
        ).first()
        if group is None:
            group = ServerGroup(user_id=user.id, name="Connectors")
            db.session.add(group)
            db.session.flush()
        registrations = {
            item["profile_id"]: item for item in registration_profiles()
        }
        created = []
        updated = []
        for item in profile_items:
            registration = registration_for(item, registrations)
            route = route_for(
                item, host, registration, provider_route_options
            )
            existing = Server.query.join(EndpointProfile).filter(
                Server.user_id == user.id,
                Server.name == item["name"],
                EndpointProfile.profile_id == registration["profile_id"],
            ).first()
            database = route.get("database", item.get("database") or "default")
            port = item.get("port") or registration.get("default_port") or 1
            username = item.get("user") or "anonymous"
            if existing is None:
                server = Server(
                    user_id=user.id, servergroup_id=group.id,
                    name=item["name"], host=(
                        None if registration["route_kind"] == "embedded_file"
                        else host
                    ), port=port, maintenance_db=None,
                    username=(
                        "embedded-process"
                        if registration["route_kind"] == "embedded_file"
                        else username
                    ), save_password=0, shared=False,
                    use_ssh_tunnel=0, tunnel_port=22,
                    tunnel_authentication=0, tunnel_prompt_password=0,
                    tunnel_keep_alive=0, kerberos_conn=False,
                    connection_params={}, tags=[{
                        "text": "cdeadmin-reference-demo",
                        "color": "#31708f",
                    }],
                    comment="Managed by the CDEadmin reference demo fixture.",
                )
                server._cde_endpoint_registration = registration
                server._cde_endpoint_route_configuration = route
                db.session.add(server)
                db.session.flush()
                retain_database_target(
                    db, EndpointDatabaseTarget, server, item, route,
                    registration,
                )
                created.append(item["name"])
            else:
                existing.host = (
                    None if registration["route_kind"] == "embedded_file"
                    else host
                )
                existing.port = port
                # A provider endpoint is a server/listener registration.
                # Its databases are retained exclusively as child targets.
                existing.maintenance_db = None
                existing.username = (
                    "embedded-process"
                    if registration["route_kind"] == "embedded_file"
                    else username
                )
                retain_database_target(
                    db, EndpointDatabaseTarget, existing, item, route,
                    registration,
                )
                runtime_identity = existing.endpoint_profile.runtime_identity
                runtime_identity.verification_state = "stale"
                updated.append(item["name"])
        visible = update_visibility(db, Preferences, UserPreference, user.id)
        if args.dry_run:
            db.session.rollback()
        else:
            db.session.commit()
        return {
            "user": args.user, "user_id": user.id,
            "created": created, "updated": updated,
            "connectors_enabled": visible, "dry_run": args.dry_run,
            "saved_passwords": False,
        }


def main():
    args = arguments()
    database = Path(config.SQLITE_PATH)
    backup = None
    if not args.dry_run and not args.no_backup:
        backup = backup_database(database)
    result = register(args)
    result["backup"] = str(backup) if backup else None
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
