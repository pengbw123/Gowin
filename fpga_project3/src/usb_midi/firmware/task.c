
// Manage the root port using the SIE registers.
//

#include "sys.h"
#include "regs.h"
#include "usb.h"

// Hardware registers can change without a CPU store.  volatile is mandatory:
// otherwise Clang may hoist TOKEN/RXSTS polling loads out of their loops and
// the firmware will wait forever on a stale TKN_START value.
volatile uint32_t *usbh = (volatile uint32_t *)0x21000000;
int sim;

// Put the root port into reset mode
//
void root_reset(void)
{
    // UTMI+ reset is: opmode = 2, termselect = 0, xcvrselect = 0
    // and DP+DM pulldown = 1.
    usbh[REG_CTRL] = CTL_OPMODE2|CTL_XCVRSEL0|CTL_DP_PULLD|CTL_DN_PULLD;

    //printf("root reset\n");
}

// Configure the root port: set speed and SOF generation
//
void root_config(int speed, int enable_sof)
{
    uint32_t val;

    val  = (speed == 1) ? CTL_XCVRSEL1 : (speed == 3) ? CTL_XCVRSEL3 : CTL_XCVRSEL2;
    val |= CTL_OPMODE0|CTL_TERMSEL|CTL_DP_PULLD|CTL_DN_PULLD|CTL_TX_FLSH;
    if (enable_sof) val |= CTL_SOF_EN;

    usbh[REG_CTRL] = val;
}

// Manage the TASK pool. There is one task per USB cable and it holds data
// for both the port and the device end of the cable. This implies that there
// is one task per bus address, talking to one control endpoint.
//
#define MAX_TASK        1

TASK tasks[MAX_TASK];
REQ  requests[MAX_TASK];

TASK *new_task(void)
{
    for (int i = 0; i < MAX_TASK; i++ ) {
        if ( (tasks[i].prt_flags & (ROOT_PORT|HUB_PORT)) == 0)
            return &tasks[i];
    }
    printf("panic: out of tasks\n");
    return NULL;
}

TASK *clr_task(TASK *task)
{
    REQ *req = task->req;

    memset(task, 0, sizeof(TASK));
    memset(req,  0, sizeof(REQ));
    req->maxsz = 8;
    req->task  = task;
    task->req = req;
    return task;
}

// Return the one-device root port to the same recoverable state used after a
// physical disconnect.  Class drivers call this after a persistent endpoint
// fault instead of entering an idle state that can never produce MIDI again.
void restart_root_task(TASK *task)
{
    midi_set_connected(0);
    clr_task(task);
    root_config(SPEED_FS, 0);
    task->prt_flags = ROOT_PORT | PRT_POWER;
}

uint8_t donotspam = 0;

// Manage root port connection status
//
static void check_root(TASK *task)   
{
    //printf("regstat: %d%d%d%d dt_ctr12: %d\n", usbh[REG_STAT] & (0x8), usbh[REG_STAT] & (0x4), usbh[REG_STAT] & (0x2), usbh[REG_STAT] & (0x1), (usbh[REG_STAT] & 0xFFF0) >> 4);

    // device disconnected?
    if ((usbh[REG_STAT] & STAT_DETECT) == 0) {
        if (!donotspam)
        {
            printf("device disconnected, task addr = %d\n", task->addr);
            donotspam = 1;
        }

        restart_root_task(task);
        return;
    }

    donotspam = 0;
    
    // connected and nothing to do?
    if (task->prt_flags & (PRT_STALL|PRT_ENABLED)) return;
    
    // wait for connection
    if ((task->prt_flags & (PRT_POWER|PRT_CONNECT)) == PRT_POWER) {
        while ((usbh[REG_STAT] & STAT_DETECT) == 0) ;
        // USB-MIDI 1.0 uses bulk endpoints, which USB low-speed devices do
        // not support.  This host therefore only has a valid MIDI target at
        // full speed.  A dynamic MMIO line-state read once returned a stale
        // D- state even though the following STAT snapshot showed D+=1;
        // configuring the PHY as low speed then swapped signalling and made
        // SET_ADDRESS time out.  Wait for attach to settle and explicitly use
        // full-speed signalling for the class this project supports.
        wait_ms(20);
        task->prt_speed  = SPEED_FS;
        task->prt_flags |= PRT_CONNECT;
        printf("device connect, speed = %x, line=%x\n",
               task->prt_speed, usbh[REG_STAT] & 0x0fu);
    }
    
    // reset port / device
    if ((task->prt_flags & (PRT_CONNECT|PRT_RESET)) == PRT_CONNECT) {
        printf("USB root reset begin\n");
        root_reset();
        reset_enum();
        // Use a conservative reset width.  The USB specification requires at
        // least 10 ms; 100 ms also gives bus-powered MIDI controllers ample
        // time to recover when the FPGA is reconfigured without removing 5 V.
        wait_ms(100);
        root_config(task->prt_speed, (sim ? 0 : 1));
        task->prt_flags |= PRT_RESET;
        printf("USB root reset released\n");
    }

    // enable port / device 100 ms after reset
    if ((task->prt_flags & (PRT_RESET|PRT_ENABLED)) == PRT_RESET) {
        wait_ms(150);
        task->driver = &enum_dev;
        task->prt_flags |= PRT_ENABLED;
        task->state = 0; // = enum:set_address
        printf("USB root enabled\n");
    }
}

extern uint8_t _end[];

void main()
{
    TASK *root, *task;
    uint32_t heartbeat_ms;
    volatile uint32_t *midi_hw = (volatile uint32_t *)0x22000000;

    printf("Primer25K USB-MIDI host, firmware=%x bytes, pll_unlocks=%x\n",
           (uint32_t)_end - 0x10000000u,
           midi_hw[REG_MIDI_PLL_UNLOCK_COUNT]);
    sim = is_sim();

    // Initialise the 'tasks' table and set up root task
    for(int i=0; i<MAX_TASK; i++) {
        tasks[i].req = &requests[i];
        clr_task(&tasks[i]);
    }
    root = new_task();
    root->prt_flags = ROOT_PORT|PRT_POWER;
    heartbeat_ms = now_ms();

    // Event loop
    while(1) {
        uint32_t current_ms = now_ms();
        if ((uint32_t)(current_ms - heartbeat_ms) >= 1000u) {
            heartbeat_ms = current_ms;
            printf("USB host alive ctrl=%x stat=%x\n",
                   usbh[REG_CTRL], usbh[REG_STAT]);
        }

        for(int i=0; i<MAX_TASK; i++) {
            task = &tasks[i];

            // Skip inactive tasks, check root task status
            if ((task->prt_flags & (ROOT_PORT|HUB_PORT)) == 0)
                continue;
            if (task->prt_flags & ROOT_PORT)
                check_root(task);
            if ((task->prt_flags & PRT_ENABLED) == 0)
                continue;
            
            // Perform state machine step as needed:
            // - request step when a request is active
            // - task driver step otherwise
            if (task->req->state != rq_idle )
                do_request_step(task->req);
            else {
                if (sim || task->when <= now_ms()) {
                    task->driver(task, NULL);
                    if (task->state == dev_stall)
                        task->prt_flags |= PRT_STALL;
                }
            }
        }
    }
}
