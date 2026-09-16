"""Discard unpublished failed attachments without detach-retention hooks."""


def discard_failed_session(connection):
    attachment = connection._att
    if attachment is None:
        return
    try:
        try:
            # Native driver cleanup rolls back its owned transaction managers.
            connection._close()
        finally:
            connection._close_internals()
    finally:
        # A failed detach does not confirm release. Keep the native reference
        # so the provider can quarantine and explicitly retry this attachment.
        attachment.detach()
        connection._att = None
