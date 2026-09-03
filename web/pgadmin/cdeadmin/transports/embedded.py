##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Constrained helper-process hosting for embedded actual engines."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .models import (
    TransportError,
    TransportIsolationError,
    TransportUnavailableError,
    endpoint_uuid,
    required_string,
)

try:
    import resource
except ImportError:  # pragma: no cover - bubblewrap is Linux-only
    resource = None


EMBEDDED_PROFILES = frozenset({
    ('duckdb', '1.5.2'),
    ('sqlite', '3.53.0'),
})
NETWORK_POLICIES = frozenset({'deny'})
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


class EmbeddedRuntimeError(TransportError):
    """An embedded helper grant or invocation is not safe to execute."""


@dataclass(frozen=True)
class SandboxCapabilities:
    """Capabilities an external sandbox adapter proves it enforces."""

    filesystem_isolation: bool
    network_denial: bool
    resource_limits: bool
    clean_environment: bool

    @property
    def complete(self):
        return all((
            self.filesystem_isolation,
            self.network_denial,
            self.resource_limits,
            self.clean_environment,
        ))


def _absolute_paths(values, field_name):
    if not isinstance(values, (tuple, list)):
        raise EmbeddedRuntimeError(f'{field_name} must be an array')
    result = []
    for value in values:
        path = Path(required_string(value, f'{field_name} item'))
        if not path.is_absolute():
            raise EmbeddedRuntimeError(
                f'{field_name} entries must be absolute'
            )
        normalized = path.resolve(strict=False)
        if normalized == Path('/') or normalized == Path.home():
            raise EmbeddedRuntimeError(
                f'{field_name} contains an over-broad grant'
            )
        result.append(str(normalized))
    if len(set(result)) != len(result):
        raise EmbeddedRuntimeError(f'{field_name} contains duplicates')
    return tuple(result)


@dataclass(frozen=True)
class EmbeddedRuntimeGrant:
    """Explicit filesystem, network, process, and resource authority."""

    endpoint_id: str
    endpoint_mode: str
    engine_id: str
    engine_version: str
    executable: str
    executable_sha256: str
    working_directory: str
    read_paths: tuple[str, ...] = field(default_factory=tuple)
    write_paths: tuple[str, ...] = field(default_factory=tuple)
    network_policy: str = 'deny'
    memory_bytes: int = 268435456
    cpu_seconds: int = 30
    wall_seconds: float = 30.0
    process_count: int = 1
    open_file_count: int = 64
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(
            self, 'endpoint_id', endpoint_uuid(self.endpoint_id)
        )
        if self.endpoint_mode != 'legacy_native':
            raise EmbeddedRuntimeError(
                'embedded helper host is limited to actual legacy engines'
            )
        for name in ('engine_id', 'engine_version'):
            object.__setattr__(
                self, name, required_string(getattr(self, name), name)
            )
        if (self.engine_id, self.engine_version) not in EMBEDDED_PROFILES:
            raise EmbeddedRuntimeError(
                'embedded engine profile is not an exact approved target'
            )
        executable = Path(required_string(self.executable, 'executable'))
        if not executable.is_absolute():
            raise EmbeddedRuntimeError('executable must be an absolute path')
        object.__setattr__(
            self, 'executable', str(executable.resolve(strict=False))
        )
        digest = required_string(
            self.executable_sha256, 'executable_sha256'
        ).casefold()
        if not _SHA256.fullmatch(digest):
            raise EmbeddedRuntimeError('executable_sha256 must be SHA-256')
        object.__setattr__(self, 'executable_sha256', digest)
        working = _absolute_paths(
            (self.working_directory,), 'working_directory'
        )[0]
        object.__setattr__(self, 'working_directory', working)
        reads = _absolute_paths(self.read_paths, 'read_paths')
        writes = _absolute_paths(self.write_paths, 'write_paths')
        if set(reads) & set(writes):
            raise EmbeddedRuntimeError(
                'read-only and writable grants must be distinct'
            )
        if working not in writes:
            raise EmbeddedRuntimeError(
                'working directory requires an explicit writable grant'
            )
        object.__setattr__(self, 'read_paths', reads)
        object.__setattr__(self, 'write_paths', writes)
        if self.network_policy not in NETWORK_POLICIES:
            raise EmbeddedRuntimeError('embedded network access is denied')
        integer_limits = (
            'memory_bytes', 'cpu_seconds', 'process_count',
            'open_file_count',
        )
        for name in integer_limits:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or (
                value < 1
            ):
                raise EmbeddedRuntimeError(f'{name} must be positive')
        if isinstance(self.wall_seconds, bool) or not isinstance(
            self.wall_seconds, (int, float)
        ) or self.wall_seconds <= 0:
            raise EmbeddedRuntimeError('wall_seconds must be positive')
        if not isinstance(self.environment, Mapping):
            raise EmbeddedRuntimeError('environment must be an object')
        clean_environment = {}
        for key, value in self.environment.items():
            key = required_string(key, 'environment key')
            value = required_string(value, 'environment value')
            if key.casefold() in {
                'home', 'pythonpath', 'ld_preload', 'dyld_insert_libraries',
            }:
                raise EmbeddedRuntimeError(
                    'environment contains a forbidden authority channel'
                )
            clean_environment[key] = value
        object.__setattr__(self, 'environment', clean_environment)

    def verify_executable(self):
        path = Path(self.executable)
        if not path.is_file():
            raise EmbeddedRuntimeError('embedded executable is unavailable')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != self.executable_sha256:
            raise EmbeddedRuntimeError(
                'embedded executable integrity check failed'
            )


@dataclass(frozen=True)
class HelperInvocation:
    """Opaque helper arguments and input; provider semantics stay outside."""

    endpoint_id: str
    argument_frames: tuple[str, ...]
    input_bytes: bytes = b''

    def __post_init__(self):
        object.__setattr__(
            self, 'endpoint_id', endpoint_uuid(self.endpoint_id)
        )
        if not isinstance(self.argument_frames, (tuple, list)):
            raise EmbeddedRuntimeError('argument_frames must be an array')
        arguments = tuple(
            required_string(item, 'argument_frames item')
            for item in self.argument_frames
        )
        if any('\x00' in item for item in arguments):
            raise EmbeddedRuntimeError('helper argument contains NUL')
        object.__setattr__(self, 'argument_frames', arguments)
        if not isinstance(self.input_bytes, (bytes, bytearray, memoryview)):
            raise EmbeddedRuntimeError('input_bytes must be bytes')
        object.__setattr__(self, 'input_bytes', bytes(self.input_bytes))


@dataclass(frozen=True)
class HelperResult:
    """Raw process result for interpretation by the owning provider."""

    return_code: int
    output_bytes: bytes
    error_bytes: bytes


class EmbeddedHelperHost:
    """Fail-closed host that requires a complete external sandbox."""

    def __init__(self, sandbox_adapter):
        capabilities = getattr(sandbox_adapter, 'capabilities', None)
        run = getattr(sandbox_adapter, 'run', None)
        if not isinstance(capabilities, SandboxCapabilities) or (
            not capabilities.complete
        ):
            raise TransportUnavailableError(
                'embedded host requires complete sandbox capabilities'
            )
        if not callable(run):
            raise TransportUnavailableError(
                'sandbox adapter must implement run'
            )
        self._sandbox = sandbox_adapter

    def invoke(self, grant, invocation):
        if not isinstance(grant, EmbeddedRuntimeGrant):
            raise EmbeddedRuntimeError('embedded runtime grant is required')
        if not isinstance(invocation, HelperInvocation):
            raise EmbeddedRuntimeError('helper invocation is required')
        if grant.endpoint_id != invocation.endpoint_id:
            raise TransportIsolationError(
                'helper invocation crossed its endpoint grant'
            )
        grant.verify_executable()
        result = self._sandbox.run(grant, invocation)
        if not isinstance(result, HelperResult):
            raise EmbeddedRuntimeError(
                'sandbox adapter returned an invalid helper result'
            )
        return result


class BubblewrapSandbox:
    """Linux bubblewrap adapter with no network namespace access."""

    capabilities = SandboxCapabilities(True, True, True, True)

    def __init__(self, executable='/usr/bin/bwrap'):
        path = Path(executable)
        if not path.is_absolute():
            raise EmbeddedRuntimeError(
                'bubblewrap executable must be absolute'
            )
        self.executable = str(path)

    def command(self, grant, invocation):
        command = [
            self.executable,
            '--die-with-parent',
            '--new-session',
            '--unshare-all',
            '--clearenv',
            '--proc', '/proc',
            '--dev', '/dev',
        ]
        for path in grant.read_paths:
            command.extend(('--ro-bind', path, path))
        for path in grant.write_paths:
            command.extend(('--bind', path, path))
        for key, value in sorted(grant.environment.items()):
            command.extend(('--setenv', key, value))
        command.extend(('--chdir', grant.working_directory, '--'))
        command.append(grant.executable)
        command.extend(invocation.argument_frames)
        return command

    @staticmethod
    def _limits(grant):
        if resource is None:
            raise TransportUnavailableError(
                'POSIX resource limits are unavailable'
            )
        resource.setrlimit(
            resource.RLIMIT_AS, (grant.memory_bytes, grant.memory_bytes)
        )
        resource.setrlimit(
            resource.RLIMIT_CPU, (grant.cpu_seconds, grant.cpu_seconds)
        )
        resource.setrlimit(
            resource.RLIMIT_NPROC,
            (grant.process_count, grant.process_count),
        )
        resource.setrlimit(
            resource.RLIMIT_NOFILE,
            (grant.open_file_count, grant.open_file_count),
        )

    def run(self, grant, invocation):
        if not Path(self.executable).is_file():
            raise TransportUnavailableError('bubblewrap is unavailable')
        for path in grant.read_paths + grant.write_paths:
            if not Path(path).exists():
                raise EmbeddedRuntimeError('granted path is unavailable')
        process = subprocess.Popen(
            self.command(grant, invocation),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=grant.working_directory,
            env={},
            shell=False,
            close_fds=True,
            preexec_fn=lambda: self._limits(grant),
        )
        try:
            output, error = process.communicate(
                invocation.input_bytes, timeout=grant.wall_seconds
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise EmbeddedRuntimeError('embedded helper timed out') from None
        return HelperResult(process.returncode, output, error)
