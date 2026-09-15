/* Qualification-only BLOB filter. Never install in an existing server.
 * This fixture targets the exact Firebird 5.0.4 internal callback ABI.
 * fb_blk.h TypedHandle adds a leading BlockType field to BlobControl;
 * the public ISC_BLOB_CTL omits it. Do not deploy this as a portable filter.
 * Transform ASCII lower case to upper case, preserving segment boundaries.
 */
#include <firebird/ibase.h>
#include <cstring>
#include <cstdint>
#include <cstddef>
#include <vector>

static void uppercase(unsigned char *buffer, unsigned short length)
{
    for (unsigned short i = 0; i < length; ++i)
        if (buffer[i] >= 'a' && buffer[i] <= 'z')
            buffer[i] -= 'a' - 'A';
}

struct OwnedControl
{
    int32_t block_type;
    isc_blob_ctl fields;
};

static_assert(offsetof(OwnedControl, fields) == 8, "5.0.4 Linux x64 control ABI");

extern "C" ISC_STATUS owned_uppercase(unsigned short action, OwnedControl *raw)
{
    ISC_BLOB_CTL control = &raw->fields;
    auto raw_source = reinterpret_cast<OwnedControl*>(control->ctl_source_handle);
    ISC_BLOB_CTL source = &raw_source->fields;
    using Callback = ISC_STATUS (*)(unsigned short, OwnedControl*);
    // The historical public C header leaves this pointer unprototyped.
    // blob_filter.h FPTR_BFILTER_CALLBACK provides the actual two arguments.
    Callback callback;
    static_assert(sizeof(callback) == sizeof(source->ctl_source), "callback ABI");
    std::memcpy(&callback, &source->ctl_source, sizeof(callback));
    if (action == isc_blob_filter_open || action == isc_blob_filter_create)
    {
        control->ctl_max_segment = source->ctl_max_segment;
        control->ctl_number_segments = source->ctl_number_segments;
        control->ctl_total_length = source->ctl_total_length;
        return 0;
    }
    if (action == isc_blob_filter_get_segment)
    {
        source->ctl_status = control->ctl_status;
        source->ctl_buffer = control->ctl_buffer;
        source->ctl_buffer_length = control->ctl_buffer_length;
        const ISC_STATUS status = callback(action, raw_source);
        control->ctl_segment_length = source->ctl_segment_length;
        uppercase(control->ctl_buffer, control->ctl_segment_length);
        return status;
    }
    if (action == isc_blob_filter_put_segment)
    {
        std::vector<unsigned char> buffer(control->ctl_buffer,
            control->ctl_buffer + control->ctl_buffer_length);
        uppercase(buffer.data(), control->ctl_buffer_length);
        source->ctl_status = control->ctl_status;
        source->ctl_buffer = buffer.data();
        source->ctl_buffer_length = control->ctl_buffer_length;
        return callback(action, raw_source);
    }
    // Firebird closes and frees the source chain; we own no persistent buffers.
    return 0;
}
