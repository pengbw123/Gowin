// Small freestanding runtime for the RV32I USB host firmware.
#include <stdarg.h>

typedef unsigned int  uint32_t;
typedef int           int32_t;
typedef unsigned char uint8_t;
typedef uint32_t      time_t;

#define NULL ((void *)0)

static volatile uint32_t *const uart = (volatile uint32_t *)0x20000000;

static void uart_putc(char value)
{
    while ((uart[1] & 0x01u) == 0u) { }
    uart[0] = (uint8_t)value;
}

static void uart_puts(const char *text)
{
    while (*text != '\0')
        uart_putc(*text++);
}

int is_sim(void)
{
    return (int)(uart[1] & 0x4u);
}

void wait_ms(time_t milliseconds)
{
    time_t end;
    if (is_sim())
        return;
    end = uart[2] + milliseconds;
    while ((int32_t)(uart[2] - end) < 0) { }
}

time_t now_ms(void)
{
    return uart[2];
}

// RV32I has no divider, so use a shift/subtract unsigned divide.
static uint32_t divide_unsigned(uint32_t numerator, uint32_t denominator,
                                uint32_t *remainder)
{
    uint32_t quotient = 0;
    uint32_t bit = 1;

    while ((denominator <= numerator) && ((int32_t)denominator > 0)) {
        denominator <<= 1;
        bit <<= 1;
    }
    while (bit != 0) {
        if (numerator >= denominator) {
            numerator -= denominator;
            quotient |= bit;
        }
        denominator >>= 1;
        bit >>= 1;
    }
    *remainder = numerator;
    return quotient;
}

static void print_unsigned(uint32_t value, uint32_t base)
{
    char digits[11];
    uint32_t count = 0;
    uint32_t remainder;

    do {
        value = divide_unsigned(value, base, &remainder);
        digits[count++] = "0123456789abcdef"[remainder];
    } while ((value != 0) && (count < sizeof(digits)));

    while (count != 0)
        uart_putc(digits[--count]);
}

// Supported conversions: %% %c %s %u %d %o %x.
void printf(char *format, ...)
{
    va_list arguments;
    va_start(arguments, format);

    while (*format != '\0') {
        char code = *format++;
        if (code != '%') {
            uart_putc(code);
            continue;
        }
        code = *format++;
        if (code == '%') {
            uart_putc('%');
        } else if (code == 'c') {
            uart_putc((char)va_arg(arguments, int));
        } else if (code == 's') {
            const char *text = va_arg(arguments, const char *);
            uart_puts(text ? text : "(null)");
        } else if ((code == 'u') || (code == 'd')) {
            print_unsigned(va_arg(arguments, uint32_t), 10);
        } else if (code == 'o') {
            print_unsigned(va_arg(arguments, uint32_t), 8);
        } else if (code == 'x') {
            print_unsigned(va_arg(arguments, uint32_t), 16);
        }
    }
    va_end(arguments);
}

void *memset(void *destination, uint8_t value, uint32_t length)
{
    uint8_t *bytes = (uint8_t *)destination;
    while (length-- != 0)
        *bytes++ = value;
    return destination;
}

// Fixed bump allocator. The one-device MIDI build never frees allocations.
static uint8_t allocation_pool[768];
static uint32_t allocation_offset;

void *malloc(uint32_t size)
{
    uint32_t aligned = (size + 3u) & ~3u;
    void *result;
    if ((allocation_offset + aligned) > sizeof(allocation_pool)) {
        printf("panic: malloc %u\n", size);
        return NULL;
    }
    result = &allocation_pool[allocation_offset];
    allocation_offset += aligned;
    memset(result, 0, aligned);
    return result;
}

void free(void *pointer)
{
    (void)pointer;
}
