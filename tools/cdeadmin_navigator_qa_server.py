#!/usr/bin/env python3
"""Run a loopback-only UI QA instance with an isolated application database.

Uses real provider registration and application routes. No user application
configuration or saved passwords are copied. Database credentials are entered
through the normal endpoint prompts; this tool does not bypass authorization.
Source tools/reference_engine_demos/environment.sh before launching this tool
to load the same native client libraries as the normal demo application.
"""
import argparse
import builtins
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--port', type=int, default=5052)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    builtins.SERVER_MODE = None
    import config
    config.SERVER_MODE = False
    config.TESTING = True
    # The isolated desktop account has no retained database passwords and
    # must not access the user's OS keyring during browser automation.
    config.MASTER_PASSWORD_REQUIRED = False
    config.USE_OS_SECRET_STORAGE = False
    config.DATA_DIR = str(workspace)
    config.SQLITE_PATH = str(workspace / 'application.db')
    config.SESSION_DB_PATH = str(workspace / 'sessions')
    config.STORAGE_DIR = str(workspace / 'storage')
    config.LOG_FILE = str(workspace / 'application.log')
    config.AZURE_CREDENTIAL_CACHE_DIR = str(workspace / 'azure')
    from pgadmin import create_app
    from pgadmin.model import User
    from reference_engine_demos.register_demo_profiles import register
    app = create_app('ScratchRobin-navigator-QA')
    app.PGADMIN_INT_KEY = ''  # Same loopback desktop mode as the entry point.
    app.PGADMIN_RUNTIME = False
    app.PGADMIN_EXTERNAL_AUTH_SOURCE = 'internal'
    app.config['sessions'] = {}
    config.EFFECTIVE_SERVER_PORT = args.port
    app.run_before_app_start()
    with app.app_context():
        email = User.query.first().email
    register(SimpleNamespace(
        user=email, dry_run=False,
        profiles=ROOT / 'tools/reference_engine_demos/runtime/'
        'connection_profiles.json'), app=app)
    app.run(host='127.0.0.1', port=args.port, use_reloader=False)


if __name__ == '__main__':
    main()
