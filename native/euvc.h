/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef PS_EUVC_H
#define PS_EUVC_H
#include <libuvc/libuvc.h>
#include <libuvc/libuvc_internal.h>
typedef struct {
  uvc_frame_desc_t *frame;
  unsigned int terminal, caps, clock_min, clock_max, blank_x, blank_y;
} ps_euvc;
void ps_euvc_clear_error(void);
const char *ps_euvc_error(int code);
void ps_euvc_record_error(int code, const char *stage);
int ps_euvc_device(libusb_device *device);
int ps_euvc_init(uvc_device_handle_t *handle, ps_euvc *state);
int ps_euvc_configure(uvc_device_handle_t *handle, ps_euvc *state, unsigned int interval);
int ps_euvc_query(uvc_device_handle_t *handle, uvc_stream_ctrl_t *ctrl, int probe, int req);
#endif
