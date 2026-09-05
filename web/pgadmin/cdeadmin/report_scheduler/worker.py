##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Bounded one-shot worker for delegated semantic report occurrences."""

from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import PurePosixPath

from .models import ReportSchedulerError


MEDIA_TYPES = {
    'csv': 'text/csv',
    'json': 'application/json',
    'jsonl': 'application/x-ndjson',
    'xlsx': ('application/vnd.openxmlformats-officedocument.'
             'spreadsheetml.sheet'),
    'svg': 'image/svg+xml',
    'pdf': 'application/pdf',
}


class ReportSchedulerWorker:
    """Run claimed work without an interactive Flask user session."""

    def __init__(
        self, scheduler, studio, result_service, semantic_service,
        delivery_service, poll_interval=0.1, max_polls=600,
    ):
        self.scheduler = scheduler
        self.studio = studio
        self.result_service = result_service
        self.semantic_service = semantic_service
        self.delivery_service = delivery_service
        if not isinstance(poll_interval, (int, float)) or not (
                0.01 <= poll_interval <= 60):
            raise ReportSchedulerError('worker poll interval is invalid')
        if isinstance(max_polls, bool) or not isinstance(max_polls, int) or (
                not 1 <= max_polls <= 100000):
            raise ReportSchedulerError('worker poll limit is invalid')
        self.poll_interval = float(poll_interval)
        self.max_polls = max_polls

    def run_once(self, worker_id, max_occurrences=10):
        if isinstance(max_occurrences, bool) or not isinstance(
                max_occurrences, int) or not 1 <= max_occurrences <= 100:
            raise ReportSchedulerError('worker occurrence limit is invalid')
        summary = {
            'recovered': self.scheduler.recover_stale(),
            'enqueued': self.scheduler.enqueue_due(),
            'claimed': 0, 'delivered': 0, 'failed': 0,
            'outcome_unknown': 0,
        }
        for _index in range(max_occurrences):
            claim = self.scheduler.claim(worker_id)
            if claim is None:
                break
            occurrence, token = claim
            summary['claimed'] += 1
            state = self._run_occurrence(occurrence, token)
            summary[state] = summary.get(state, 0) + 1
        return summary

    def _run_occurrence(self, occurrence, token):
        try:
            return self._execute(occurrence, token)
        except Exception as exc:
            current = self.scheduler.repository.occurrence.query.filter_by(
                id=occurrence.id
            ).first()
            state = 'outcome_unknown' if current is not None and (
                current.state in {
                    'executing', 'delivering', 'cancel_requested',
                }
            ) else 'failed'
            try:
                self.scheduler.transition(
                    occurrence.id, token, state, 'worker-error',
                    error_type=type(exc).__name__,
                )
            except Exception:
                # A lost claim is reconciled by lease expiry. Never infer
                # provider or delivery finality after authority is lost.
                state = 'outcome_unknown'
            return state

    def _execute(self, occurrence, token):
        delegation = occurrence.delegation
        _record, model, report, schedule = (
            self.scheduler.definition_for_worker(delegation)
        )
        context, endpoint = self.scheduler.delegated_workspace(delegation)
        binding = self.scheduler.endpoint_service.provider_registry.resolve(
            context
        )
        visualizations = {item['id']: item for item in model[
            'visualizations'
        ]}
        dashboard = next((item for item in model['dashboards'] if item[
            'id'] == report.get('dashboard_id')), None)
        if dashboard is None or not dashboard['tiles']:
            raise ReportSchedulerError(
                'scheduled report requires a non-empty dashboard'
            )
        charts = [visualizations[item['visualization_id']] for item in (
            dashboard['tiles'])]
        self.scheduler.transition(
            occurrence.id, token, 'executing', 'executing',
            progress=json.dumps({'completed': 0, 'total': len(charts)}),
        )
        delivery_ids = []
        for index, chart in enumerate(charts):
            self._require_not_cancelled(occurrence.id)
            query = copy.deepcopy(chart['query'])
            query['parameters'] = {
                **report.get('parameters', {}),
                **query.get('parameters', {}),
            }
            compiled = self.semantic_service.compile(
                binding.instance, model, query,
                security_context={'claims': {'user_id': delegation.user_id}},
            )
            opened = self.studio.open_session(
                context, endpoint, compiled['language_profile']
            )
            session_id = opened['provider_session']['session_id']
            execution = self.studio.execute(
                context, session_id, compiled['source'],
                parameters=compiled.get('parameters', {}),
                output_policy={'redact_keys': []},
            )
            studio_occurrence_id = execution['occurrence_id']
            execution = self._poll(
                occurrence, token, context, studio_occurrence_id,
                index, len(charts)
            )
            result = execution.get('result')
            if result is None:
                raise ReportSchedulerError(
                    'provider completed without a report result'
                )
            renderer = binding.instance.select_renderer(copy.deepcopy(result))
            descriptor = self.studio.admit_result(
                context, studio_occurrence_id,
                renderer.get('capability_ids', ()),
            )
            self.scheduler.transition(
                occurrence.id, token, 'delivering', 'delivering',
                progress=json.dumps({
                    'completed': index, 'total': len(charts)
                }),
            )
            delivery_ids.append(self._deliver(
                delegation, occurrence, descriptor['result_id'], chart,
                schedule['delivery'], len(charts),
            ))
            self.result_service.release(
                descriptor['result_id'], endpoint_id=context.endpoint_id
            )
            self.scheduler.heartbeat(occurrence.id, token, {
                'completed': index + 1, 'total': len(charts),
            })
            if index + 1 < len(charts):
                self.scheduler.transition(
                    occurrence.id, token, 'executing', 'executing',
                    progress=json.dumps({
                        'completed': index + 1, 'total': len(charts)
                    }),
                )
        self.scheduler.transition(
            occurrence.id, token, 'delivered', 'complete',
            progress=json.dumps({
                'completed': len(charts), 'total': len(charts)
            }),
            delivery_occurrence_ids=json.dumps(delivery_ids),
        )
        return 'delivered'

    def _poll(self, occurrence, token, context, studio_occurrence_id,
              index, total):
        for poll_index in range(self.max_polls):
            self._require_not_cancelled(
                occurrence.id, context, studio_occurrence_id
            )
            result = self.studio.poll(context, studio_occurrence_id)
            self.scheduler.heartbeat(occurrence.id, token, {
                'completed': index, 'total': total,
                'provider_poll': poll_index + 1,
            })
            if result['operation']['terminal']:
                return result
            time.sleep(self.poll_interval)
        raise ReportSchedulerError('provider report execution timed out')

    def _require_not_cancelled(
        self, occurrence_id, context=None, studio_occurrence_id=None,
    ):
        row = self.scheduler.repository.occurrence.query.filter_by(
            id=occurrence_id
        ).first()
        if row is None or row.state != 'cancel_requested':
            return
        if context is not None and studio_occurrence_id is not None:
            try:
                self.studio.request_cancel(context, studio_occurrence_id)
            except Exception:
                pass
        raise ReportSchedulerError(
            'report cancellation was requested after provider submission'
        )

    def _deliver(self, delegation, occurrence, result_id, chart, delivery,
                 chart_count):
        export_format = delivery['format']
        exported = self.result_service.export(
            result_id, export_format, endpoint_id=delegation.endpoint_id
        )
        filename = self._filename(
            occurrence.id, chart['id'], export_format
        )
        target = copy.deepcopy(delivery['target'])
        if 'object_name' in target and chart_count > 1:
            target['object_name'] = self._chart_object_name(
                target['object_name'], chart['id'], export_format
            )
        delivered = self.delivery_service.deliver(
            delegation.user_id, delegation.endpoint_id, {
                'request_key': str(uuid.uuid5(
                    uuid.UUID(occurrence.id),
                    f"{chart['id']}:{export_format}",
                )),
                'result_id': result_id, 'format': export_format,
                'profile_id': delivery['profile_id'], 'target': target,
                'content': exported['content'], 'filename': filename,
                'media_type': MEDIA_TYPES[export_format],
            }
        )
        if delivered['state'] != 'delivered':
            raise ReportSchedulerError(
                'scheduled report delivery did not reach delivered state'
            )
        return delivered['occurrence_id']

    @staticmethod
    def _filename(occurrence_id, chart_id, export_format):
        return (
            f'cdeadmin-report-{occurrence_id}-{chart_id}.{export_format}'
        )

    @staticmethod
    def _chart_object_name(value, chart_id, export_format):
        path = PurePosixPath(value)
        suffix = path.suffix
        stem = path.name[:-len(suffix)] if suffix else path.name
        name = f'{stem}-{chart_id}.{export_format}'
        return str(path.with_name(name))
