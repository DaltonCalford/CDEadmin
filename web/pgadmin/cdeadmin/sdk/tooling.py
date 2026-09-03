##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Constrained provider-owned external administration tool execution.

The common runner validates grants and reports process observations. It does
not understand a database command, infer a remote transaction outcome, or
convert local process exit into remote finality. Providers own argument
construction and any post-state verification.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


MAX_TOOL_OUTPUT = 2 * 1024 * 1024


class ProviderToolError(RuntimeError):
    """An external provider tool grant or invocation is unsafe."""


def _safe_root(value):
    if not isinstance(value, str) or not value:
        raise ProviderToolError('tool workspace must be an absolute path')
    path = Path(value)
    if not path.is_absolute():
        raise ProviderToolError('tool workspace must be an absolute path')
    path = path.resolve(strict=False)
    forbidden = {Path('/'), Path.home().resolve(strict=False)}
    if path in forbidden:
        raise ProviderToolError('tool workspace grant is over-broad')
    return path


@dataclass(frozen=True)
class ProviderToolGrant:
    """Explicit executable, filesystem, network, and resource authority."""

    executable_id: str
    workspace: str
    endpoint_host: str
    endpoint_port: int
    timeout_seconds: int = 300
    max_output_bytes: int = MAX_TOOL_OUTPUT
    secret_environment_names: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.executable_id, str) or not self.executable_id:
            raise ProviderToolError('tool executable ID is required')
        object.__setattr__(self, 'workspace', str(_safe_root(self.workspace)))
        if not isinstance(self.endpoint_host, str) or not self.endpoint_host:
            raise ProviderToolError('tool endpoint host is required')
        if isinstance(self.endpoint_port, bool) or not isinstance(
            self.endpoint_port, int
        ) or not 1 <= self.endpoint_port <= 65535:
            raise ProviderToolError('tool endpoint port is invalid')
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, int
        ) or not 1 <= self.timeout_seconds <= 3600:
            raise ProviderToolError('tool timeout is invalid')
        if isinstance(self.max_output_bytes, bool) or not isinstance(
            self.max_output_bytes, int
        ) or not 1024 <= self.max_output_bytes <= MAX_TOOL_OUTPUT:
            raise ProviderToolError('tool output limit is invalid')
        if not isinstance(self.secret_environment_names, tuple) or any(
            not isinstance(name, str) or not name or
            not name.replace('_', '').isalnum() or name.upper() != name
            for name in self.secret_environment_names
        ):
            raise ProviderToolError(
                'tool secret environment grant is invalid'
            )


class ProviderToolRunner:
    """Run one allowlisted executable without a shell or inherited secrets."""

    def __init__(self, executable_allowlist):
        if not isinstance(executable_allowlist, dict):
            raise ProviderToolError('tool executable allowlist is invalid')
        self._allowlist = dict(executable_allowlist)

    def available(self):
        return {
            key: self._resolve(value) is not None
            for key, value in self._allowlist.items()
        }

    @staticmethod
    def _resolve(value):
        if not isinstance(value, str) or not value:
            return None
        candidate = value if os.path.isabs(value) else shutil.which(value)
        if candidate is None:
            return None
        path = Path(candidate).resolve(strict=False)
        return path if path.is_file() and os.access(path, os.X_OK) else None

    def run(
        self, grant, arguments, input_bytes=b'', secret_config=None,
        secret_argument='--config={path}', secret_suffix='.yaml',
        redact_values=(), secret_environment=None,
    ):
        if not isinstance(grant, ProviderToolGrant):
            raise ProviderToolError('provider tool grant is required')
        declared = self._allowlist.get(grant.executable_id)
        executable = self._resolve(declared)
        if executable is None:
            raise ProviderToolError('provider tool executable is unavailable')
        if not isinstance(arguments, (list, tuple)) or any(
            not isinstance(item, str) or '\x00' in item for item in arguments
        ):
            raise ProviderToolError('provider tool arguments are invalid')
        if not isinstance(input_bytes, (bytes, bytearray, memoryview)):
            raise ProviderToolError('provider tool input must be bytes')
        workspace = Path(grant.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        config_path = None
        command = [str(executable), *arguments]
        environment = {
            'PATH': '/usr/bin:/bin',
            'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8',
        }
        if secret_environment is not None:
            if not isinstance(secret_environment, dict) or any(
                not isinstance(key, str) or not key or '\x00' in key or
                not isinstance(value, str) or '\x00' in value
                for key, value in secret_environment.items()
            ):
                raise ProviderToolError(
                    'provider tool secret environment is invalid'
                )
            if not set(secret_environment).issubset(
                grant.secret_environment_names
            ):
                raise ProviderToolError(
                    'provider tool secret environment key is not granted'
                )
            environment.update(secret_environment)
        try:
            if secret_config is not None:
                if not isinstance(secret_config, (bytes, bytearray)):
                    raise ProviderToolError(
                        'provider tool secret config must be bytes'
                    )
                descriptor, name = tempfile.mkstemp(
                    prefix='.cdeadmin-tool-', suffix=secret_suffix,
                    dir=workspace
                )
                config_path = Path(name)
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(bytes(secret_config))
                    stream.flush()
                    os.fsync(stream.fileno())
                if not isinstance(secret_argument, str) or (
                    '{path}' not in secret_argument
                ):
                    raise ProviderToolError(
                        'provider tool secret argument is invalid'
                    )
                command.append(secret_argument.format(path=config_path))
            try:
                completed = subprocess.run(
                    command,
                    input=bytes(input_bytes),
                    cwd=workspace,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=grant.timeout_seconds,
                    check=False,
                    shell=False,
                    close_fds=True,
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderToolError('provider tool timed out') from exc
            output = completed.stdout[:grant.max_output_bytes]
            error = completed.stderr[:grant.max_output_bytes]
            output_text = output.decode('utf-8', errors='replace')
            error_text = error.decode('utf-8', errors='replace')
            for secret in redact_values:
                if isinstance(secret, str) and secret:
                    output_text = output_text.replace(secret, '[redacted]')
                    error_text = error_text.replace(secret, '[redacted]')
            return {
                'executable_id': grant.executable_id,
                'executable_sha256': hashlib.sha256(
                    executable.read_bytes()
                ).hexdigest(),
                'workspace': str(workspace),
                'network_grant': {
                    'host': grant.endpoint_host,
                    'port': grant.endpoint_port,
                },
                'return_code': completed.returncode,
                'stdout': output_text,
                'stderr': error_text,
                'stdout_truncated': len(completed.stdout) > len(output),
                'stderr_truncated': len(completed.stderr) > len(error),
                'remote_finality_inferred': False,
                'local_process_observation_only': True,
            }
        finally:
            if config_path is not None and config_path.exists():
                try:
                    size = config_path.stat().st_size
                    with config_path.open('r+b') as stream:
                        stream.write(b'\x00' * size)
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    config_path.unlink(missing_ok=True)
