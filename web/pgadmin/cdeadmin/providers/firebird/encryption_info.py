"""Lossless privileged native encryption-header text, not key material."""
from ...sdk.relational import RelationalClientError


# Firebird 5 fb_info_crypt_key and fb_info_crypt_plugin (inf.cpp).
INFO_FIELDS = {'encryption_key_name': 133, 'encryption_plugin': 138}


def read_encryption_text(info, field):
    """Read one UTF-8 native header name on a caller-owned attachment.

    Driver 1.10.11's generic string handler defaults to ASCII. Reuse its
    bounded native-info acquisition, but decode this exact tagged response
    losslessly. The caller must own/exclusively hold the connection, just as
    for info.get_info(). No SQL, transaction or global driver patch is used.
    Access denial remains a native error, never an invented empty name.
    """
    if field not in INFO_FIELDS:
        raise ValueError('Unknown Firebird encryption header field')
    code = INFO_FIELDS[field]
    info.response.clear()
    info._get_data(bytes([code]))
    raw = bytes(info.response.raw)
    if len(raw) < 4:
        raise RelationalClientError('Firebird encryption info is truncated')
    tag = raw[0]
    size = int.from_bytes(raw[1:3], 'little')
    end = 3 + size
    if end >= len(raw) or raw[end] != 1:  # isc_info_end
        raise RelationalClientError(
            'Firebird encryption info framing is invalid')
    payload = raw[3:end]
    if tag == 3:  # isc_info_error, requested item followed by native status.
        error = RelationalClientError(
            'Firebird encryption header observation was denied or unavailable')
        if len(payload) == 5 and payload[0] == code:
            status = int.from_bytes(payload[1:], 'little')
            if 0 < status <= 2147483647:
                error.gds_codes = (status,)
        raise error
    if tag != code:
        raise RelationalClientError('Firebird encryption info tag is invalid')
    try:
        return payload.decode('utf-8')
    except UnicodeDecodeError:
        raise RelationalClientError(
            'Firebird encryption header name has invalid UTF-8') from None
