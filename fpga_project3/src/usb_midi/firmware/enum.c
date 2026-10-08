// Enumerate the (newly connected) device
//

#include "sys.h"
#include "usb.h"

DEV_DESC dev_desc;
uint8_t  buffer[512];

void prn_dev_desc(uint8_t *data);
void prn_cf_full(uint8_t *data);

void prn_all(TASK *task)
{
    prn_dev_desc((uint8_t*) &dev_desc);
    prn_cf_full((uint8_t*) buffer);
    printf("# of NAKs: %x\n", task->nak);
    printf("# of TOs:  %x\n", task->tout); 
}

void set_driver(TASK *task, uint8_t *data);

int nxt_addr = 1;

void reset_enum(void)
{
    nxt_addr = 1;
}

enum enum_state {
  set_addr, get_dev_desc, get_cfg_desc, set_config, get_full_config, dev_enumerated
};

void enum_dev(TASK *task, uint8_t *data)
{
    struct config_desc *desc_conf = (struct config_desc*)buffer;

    switch (task->state) {
    
    // Enumerate the device:
    // 1. set address, wait 50ms for device to set it
    // 2. get device descriptor, set EP0 size
    // 3. get config descriptor, with size of full config data
    // 4. set active configuration to 1
    // 5. fetch the full configuration data and init the right driver
    //
    case set_addr:
        printf("ENUM SET_ADDRESS start\n");
        task->addr = 0;
        setup_req(task, (SU_OUT|SU_STD|SU_DEV), SET_ADDRESS, nxt_addr, 0, 0);
        task->when = now_ms() + 50;
        task->state = get_dev_desc;
        return;
        
    case get_dev_desc:
        if (task->req->resp != REQ_OK) break;
        task->addr = nxt_addr++;
        printf("SET ADDR ok\n");

        setup_req(task, (SU_IN|SU_STD|SU_DEV), GET_DESC, DEV_ID<<8, 0, sizeof(DEV_DESC));
        task->setup.pData = (uint8_t *) &dev_desc;
        task->state = get_cfg_desc;
        return;
    
    case get_cfg_desc:
        if (task->req->resp != REQ_OK) break;
        printf("GET TASK DESC ok\n");

        // bMaxPacketSize0 only becomes valid after the device descriptor has
        // been received.  Setting maxsz before this point changes it to zero
        // (dev_desc is initially zero-filled) and turns the next 8-byte SETUP
        // transaction into an invalid zero-byte transaction.
        if ((dev_desc.bMaxPacketSize0 != 8)  &&
            (dev_desc.bMaxPacketSize0 != 16) &&
            (dev_desc.bMaxPacketSize0 != 32) &&
            (dev_desc.bMaxPacketSize0 != 64)) {
            printf("invalid EP0 max packet size %u\n",
                   dev_desc.bMaxPacketSize0);
            task->state = dev_stall;
            return;
        }
        task->req->maxsz = dev_desc.bMaxPacketSize0;
        
        setup_req(task, (SU_IN|SU_STD|SU_DEV), GET_DESC, CNF_ID<<8, 0, sizeof(CNF_DESC));
        task->setup.pData = buffer;
        task->state = set_config;
        return;

    case set_config:
        if (task->req->resp != REQ_OK) break;
        printf("GET CONF DESC ok, size = %d\n", ((struct config_desc*)buffer)->wTotalLength);
        
        setup_req(task, (SU_OUT|SU_STD|SU_DEV), SET_CONF,
                  desc_conf->bConfigurationValue, 0, 0);
        task->when = now_ms() + 10; // [needed ?]
        task->state = get_full_config;
        return;

    case get_full_config:
        if (task->req->resp != REQ_OK) break;
        printf("SET CONFIG ok\n");

        if (desc_conf->wTotalLength > sizeof(buffer)) {
            printf("configuration too large\n");
            task->state = dev_stall;
            return;
        }
        setup_req(task, (SU_IN|SU_STD|SU_DEV), GET_DESC, CNF_ID<<8, 0, desc_conf->wTotalLength);
        task->setup.pData = buffer;
        task->state = dev_enumerated;
        return;

    case dev_enumerated:
        if (task->req->resp != REQ_OK) break;
        printf("GET CONF FULL ok\n");
        prn_all(task);
        set_driver(task, buffer);
        task->state = dev_init;
        (*task->driver)(task, buffer);
        return;
        
    case dev_stall:
        task->when = now_ms() + 255;
        return;
    }
    printf("Enumeration %x step failed (%x)\n", task->state, task->req->resp);
    // A controller can miss the first SETUP packet after an FPGA reload while
    // it remains powered from VBUS.  Do not turn one transient timeout into a
    // permanent dead host: perform a fresh bus reset and retry enumeration.
    // task->dummy is otherwise unused and is cleared on physical disconnect.
    if (task->dummy < 5u) {
        task->dummy++;
        printf("USB enumeration retry %u/5\n", task->dummy);
        task->addr = 0;
        task->req->state = rq_idle;
        task->req->resp = REQ_OK;
        task->req->maxsz = 8;
        task->state = set_addr;
        task->prt_flags &= ~(PRT_RESET | PRT_ENABLED | PRT_STALL);
        wait_ms(100);
    } else {
        printf("USB enumeration stopped after 5 retries\n");
        task->state = dev_stall;
    }
    return;
}

uint8_t *config_end;

// Find a descriptor in the configuration data starting at 'data'
//
void  *find_desc(void *data, uint8_t id)
{
    uint8_t *cursor = (uint8_t *)data;
    ANY_DESC *dsc = (ANY_DESC *)cursor;
    
    while (cursor < config_end && dsc->bDescriptorType != id) {
        if (dsc->bLength < 2)
            return NULL;
        cursor += dsc->bLength;
        dsc = (ANY_DESC *)cursor;
    }
    if (cursor >= config_end) {
        return NULL;
    }
    return cursor;
}

void drv_unknown(TASK *task, uint8_t *data)
{
    //printf("Unkown device, port stalled\n");
    return;
}

void set_driver(TASK *task, uint8_t *data)
{
    CNF_DESC *conf = (struct config_desc *)data;
    uint8_t *cursor;

    // A class-compliant USB-MIDI 1.0 controller normally starts with an
    // AudioControl interface (class 1/subclass 1), followed by the useful
    // MIDIStreaming interface (class 1/subclass 3). Search every interface
    // instead of assuming that interface zero is the data interface.
    config_end = data + conf->wTotalLength;
    cursor = data;
    while (cursor < config_end) {
        ANY_DESC *descriptor = (ANY_DESC *)cursor;
        if ((descriptor->bLength < 2) || (cursor + descriptor->bLength > config_end))
            break;
        if (descriptor->bDescriptorType == IFC_ID) {
            IFC_DESC *iface = (IFC_DESC *)cursor;
            if ((iface->bInterfaceClass == 1) &&
                (iface->bInterfaceSubClass == 3)) {
                printf("USB-MIDI interface=%u\n", iface->bInterfaceNumber);
                task->driver = drv_midi;
                return;
            }
        }
        cursor += descriptor->bLength;
    }
    printf("No MIDIStreaming interface\n");
    task->driver = drv_unknown;
}
