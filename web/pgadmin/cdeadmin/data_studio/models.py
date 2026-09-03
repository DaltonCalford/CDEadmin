##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-neutral Data Studio contributions and presentation records."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


CHANNELS = frozenset({'progress', 'notice', 'diagnostic', 'plan'})
OCCURRENCE_STATES = frozenset({
    'created',
    'provider_active',
    'provider_terminal',
    'submission_failed',
})
CANCELLATION_STATES = frozenset({
    'not_requested',
    'requested',
    'provider_response_recorded',
    'provider_response_failed',
})


class DataStudioError(RuntimeError):
    """Base error for common Data Studio operations."""


class DataStudioAccessError(DataStudioError):
    """A request cannot be admitted without crossing an authority boundary."""


class FixtureExecutionError(DataStudioAccessError):
    """A non-production story attempted to enter an operational path."""


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataStudioError(f'{field_name} must not be empty')
    return value.strip()


def _profiles(value: frozenset[str]) -> frozenset[str]:
    result = frozenset(
        _required_string(item, 'language_profiles item')
        for item in value
    )
    if not result:
        raise DataStudioError('language_profiles must not be empty')
    return result


@dataclass(frozen=True)
class LanguageContribution:
    """Editor language metadata supplied by a provider or model package."""

    language_profile: str
    title: str
    editor_mode: str
    model_families: frozenset[str]
    source_kind: str = 'text'

    def __post_init__(self):
        names = ('language_profile', 'title', 'editor_mode', 'source_kind')
        for name in names:
            object.__setattr__(
                self, name, _required_string(getattr(self, name), name)
            )
        object.__setattr__(self, 'model_families', _profiles(
            self.model_families
        ))


@dataclass(frozen=True)
class CompletionContribution:
    """Completion callback selected by an opaque language profile."""

    contribution_id: str
    language_profiles: frozenset[str]
    complete: Callable[[object, Mapping[str, Any]], object]

    def __post_init__(self):
        object.__setattr__(
            self,
            'contribution_id',
            _required_string(self.contribution_id, 'contribution_id'),
        )
        object.__setattr__(
            self, 'language_profiles', _profiles(self.language_profiles)
        )
        if not callable(self.complete):
            raise DataStudioError('completion callback must be callable')


@dataclass(frozen=True)
class SessionContribution:
    """Session and transaction-presentation callbacks."""

    contribution_id: str
    language_profiles: frozenset[str]
    open_session: Callable[
        [object, Mapping[str, Any]], Mapping[str, Any]
    ]
    describe_transaction: Callable[
        [object, Mapping[str, Any]], Mapping[str, Any]
    ]

    def __post_init__(self):
        object.__setattr__(
            self,
            'contribution_id',
            _required_string(self.contribution_id, 'contribution_id'),
        )
        object.__setattr__(
            self, 'language_profiles', _profiles(self.language_profiles)
        )
        if not callable(self.open_session) or not callable(
            self.describe_transaction
        ):
            raise DataStudioError('session callbacks must be callable')


@dataclass(frozen=True)
class ExecutionContribution:
    """Provider-owned execution callbacks and opaque returned state."""

    contribution_id: str
    language_profiles: frozenset[str]
    execute: Callable[[object, Mapping[str, Any]], Mapping[str, Any]]
    poll: Callable[
        [object, Mapping[str, Any]], tuple[
            Mapping[str, Any], Mapping[str, Any] | None
        ]
    ]
    cancel: Callable[[object, Mapping[str, Any]], Mapping[str, Any]]

    def __post_init__(self):
        object.__setattr__(
            self,
            'contribution_id',
            _required_string(self.contribution_id, 'contribution_id'),
        )
        object.__setattr__(
            self, 'language_profiles', _profiles(self.language_profiles)
        )
        if not all(callable(item) for item in (
            self.execute, self.poll, self.cancel
        )):
            raise DataStudioError('execution callbacks must be callable')


@dataclass(frozen=True)
class ChannelMessage:
    """One redacted provider message on a typed presentation channel."""

    channel: str
    occurred_at: str
    payload: Mapping[str, Any]

    def __post_init__(self):
        if self.channel not in CHANNELS:
            raise DataStudioError('Data Studio channel is unknown')

    def to_dict(self) -> dict[str, Any]:
        return {
            'channel': self.channel,
            'occurred_at': self.occurred_at,
            'payload': copy.deepcopy(dict(self.payload)),
        }


@dataclass
class StudioSession:
    """Common handle over a provider session or a non-operational fixture."""

    endpoint_id: str
    language_profile: str
    provider_session: Mapping[str, Any]
    session_contribution_id: str | None
    execution_contribution_id: str | None
    completion_contribution_id: str | None
    fixture: bool = False
    execution_enabled: bool = True
    transaction_presentation: Mapping[str, Any] | None = None

    @property
    def session_id(self) -> str:
        return str(self.provider_session['session_id'])

    def to_dict(self) -> dict[str, Any]:
        return {
            'endpoint_id': self.endpoint_id,
            'language_profile': self.language_profile,
            'provider_session': copy.deepcopy(dict(self.provider_session)),
            'session_contribution_id': self.session_contribution_id,
            'execution_contribution_id': self.execution_contribution_id,
            'completion_contribution_id': self.completion_contribution_id,
            'fixture': self.fixture,
            'execution_enabled': self.execution_enabled,
            'transaction_presentation': copy.deepcopy(
                self.transaction_presentation
            ),
        }


@dataclass
class ExecutionOccurrence:
    """UI request occurrence, deliberately not a transaction state machine."""

    occurrence_id: str
    execution_id: str
    session_id: str
    language_profile: str
    request_summary: Mapping[str, Any]
    redaction_keys: tuple[str, ...] = field(default_factory=tuple)
    lifecycle_state: str = 'created'
    cancellation_state: str = 'not_requested'
    operation: Mapping[str, Any] | None = None
    result: Mapping[str, Any] | None = None
    transaction_presentation: Mapping[str, Any] | None = None
    channels: list[ChannelMessage] = field(default_factory=list)

    def __post_init__(self):
        if self.lifecycle_state not in OCCURRENCE_STATES:
            raise DataStudioError('execution occurrence state is unknown')
        if self.cancellation_state not in CANCELLATION_STATES:
            raise DataStudioError('cancellation request state is unknown')

    def to_dict(self) -> dict[str, Any]:
        return {
            'occurrence_id': self.occurrence_id,
            'execution_id': self.execution_id,
            'session_id': self.session_id,
            'language_profile': self.language_profile,
            'request_summary': copy.deepcopy(dict(self.request_summary)),
            'lifecycle_state': self.lifecycle_state,
            'cancellation_state': self.cancellation_state,
            'operation': copy.deepcopy(self.operation),
            'result': copy.deepcopy(self.result),
            'transaction_presentation': copy.deepcopy(
                self.transaction_presentation
            ),
            'channels': [item.to_dict() for item in self.channels],
        }
