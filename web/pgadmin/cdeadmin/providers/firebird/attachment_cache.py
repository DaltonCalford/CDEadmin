"""Attachment cache requests, distinct from persistent database buffers."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


# Firebird 5.0.4 inf_pub.h: isc_info_set_page_buffers. MON$PAGE_BUFFERS
# reports bcb_count (allocation), not this stored database-header override.
INFO_STORED_PAGE_BUFFERS = 61
MAX_REQUESTED_PAGES = 2147483646


def requested_pages(route):
    policy = route.get('attachment_cache_policy')
    if policy is None or policy == 'NATIVE_DEFAULT':
        return None
    if not isinstance(policy, str) or policy != 'CUSTOM':
        raise RelationalClientError(
            'Firebird attachment cache policy is invalid')
    pages = route.get('attachment_cache_pages')
    if type(pages) is not int or not 25 <= pages <= MAX_REQUESTED_PAGES:
        raise RelationalClientError(
            'Firebird attachment cache pages must be an integer from '
            f'25 to {MAX_REQUESTED_PAGES}')
    return pages


def stored_page_buffers(info):
    value = info.get_info(INFO_STORED_PAGE_BUFFERS)
    if type(value) is not int or not 0 <= value <= MAX_REQUESTED_PAGES:
        raise RelationalClientError(
            'Firebird stored page-buffer observation is invalid')
    return value


def creation_stored_pages(options):
    """Validate the stored override; the native server owns its final limit."""
    value = options.get('stored_page_buffers')
    if value is None:
        return None
    if type(value) is not int or (
            value != 0 and not 50 <= value <= MAX_REQUESTED_PAGES):
        raise RelationalClientError(
            'Firebird stored page buffers must be zero or an integer from '
            f'50 to {MAX_REQUESTED_PAGES}; '
            'the server may impose a lower limit')
    return value
