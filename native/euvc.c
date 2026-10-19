/* SPDX-License-Identifier: GPL-3.0-or-later
 * EUVC protocol ported from OpenAstro liboacam/euvc EUVCconnect.c,
 * EUVCcontroller.c and EUVCusb.c, Copyright 2015-2024 James Fidell
 * (james@openastroproject.org). See ../LICENSE and ../THIRD_PARTY.md.
 * These devices advertise 16-bit YUV at half width but deliver raw 8-bit
 * pixels. They use an eight-byte COMMIT and a vendor pixel clock, not PROBE.
 */
#include "euvc.h"
#include <string.h>
#include <unistd.h>
#include <stdio.h>

/* Match OpenAstro's USB_CTRL_TIMEOUT. Sensor writes/COMMIT can take longer
 * than the one-second timeout used in the first version of this port. */
#define EUVC_CONTROL_TIMEOUT 5000
static _Thread_local char last_error[256];
static _Thread_local int last_code;
void ps_euvc_clear_error(void) { last_error[0] = 0; last_code = 0; }
const char *ps_euvc_error(int code) {
  return code == last_code && last_error[0] ? last_error : NULL;
}
void ps_euvc_record_error(int code, const char *stage) {
  last_code = code;
  snprintf(last_error, sizeof(last_error), "%s: %s", stage, uvc_strerror(code));
}
static const char *selector_name(int selector) {
  switch (selector) {
    case 0x24: return "pixel clock";
    case 0x25: return "ROI width";
    case 0x26: return "ROI height";
    case 0x28: return "ROI X position";
    case 0x29: return "ROI Y position";
    case 0x2a: return "binning";
    default: return "sensor control";
  }
}

int ps_euvc_device(libusb_device *device) {
  struct libusb_device_descriptor d;
  return !libusb_get_device_descriptor(device, &d) && d.idVendor == 0x199e &&
      d.idProduct >= 0x8201 && d.idProduct <= 0x8208;
}

static int transfer(uvc_device_handle_t *h, int unit, int selector, int req,
                    unsigned int *value, int size) {
  unsigned char data[4] = {0};
  for (int i = 0; i < size; i++) data[i] = *value >> (8 * i);
  int ret = libusb_control_transfer(h->usb_devh, req == 1 ? 0x21 : 0xa1,
      req, selector << 8, (unit << 8) | h->info->ctrl_if.bInterfaceNumber,
      data, size, EUVC_CONTROL_TIMEOUT);
  if (ret != size) {
    char stage[160];
    snprintf(stage, sizeof(stage), "EUVC %s %s (selector 0x%02x, unit %d, request 0x%02x, %d bytes)",
        req == 1 ? "setting" : "reading", selector_name(selector), selector, unit, req, size);
    int error = ret < 0 ? ret : UVC_ERROR_IO;
    ps_euvc_record_error(error, stage);
    return error;
  }
  *value = 0;
  for (int i = 0; i < size; i++) *value |= (unsigned int)data[i] << (8 * i);
  return 0;
}

int ps_euvc_init(uvc_device_handle_t *h, ps_euvc *s) {
  const uvc_input_terminal_t *t = uvc_get_input_terminals(h);
  const uvc_processing_unit_t *p = uvc_get_processing_units(h);
  unsigned char type;
  unsigned int color = 0, blank = 0;
  int ret;
  if (!t || !p) return UVC_ERROR_INVALID_DEVICE;
  s->terminal = t->bTerminalID;
  ret = libusb_control_transfer(h->usb_devh, 0xc0, 0, 0, 0x1a, &type, 1, EUVC_CONTROL_TIMEOUT);
  if (ret != 1) return ret < 0 ? ret : UVC_ERROR_IO;
  int colour = (type & 3) == 2;
  if ((ret = transfer(h, s->terminal, 0x20, 0x81, &s->caps, 2))) return ret;
  if (colour && (ret = transfer(h, p->bUnitID, 0x21, 0x81, &color, 2))) return ret;
  const char *pattern = "Y800";
  if (colour) {
    switch (color) {
      case 3: pattern = "GRBG"; break;
      case 4: pattern = "RGGB"; break;
      case 5: pattern = "BGGR"; break;
      case 6: pattern = "GBRG"; break;
      default: return UVC_ERROR_NOT_SUPPORTED;
    }
  }
  for (uvc_streaming_interface_t *i = h->info->stream_ifs; i; i = i->next)
    for (uvc_format_desc_t *f = i->format_descs; f; f = f->next) {
      if (f->bDescriptorSubtype != UVC_VS_FORMAT_UNCOMPRESSED || f->bBitsPerPixel != 16)
        continue;
      if (colour ? (memcmp(f->fourccFormat, "YUY2", 4) && memcmp(f->fourccFormat, "UYVY", 4))
                 : memcmp(f->fourccFormat, "Y800", 4)) continue;
      static const unsigned char tail[12] = {0,0,0x10,0,0x80,0,0,0xaa,0,0x38,0x9b,0x71};
      memcpy(f->guidFormat, pattern, 4);
      memcpy(f->guidFormat + 4, tail, 12);
      f->bBitsPerPixel = 8;
      for (uvc_frame_desc_t *r = f->frame_descs; r; r = r->next) {
        if (!r->wWidth || r->wWidth > 32767 || !r->wHeight) return UVC_ERROR_INVALID_MODE;
        r->wWidth *= 2;
        /* Start with full sensor, unbinned. Smaller descriptors can denote
         * binning modes; exposing those as ordinary ROI would corrupt Bayer. */
        if (!s->frame || (unsigned int)r->wWidth * r->wHeight >
            (unsigned int)s->frame->wWidth * s->frame->wHeight) s->frame = r;
      }
    }
  if (!s->frame || (s->caps & 0x90) != 0x90) return UVC_ERROR_NOT_SUPPORTED;
  if ((ret = transfer(h, s->terminal, 0x24, 0x82, &s->clock_min, 4)) ||
      (ret = transfer(h, s->terminal, 0x24, 0x83, &s->clock_max, 4)) ||
      (ret = transfer(h, s->terminal, 0x27, 0x81, &blank, 4))) return ret;
  s->blank_x = blank & 0xffff; s->blank_y = blank >> 16;
  return 0;
}

int ps_euvc_configure(uvc_device_handle_t *h, ps_euvc *s, unsigned int interval) {
  unsigned int value;
  int ret;
  if (!s->frame || !interval) return UVC_ERROR_INVALID_MODE;
  usleep(100000); /* Camera settles after COMMIT before sensor controls. */
  if (s->caps & (1 << 8)) {
    value = 1;
    if ((ret = transfer(h, s->terminal, 0x2a, 1, &value, 1))) return ret;
  }
  if ((s->caps & 0x60) == 0x60) {
    value = 0;
    if ((ret = transfer(h, s->terminal, 0x28, 1, &value, 4)) ||
        (ret = transfer(h, s->terminal, 0x29, 1, &value, 4))) return ret;
  }
  uint64_t pixels = (uint64_t)(s->frame->wWidth + s->blank_x) *
      (s->frame->wHeight + s->blank_y);
  uint64_t clock = (pixels * 10000000 + interval / 2) / interval;
  if (clock < s->clock_min || clock > s->clock_max) return UVC_ERROR_INVALID_MODE;
  value = clock;
  if ((ret = transfer(h, s->terminal, 0x24, 1, &value, 4))) return ret;
  if ((s->caps & 0x60) == 0x60) {
    value = s->frame->wWidth;
    if ((ret = transfer(h, s->terminal, 0x25, 1, &value, 4))) return ret;
    value = s->frame->wHeight;
    if ((ret = transfer(h, s->terminal, 0x26, 1, &value, 4))) return ret;
  }
  return 0;
}

int ps_euvc_query(uvc_device_handle_t *h, uvc_stream_ctrl_t *ctrl, int probe, int req) {
  if (probe || req != UVC_SET_CUR) return UVC_ERROR_NOT_SUPPORTED;
  unsigned char data[8] = {1, 0, ctrl->bFormatIndex, ctrl->bFrameIndex};
  /* OA always commits 30fps; the actual rate is set by the pixel clock. */
  unsigned int interval = 333333;
  for (int i = 0; i < 4; i++) data[4+i] = interval >> (8*i);
  int ret = libusb_control_transfer(h->usb_devh, 0x21, 1, 0x0200,
      ctrl->bInterfaceNumber, data, sizeof(data), EUVC_CONTROL_TIMEOUT);
  if (ret == sizeof(data)) return 0;
  int error = ret < 0 ? ret : UVC_ERROR_IO;
  ps_euvc_record_error(error, "EUVC eight-byte stream COMMIT");
  return error;
}
