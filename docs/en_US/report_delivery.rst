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
execution remains disabled because an automatic worker also needs revocable,
scoped authority to open the source database endpoint. CDEadmin does not
impersonate an interactive user, retain an immortal database session, or
decrypt an owner's endpoint credentials without such a delegation. Schedule
definitions remain versioned metadata until that delegated-worker authority is
selected and implemented.
