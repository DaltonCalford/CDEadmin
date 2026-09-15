##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Small, allowlisted HTTP failure receipts; never export provider payloads."""

import uuid
from types import SimpleNamespace

from .provider import _native_status_codes


def execution_failure_response(error, translate):
    operation = error.operation
    stage = operation.get('stage')
    messages = {
        'provider_response_unavailable': translate(
            'The provider operation did not return a usable response. Its '
            'outcome is unknown: an error does not prove that changes were '
            'rolled back. Inspect the native state before taking further '
            'action. The submitted plan is retired and will not be '
            'automatically retried.'),
        'observation_response_unavailable': translate(
            'The provider operation could not be observed. Its outcome is '
            'unconfirmed. No mutation was retried.'),
        'cancel_response_unavailable': translate(
            'The cancellation response is unavailable. Cancellation is not '
            'confirmed. The cancellation request will not be retried.'),
        'post_state_response_unavailable': translate(
            'The provider post-state check failed. This does not establish '
            'whether the operation succeeded or failed. No mutation was '
            'retried.'),
    }
    message = messages.get(stage, translate(
        'The provider operation response is unavailable. Inspect native '
        'state before taking further action. No mutation was retried.'))
    codes = _native_status_codes(SimpleNamespace(
        native_status_codes=operation.get('native_status_codes', ())))
    if codes:
        message += ' ' + translate('Native status codes: ') + ', '.join(
            str(code) for code in codes)
    receipt = {
        'stage': stage if stage in messages else 'response_unavailable',
        'unknown_outcome': operation.get('unknown_outcome') is True,
        'automatic_mutation_retry': False,
        'native_status_codes': codes,
    }
    try:
        receipt['operation_id'] = str(uuid.UUID(operation.get('operation_id')))
    except (ValueError, TypeError, AttributeError):
        pass
    return {
        'status': 502, 'success': 0,
        'info': 'PROVIDER_OPERATION_RESPONSE_UNAVAILABLE',
        'errormsg': message, 'data': {'control_operation': receipt},
    }
