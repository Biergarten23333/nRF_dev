#ifndef UWB_PORT_H
#define UWB_PORT_H

#include <stdint.h>

#define UWB_DW1000_DEVICE_ID 0xDECA0130U

int uwb_port_init(void);
int uwb_port_hw_reset(void);
int uwb_port_read_dev_id(uint32_t *dev_id);
int uwb_port_set_spi_slow(void);
int uwb_port_set_spi_fast(void);

#endif /* UWB_PORT_H */
