// USB Audio/MIDIStreaming class driver for one class-compliant MIDI 1.0
// device. Hubs and MIDI 2.0 UMP are intentionally outside the first build.
#include "sys.h"
#include "usb.h"
#include "regs.h"

#define USB_CLASS_AUDIO          0x01
#define USB_SUBCLASS_MIDISTREAM  0x03
#define ENDPOINT_IN              0x80
#define TRANSFER_TYPE_MASK       0x03
#define TRANSFER_TYPE_BULK       0x02
#define MIDI_PACKET_CAPACITY     64

enum midi_driver_state {
    midi_init = 0,
    midi_poll_start,
    midi_poll_wait,
    midi_idle
};

struct midi_driver_data {
    uint8_t endpoint;
    uint8_t toggle;
    uint8_t max_packet;
    uint8_t error_streak;
    uint8_t packet[MIDI_PACKET_CAPACITY];
};

static struct midi_driver_data midi_data;
static volatile uint32_t *const midi_output =
    (volatile uint32_t *)0x22000000;

void midi_set_connected(uint8_t connected)
{
    midi_output[REG_MIDI_STATUS] = connected ? 1u : 0u;
}

static void emit_midi_event(uint8_t header, uint8_t status,
                            uint8_t data1, uint8_t data2)
{
    uint32_t wait_started;
    uint32_t event = ((uint32_t)header << 24) |
                     ((uint32_t)status << 16) |
                     ((uint32_t)data1 << 8) |
                     (uint32_t)data2;

    // Never let one lost cross-clock acknowledge freeze the entire USB host.
    // The old unbounded wait stopped enumeration, heartbeat and disconnect
    // handling forever if the audio side ever became unavailable.  A healthy
    // synthesizer accepts an event in microseconds; 20 ms is deliberately
    // generous and dropping one event is safer than deadlocking the device.
    wait_started = now_ms();
    while ((midi_output[REG_MIDI_STATUS] & MIDI_READY) == 0u) {
        if ((uint32_t)(now_ms() - wait_started) >= 20u) {
            printf("MIDI mailbox timeout, event dropped\n");
            return;
        }
    }
    midi_output[REG_MIDI_EVENT] = event;

    if (((status & 0xf0u) == 0x90u) && (data2 != 0u))
        printf("NOTE ON  n=%u v=%u\n", data1, data2);
    else if (((status & 0xf0u) == 0x80u) ||
             (((status & 0xf0u) == 0x90u) && (data2 == 0u)))
        printf("NOTE OFF n=%u\n", data1);
    else if (((status & 0xf0u) == 0xb0u) && (data1 == 0x14u))
        printf("VOLUME=%u\n", data2);
}

static uint8_t find_midi_input_endpoint(uint8_t *configuration,
                                        uint8_t *endpoint,
                                        uint8_t *max_packet)
{
    uint8_t *cursor = configuration;
    uint8_t *end = configuration + ((CNF_DESC *)configuration)->wTotalLength;
    uint8_t in_midi_interface = 0;

    while (cursor < end) {
        ANY_DESC *descriptor = (ANY_DESC *)cursor;
        if ((descriptor->bLength < 2) || (cursor + descriptor->bLength > end))
            break;

        if (descriptor->bDescriptorType == IFC_ID) {
            IFC_DESC *iface = (IFC_DESC *)cursor;
            in_midi_interface =
                (iface->bInterfaceClass == USB_CLASS_AUDIO) &&
                (iface->bInterfaceSubClass == USB_SUBCLASS_MIDISTREAM);
        } else if ((descriptor->bDescriptorType == EPT_ID) &&
                   in_midi_interface) {
            EPT_DESC *ep = (EPT_DESC *)cursor;
            if (((ep->bEndpointAddress & ENDPOINT_IN) != 0) &&
                ((ep->bmAttributes & TRANSFER_TYPE_MASK) == TRANSFER_TYPE_BULK)) {
                uint16_t packet_size = ep->wMaxPacketSize & 0x07ffu;
                if (packet_size > MIDI_PACKET_CAPACITY)
                    packet_size = MIDI_PACKET_CAPACITY;
                if (packet_size < 4)
                    packet_size = 4;
                *endpoint = ep->bEndpointAddress & 0x0fu;
                *max_packet = (uint8_t)packet_size;
                return 1;
            }
        }
        cursor += descriptor->bLength;
    }
    return 0;
}

void drv_midi(TASK *task, uint8_t *configuration)
{
    uint16_t index;

    switch (task->state) {
    case midi_init:
        memset(&midi_data, 0, sizeof(midi_data));
        task->data = &midi_data;
        if (!find_midi_input_endpoint(configuration, &midi_data.endpoint,
                                      &midi_data.max_packet)) {
            printf("USB-MIDI bulk IN endpoint not found\n");
            task->state = midi_idle;
            return;
        }
        task->req->maxsz = midi_data.max_packet;
        midi_set_connected(1);
        printf("USB-MIDI ready ep=%u maxpkt=%u\n",
               midi_data.endpoint, midi_data.max_packet);
        task->state = midi_poll_start;
        return;

    case midi_poll_start:
        data_req(task, midi_data.endpoint, IN, midi_data.packet,
                 midi_data.max_packet);
        task->req->toggle = midi_data.toggle;
        task->state = midi_poll_wait;
        return;

    case midi_poll_wait:
        if (task->req->resp == PID_STALL) {
            printf("USB-MIDI endpoint stalled; re-enumerating\n");
            restart_root_task(task);
            return;
        }

        if (task->req->resp == REQ_OK) {
            midi_data.error_streak = 0;
            midi_data.toggle = task->req->toggle;
            for (index = 0; (index + 3u) < task->req->size; index += 4u) {
                uint8_t header = midi_data.packet[index];
                if ((header & 0x0fu) != 0u) {
                    emit_midi_event(header,
                                    midi_data.packet[index + 1u],
                                    midi_data.packet[index + 2u],
                                    midi_data.packet[index + 3u]);
                }
            }
        } else if (task->req->resp == REQ_EMPTY) {
            // NAK is the normal idle response of a USB-MIDI bulk endpoint.
            midi_data.error_streak = 0;
        } else {
            // CRC, timeout and unexpected PID errors are transient in small
            // numbers.  A sustained run means this endpoint is no longer in a
            // usable state; restart the root port instead of polling forever.
            midi_data.error_streak++;
            if (midi_data.error_streak >= 16u) {
                printf("USB-MIDI repeated errors; re-enumerating\n");
                restart_root_task(task);
                return;
            }
        }
        task->when = now_ms() + 1u;
        task->state = midi_poll_start;
        return;

    case midi_idle:
        task->when = now_ms() + 255u;
        return;

    default:
        task->state = midi_idle;
        return;
    }
}
