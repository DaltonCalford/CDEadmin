/* Qualification-only UDFs. Never install in an existing server. */
#include <firebird/ibase.h>
#include <stdint.h>
#include <string.h>
#include <dlfcn.h>

static void *owned_allocate(long bytes)
{
    // Match the exact engine allocator required by FREE_IT. Do not use the
    // C library allocator for buffers the Firebird server will release.
    using Allocate = void *(*)(long);
    static void *library = dlopen("/opt/firebird/lib/libib_util.so", RTLD_NOW);
    static Allocate allocate = library ?
        reinterpret_cast<Allocate>(dlsym(library, "ib_util_malloc")) : nullptr;
    return allocate ? allocate(bytes) : nullptr;
}

extern "C" {

int32_t owned_zero()
{
    return 47;
}

int32_t owned_fifteen(const int32_t *a, const int32_t *b, const int32_t *c,
                     const int32_t *d, const int32_t *e, const int32_t *f,
                     const int32_t *g, const int32_t *h, const int32_t *i,
                     const int32_t *j, const int32_t *k, const int32_t *l,
                     const int32_t *m, const int32_t *n, const int32_t *o)
{
    return *a + *b + *c + *d + *e + *f + *g + *h + *i + *j + *k + *l + *m + *n + *o;
}

int32_t owned_value(const int32_t *value)
{
    return *value + 7;
}

int32_t owned_other(const int32_t *value)
{
    return *value + 11;
}

int32_t owned_null(const int32_t *value)
{
    return value ? *value + 13 : -99;
}

int32_t *owned_reference(const int32_t *value)
{
    static thread_local int32_t result;
    result = *value + 17;
    return &result;
}

int32_t *owned_free(const int32_t *value)
{
    auto *result = static_cast<int32_t *>(owned_allocate(sizeof(int32_t)));
    if (result)
        *result = *value + 31;
    return result;
}

PARAMDSC *owned_free_descriptor(const int32_t *value)
{
    auto *descriptor = static_cast<PARAMDSC *>(owned_allocate(sizeof(PARAMDSC)));
    if (!descriptor)
        return nullptr;
    memset(descriptor, 0, sizeof(*descriptor));
    descriptor->dsc_dtype = dtype_long;
    descriptor->dsc_length = sizeof(int32_t);
    descriptor->dsc_address = reinterpret_cast<ISC_UCHAR *>(owned_free(value));
    return descriptor;
}

// Native scalar-array UDF ABI, from Firebird 5.0.4 jrd/val.h.
struct OwnedArray {
    PARAMDSC descriptor;
    int32_t dimensions;
    struct Bounds { int32_t lower, upper; } bounds[1];
};

int32_t owned_array(const OwnedArray *array)
{
    if (!array || array->dimensions != 1 ||
        array->descriptor.dsc_dtype != dtype_long ||
        array->descriptor.dsc_length != sizeof(int32_t))
        return -88;
    int32_t result = 0;
    const auto *values = reinterpret_cast<const int32_t *>(array->descriptor.dsc_address);
    const int32_t count = array->bounds[0].upper - array->bounds[0].lower + 1;
    for (int32_t i = 0; i < count; ++i)
        result += values[i];
    return result;
}

int32_t owned_descriptor(const PARAMDSC *value)
{
    int32_t result;
    if (!value || (value->dsc_flags & DSC_null))
        return -99;
    if (value->dsc_dtype != dtype_long || value->dsc_length != sizeof(result))
        return -88;
    memcpy(&result, value->dsc_address, sizeof(result));
    return result + 19;
}

PARAMDSC *owned_return_descriptor(const int32_t *value)
{
    static thread_local int32_t result;
    static thread_local PARAMDSC descriptor;
    result = *value + 23;
    memset(&descriptor, 0, sizeof(descriptor));
    descriptor.dsc_dtype = dtype_long;
    descriptor.dsc_length = sizeof(result);
    descriptor.dsc_address = (ISC_UCHAR *) &result;
    return &descriptor;
}

void owned_parameter(const int32_t *value, int32_t *result)
{
    *result = *value + 29;
}

void owned_parameter_descriptor(const int32_t *value, PARAMDSC *result)
{
    const int32_t number = *value + 29;
    if (result->dsc_dtype == dtype_long && result->dsc_length == sizeof(number))
        memcpy(result->dsc_address, &number, sizeof(number));
}

const char *owned_string(const char *value)
{
    static thread_local char result[128];
    size_t length = strlen(value);
    if (length > sizeof(result) - 2)
        length = sizeof(result) - 2;
    memcpy(result, value, length);
    result[length] = '!';
    result[length + 1] = '\0';
    return result;
}

}
