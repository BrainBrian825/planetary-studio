/* Protocol regression fixture; no camera or USB permissions required. */
#include <assert.h>
#include <stdio.h>
#include "../native/bridge.c"
#include "../native/euvc.c"

static uvc_input_terminal_t terminal = {.bTerminalID = 1};
static uvc_processing_unit_t processing = {.bUnitID = 2};
static int colour_type = 2, colour_format = 3, fail_selector = -1;
static int calls, commit_calls, opened, started, closed, commit_timeout;
static unsigned int written[256];

const uvc_input_terminal_t *uvc_get_input_terminals(uvc_device_handle_t *h) { return &terminal; }
const uvc_processing_unit_t *uvc_get_processing_units(uvc_device_handle_t *h) { return &processing; }
int LIBUSB_CALL libusb_get_device_descriptor(libusb_device *d, struct libusb_device_descriptor *out) {
  memset(out, 0, sizeof(*out)); out->idVendor = 0x199e; out->idProduct = (uintptr_t)d;
  return 0;
}
int LIBUSB_CALL libusb_control_transfer(libusb_device_handle *h, uint8_t type, uint8_t req,
    uint16_t value, uint16_t index, unsigned char *data, uint16_t size, unsigned int timeout) {
  calls++;
  /* Simulate a device that needs more than the old one-second deadline. */
  if (timeout < 5000) return LIBUSB_ERROR_TIMEOUT;
  if (type == 0xc0) { assert(index == 0x1a && size == 1); data[0] = colour_type; return 1; }
  if (index == 1) {
    assert(type == 0x21 && req == 1 && value == 0x200 && size == 8);
    if (commit_timeout == 1) return LIBUSB_ERROR_TIMEOUT;
    const unsigned char expected[] = {1, 0, 1, 1, 0x15, 0x16, 5, 0};
    assert(!memcmp(data, expected, 8)); commit_calls++; return 8;
  }
  int selector = value >> 8;
  assert((index & 255) == 0); /* fixture control interface */
  if (selector == fail_selector) return size - 1;
  if (selector == 0x2a && commit_timeout == 2) return LIBUSB_ERROR_TIMEOUT;
  if (type == 0x21) {
    unsigned int v = 0;
    for (int i = 0; i < size; i++) v |= (unsigned int)data[i] << (8*i);
    written[selector] = v;
    return size;
  }
  assert(type == 0xa1);
  unsigned int answer = 0;
  switch (selector) {
    case 0x20: answer = 0x1f0; break;
    case 0x21: assert(index == 0x200); answer = colour_format; break;
    case 0x24: answer = req == 0x82 ? 11000000 : 90000000; break;
    case 0x27: answer = (10 << 16) | 100; break;
    default: assert(0);
  }
  for (int i = 0; i < size; i++) data[i] = answer >> (8*i);
  return size;
}

uvc_error_t uvc_claim_if(uvc_device_handle_t *h, int interface) { return 0; }
uvc_error_t uvc_stream_open_ctrl(uvc_device_handle_t *h, uvc_stream_handle_t **out,
                                uvc_stream_ctrl_t *ctrl) {
  assert(ctrl->dwMaxVideoFrameSize == 1280*960);
  assert(ctrl->dwMaxPayloadTransferSize == 1280*960+12);
  int error = ps_euvc_query(h, ctrl, 0, UVC_SET_CUR);
  if (error) return error;
  opened++; *out = (void *)1; return 0;
}
uvc_error_t uvc_stream_start(uvc_stream_handle_t *stream, uvc_frame_callback_t *callback,
                            void *user, uint8_t flags) {
  assert(written[0x24] && written[0x25] == 1280); /* sensor configured after COMMIT */
  unsigned char *raw = malloc(1280*960);
  memset(raw, 123, 1280*960);
  uvc_frame_t frame = {.data = raw, .data_bytes = 1280*960, .width = 1280, .height = 960};
  callback(&frame, user); free(raw); started++; return 0;
}
void uvc_stream_close(uvc_stream_handle_t *stream) { closed++; }
void uvc_stop_streaming(uvc_device_handle_t *h) { closed++; }

int main(void) {
  assert(ps_euvc_device((void *)0x8202));
  assert(!ps_euvc_device((void *)0x8619));
  uvc_device_info_t info = {0};
  uvc_device_handle_t handle = {.info = &info};
  uvc_streaming_interface_t stream = {.bInterfaceNumber = 1};
  uvc_format_desc_t format = {.parent = &stream, .bFormatIndex = 1,
      .bDescriptorSubtype = UVC_VS_FORMAT_UNCOMPRESSED, .bBitsPerPixel = 16};
  uvc_frame_desc_t small = {.parent = &format, .bFrameIndex = 2, .wWidth = 320, .wHeight = 480};
  uvc_frame_desc_t full = {.parent = &format, .bFrameIndex = 1, .wWidth = 640, .wHeight = 960,
      .dwMaxVideoFrameBufferSize = 1280*960 + 12, .next = &small};
  stream.format_descs = &format; format.frame_descs = &full; info.stream_ifs = &stream;
  memcpy(format.fourccFormat, "YUY2", 4);
  ps_session session = {.handle = &handle};
  assert(!ps_euvc_init(&handle, &session.euvc));
  assert(full.wWidth == 1280 && small.wWidth == 640 && session.euvc.frame == &full);
  assert(!memcmp(format.fourccFormat, "GRBG", 4) && format.bBitsPerPixel == 8);
  ps_mode modes[10];
  int count = ps_modes(&session, modes, 10);
  assert(count == 3); /* 3.75 and 7.5fps are below the minimum pixel clock */
  for (int i = 0; i < count; i++) {
    assert(modes[i].width == 1280 && modes[i].height == 960 && modes[i].bits == 8);
    assert(!strcmp(modes[i].fourcc, "GRBG") && modes[i].frame_index == 1);
  }
  assert(modes[0].fps == 15 && modes[2].fps == 60);
  uvc_stream_ctrl_t ctrl = {.bFormatIndex = 1, .bFrameIndex = 1, .bInterfaceNumber = 1};
  assert(!ps_euvc_query(&handle, &ctrl, 0, UVC_SET_CUR) && commit_calls == 1);
  int before = calls;
  assert(ps_euvc_query(&handle, &ctrl, 1, UVC_GET_MAX) == UVC_ERROR_NOT_SUPPORTED);
  assert(calls == before); /* Never send ordinary UVC probe to an EUVC camera. */
  assert(!ps_euvc_configure(&handle, &session.euvc, 333333));
  assert(written[0x2a] == 1 && written[0x28] == 0 && written[0x29] == 0);
  assert(written[0x25] == 1280 && written[0x26] == 960);
  assert(written[0x24] == 40158040);
  pthread_mutex_init(&session.mutex, NULL);
  pthread_cond_init(&session.cond, NULL);
  assert(!ps_start(&session, &modes[1]));
  unsigned char *raw = malloc(1280*960);
  ps_frame meta;
  assert(ps_read(&session, raw, 1280*960, &meta, 1) == 1);
  assert(meta.width == 1280 && meta.height == 960 && meta.bytes == 1280*960);
  for (int i = 0; i < 1280*960; i++) assert(raw[i] == 123);
  ps_stop(&session);
  assert(!session.running && closed == 1);
  assert(!ps_start(&session, &modes[1]));
  ps_stop(&session);
  assert(started == 2 && opened == 2 && closed == 2);
  fail_selector = 0x24;
  assert(ps_start(&session, &modes[1]) == UVC_ERROR_IO);
  assert(!session.running && closed == 3 && started == 2);
  fail_selector = -1;
  commit_timeout = 1;
  assert(ps_start(&session, &modes[1]) == UVC_ERROR_TIMEOUT);
  assert(!session.running && strstr(ps_error(-7), "eight-byte stream COMMIT"));
  commit_timeout = 2;
  assert(ps_start(&session, &modes[1]) == UVC_ERROR_TIMEOUT);
  assert(!session.running && strstr(ps_error(-7), "setting binning"));
  assert(strstr(ps_error(-7), "selector 0x2a"));
  commit_timeout = 0;
  assert(!ps_start(&session, &modes[1]));
  assert(!strcmp(ps_error(-7), uvc_strerror(-7))); /* stale error cleared */
  ps_stop(&session);
  fail_selector = 0x24;
  free(raw); free(session.data);
  pthread_cond_destroy(&session.cond);
  pthread_mutex_destroy(&session.mutex);

  assert(ps_euvc_configure(&handle, &session.euvc, 333333) == UVC_ERROR_IO);
  fail_selector = -1;
  colour_format = 99;
  ps_euvc state = {0};
  assert(ps_euvc_init(&handle, &state) == UVC_ERROR_NOT_SUPPORTED);
  puts("EUVC protocol checks passed");
  return 0;
}
