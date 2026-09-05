.. _report_delivery:

*****************************
Authenticated report delivery
*****************************

CDEadmin can deliver an already generated, bounded and redacted result export
through authenticated SMTP or S3-compatible object storage. Delivery profiles
are server-side configuration. Semantic models and browser requests contain a
profile identifier and destination only; they never contain transport
credentials.

Every delivery has a caller-generated idempotency key and a durable occurrence
record. CDEadmin records ``delivering`` before contacting the transport and
does not automatically retry. If a transport call may have succeeded but its
response is unavailable, the occurrence is recorded as ``outcome_unknown``.
An operator must inspect the destination before deciding whether to submit a
new request.

Completed occurrences are retained for
``CDEADMIN_REPORT_DELIVERY_RETENTION_DAYS`` (90 days by default). A
``prepared`` or ``delivering`` occurrence older than
``CDEADMIN_REPORT_DELIVERY_STALE_SECONDS`` (10 minutes by default) is
reconciled to ``failed`` or ``outcome_unknown`` when occurrences are listed;
it is never resumed automatically.

SMTP profiles
=============

SMTP profiles require a username, password, SSL or STARTTLS, and an explicit
recipient or recipient-domain allowlist. The following example belongs in
``config_local.py``. Read secrets from the deployment's secret injection
mechanism rather than committing them to source control::

    import os

    CDEADMIN_REPORT_DELIVERY_PROFILES = {
        'operations-mail': {
            'kind': 'smtp',
            'label': 'Operations mail',
            'host': 'smtp.example.com',
            'port': 587,
            'use_ssl': False,
            'use_starttls': True,
            'username': os.environ['CDEADMIN_REPORT_SMTP_USERNAME'],
            'password': os.environ['CDEADMIN_REPORT_SMTP_PASSWORD'],
            'sender': 'reports@example.com',
            'allowed_domains': ['example.com'],
            'allowed_formats': ['pdf', 'xlsx', 'csv'],
        },
    }

Object-storage profiles
=======================

S3-compatible profiles fix the bucket and optional prefix on the server. A
browser may supply only a single filename, so it cannot select another bucket
or escape the prefix. CDEadmin sends a SHA-256 checksum, requires server-side
encryption, and can use the standard boto3 credential chain, a named AWS
profile, or an assumed role::

    CDEADMIN_REPORT_DELIVERY_PROFILES = {
        'report-archive': {
            'kind': 's3',
            'label': 'Report archive',
            'bucket': 'company-reports',
            'prefix': 'cdeadmin/published',
            'region_name': 'ca-central-1',
            'role_arn': 'arn:aws:iam::123456789012:role/report-writer',
            'server_side_encryption': 'aws:kms',
            'kms_key_id': 'alias/cdeadmin-reports',
            'allowed_formats': ['pdf', 'xlsx', 'csv'],
        },
    }

For a non-AWS S3-compatible service, set ``endpoint_url`` and provide its
credentials through the boto3 credential chain visible to the CDEadmin
process.

Scheduled execution boundary
============================

Manual delivery runs under the authenticated endpoint owner. Unattended report
execution uses a separate, revocable delegation for one published model,
report, schedule, endpoint generation, route and delivery destination. The
owner creates or refreshes that delegation while authenticated. CDEadmin
copies only the required endpoint credentials into AES-256-GCM envelopes whose
authenticated scope includes the delegation, credential, endpoint and
generation. Browser responses and occurrence records never contain those
envelopes or plaintext credentials.

Configure a dedicated key ring in ``config_local.py``. Keep old keys present
while grants created with them exist; refreshing a grant rewrites it with the
active key. Generate each key with a cryptographically secure source::

    python3 -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"

Then configure the key by using the deployment's secret injection mechanism::

    import os

    CDEADMIN_REPORT_WORKER_KEYS = {
        'worker-2026-09': os.environ['CDEADMIN_REPORT_WORKER_KEY'],
    }
    CDEADMIN_REPORT_WORKER_ACTIVE_KEY_ID = 'worker-2026-09'

With no active key, schedule definitions remain available but authorization
and automatic execution are fail-closed. A grant expires after 30 days by
default and can be revoked immediately; revocation deletes its encrypted
credential copies and cancels occurrences that have not been claimed.

Worker operation
================

Run the one-shot worker from a trusted scheduler such as systemd timer or
Kubernetes CronJob. Each invocation has a stable deployment-specific worker
identity::

    flask --app pgAdmin4:app cde-report-worker \
        --worker-id report-worker-1 --max-occurrences 10

The worker first reconciles expired leases, calculates due times with the
schedule's IANA timezone and five-field cron expression, and atomically claims
each occurrence. It heartbeats a bounded lease while the provider owns query
execution. An interruption before provider submission is ``failed``; an
interruption after submission or during delivery is ``outcome_unknown`` and is
never retried automatically. Occurrences later than
``CDEADMIN_REPORT_MAX_LATENESS_SECONDS`` are recorded as missed failures rather
than executed unexpectedly.

Cancellation before a claim is final. Cancellation after provider submission
is forwarded to the provider, but CDEadmin records an unknown outcome rather
than inferring transaction or cancellation finality. Multi-chart SMTP reports
produce one named attachment message per chart. Multi-chart S3 reports derive
one object name per chart from the configured object filename while retaining
the configured bucket and prefix.

To rotate keys, add the new key alongside the old key, make it active, restart
the application, and run ``flask --app pgAdmin4:app
cde-report-rotate-keys``. Remove an old key only after that command reports
that all retained envelopes have been rotated.
