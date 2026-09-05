##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Report scheduler authority and occurrence value contracts."""

from __future__ import annotations


GRANT_STATES = frozenset({'active', 'revoked', 'expired'})
OCCURRENCE_STATES = frozenset({
    'scheduled', 'claimed', 'executing', 'delivering', 'delivered',
    'failed', 'cancel_requested', 'cancelled', 'outcome_unknown',
})
TERMINAL_STATES = frozenset({
    'delivered', 'failed', 'cancelled', 'outcome_unknown',
})


class ReportSchedulerError(RuntimeError):
    """A scheduler request cannot be admitted or completed safely."""


class ReportSchedulerUnavailable(ReportSchedulerError):
    """The worker authority or runtime dependency is unavailable."""


class ReportSchedulerConflict(ReportSchedulerError):
    """A grant, occurrence, lease, or revision changed concurrently."""


class ReportSchedulerAccessError(ReportSchedulerError):
    """A worker or owner is outside a delegation's exact scope."""
