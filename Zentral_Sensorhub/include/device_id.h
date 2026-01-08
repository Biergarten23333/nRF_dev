#ifndef DEVICE_ID_H
#define DEVICE_ID_H

#include <stdint.h>

extern char bt_name[8];
extern uint16_t dev_id16;

void init_dev_name_and_id(void);

#endif /* DEVICE_ID_H */
