/* Thread-safe, small C ABI over libuvc. Raw formats are negotiated by descriptor,
 * preserving vendor Bayer FOURCCs that system video APIs often cannot decode. */
#include <libuvc/libuvc.h>
#include <libuvc/libuvc_internal.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <time.h>

typedef struct { int vid, pid, bus, address; char name[256], serial[128]; } ps_device;
typedef struct { int width, height, fps, bits, format_index, frame_index, interface_number, interval; char fourcc[5]; } ps_mode;
typedef struct { int width, height; size_t bytes, step; unsigned long long sequence, dropped; } ps_frame;
typedef struct {
  uvc_context_t *ctx;
  uvc_device_handle_t *handle;
  pthread_mutex_t mutex;
  pthread_cond_t cond;
  unsigned char *data;
  size_t capacity;
  ps_frame meta;
  unsigned long long consumed;
  int running, failed;
} ps_session;

const char *ps_error(int code) { return uvc_strerror((uvc_error_t)code); }
size_t ps_abi_size(int structure) {
  if (structure == 0) return sizeof(ps_device);
  if (structure == 1) return sizeof(ps_mode);
  if (structure == 2) return sizeof(ps_frame);
  return 0;
}

int ps_devices(ps_device *out, int capacity) {
  uvc_context_t *ctx = NULL;
  uvc_device_t **list = NULL;
  int error = uvc_init(&ctx, NULL), count = 0;
  if (error) return error;
  error = uvc_get_device_list(ctx, &list);
  if (!error) for (int i = 0; list[i] && count < capacity; i++) {
    uvc_device_descriptor_t *d = NULL;
    if (uvc_get_device_descriptor(list[i], &d)) continue;
    ps_device *item = &out[count++];
    memset(item, 0, sizeof(*item));
    item->vid = d->idVendor; item->pid = d->idProduct;
    item->bus = uvc_get_bus_number(list[i]); item->address = uvc_get_device_address(list[i]);
    snprintf(item->name, sizeof(item->name), "%s", d->product ? d->product : "UVC camera");
    snprintf(item->serial, sizeof(item->serial), "%s", d->serialNumber ? d->serialNumber : "");
    uvc_free_device_descriptor(d);
  }
  if (list) uvc_free_device_list(list, 1);
  uvc_exit(ctx);
  return error ? error : count;
}

ps_session *ps_open(int bus, int address, int *error) {
  ps_session *s = calloc(1, sizeof(*s));
  if (!s) { *error = UVC_ERROR_NO_MEM; return NULL; }
  uvc_device_t **list = NULL;
  *error = uvc_init(&s->ctx, NULL);
  if (*error) { free(s); return NULL; }
  *error = uvc_get_device_list(s->ctx, &list);
  if (!*error) {
    *error = UVC_ERROR_NO_DEVICE;
    for (int i = 0; list[i]; i++)
      if (uvc_get_bus_number(list[i]) == bus && uvc_get_device_address(list[i]) == address) {
        *error = uvc_open(list[i], &s->handle); break;
      }
  }
  if (list) uvc_free_device_list(list, 1);
  if (*error) { uvc_exit(s->ctx); free(s); return NULL; }
  pthread_mutex_init(&s->mutex, NULL);
  pthread_cond_init(&s->cond, NULL);
  return s;
}

int ps_modes(ps_session *s, ps_mode *out, int capacity) {
  int count = 0;
  for (const uvc_format_desc_t *f = uvc_get_format_descs(s->handle); f; f = f->next)
    for (const uvc_frame_desc_t *r = f->frame_descs; r; r = r->next) {
      int n = r->bFrameIntervalType ? r->bFrameIntervalType : 1;
      for (int j = 0; j < n && count < capacity; j++) {
        ps_mode *m = &out[count++];
        memset(m, 0, sizeof(*m));
        m->width = r->wWidth; m->height = r->wHeight;
        unsigned int interval = r->bFrameIntervalType && r->intervals ? r->intervals[j] : r->dwDefaultFrameInterval;
        m->fps = interval ? (10000000 + interval / 2) / interval : 30;
        m->interval = interval ? interval : 333333;
        m->bits = f->bBitsPerPixel;
        m->format_index = f->bFormatIndex; m->frame_index = r->bFrameIndex;
        m->interface_number = f->parent->bInterfaceNumber;
        if (f->bDescriptorSubtype == UVC_VS_FORMAT_MJPEG) memcpy(m->fourcc, "MJPG", 4);
        else if (!memcmp(f->guidFormat, "\x7d\xeb\x36\xe4\x4f\x52\xce\x11\x9f\x53\x00\x20\xaf\x0b\xa7\x70", 16))
          memcpy(m->fourcc, "BGR3", 4);
        else if (!memcmp(f->guidFormat, "\x7e\xeb\x36\xe4\x4f\x52\xce\x11\x9f\x53\x00\x20\xaf\x0b\xa7\x70", 16))
          memcpy(m->fourcc, "RGB3", 4);
        else memcpy(m->fourcc, f->fourccFormat, 4);
      }
    }
  return count;
}

static void ps_callback(uvc_frame_t *frame, void *user) {
  ps_session *s = user;
  if (!frame->data_bytes || !frame->data) return;
  pthread_mutex_lock(&s->mutex);
  if (frame->data_bytes > s->capacity) {
    unsigned char *data = realloc(s->data, frame->data_bytes);
    if (!data) { s->failed = 1; pthread_cond_signal(&s->cond); pthread_mutex_unlock(&s->mutex); return; }
    s->data = data; s->capacity = frame->data_bytes;
  }
  if (s->meta.sequence > s->consumed) s->meta.dropped++;
  memcpy(s->data, frame->data, frame->data_bytes);
  s->meta.bytes = frame->data_bytes; s->meta.step = frame->step;
  s->meta.width = frame->width; s->meta.height = frame->height;
  s->meta.sequence++;
  pthread_cond_signal(&s->cond);
  pthread_mutex_unlock(&s->mutex);
}

int ps_start(ps_session *s, const ps_mode *mode) {
  uvc_stream_ctrl_t ctrl;
  memset(&ctrl, 0, sizeof(ctrl));
  ctrl.bInterfaceNumber = mode->interface_number;
  int error = uvc_claim_if(s->handle, ctrl.bInterfaceNumber);
  if (error) return error;
  error = uvc_query_stream_ctrl(s->handle, &ctrl, 1, UVC_GET_MAX);
  if (error) return error;
  ctrl.bmHint = 1;
  ctrl.bFormatIndex = mode->format_index; ctrl.bFrameIndex = mode->frame_index;
  ctrl.bInterfaceNumber = mode->interface_number;
  ctrl.dwFrameInterval = mode->interval;
  error = uvc_probe_stream_ctrl(s->handle, &ctrl);
  if (error) return error;
  s->running = 1;
  error = uvc_start_streaming(s->handle, &ctrl, ps_callback, s, 0);
  if (error) s->running = 0;
  return error;
}

int ps_read(ps_session *s, void *out, size_t capacity, ps_frame *meta, int timeout_ms) {
  struct timespec limit;
  clock_gettime(CLOCK_REALTIME, &limit);
  limit.tv_sec += timeout_ms / 1000;
  limit.tv_nsec += (timeout_ms % 1000) * 1000000L;
  if (limit.tv_nsec >= 1000000000L) { limit.tv_sec++; limit.tv_nsec -= 1000000000L; }
  pthread_mutex_lock(&s->mutex);
  while (s->running && !s->failed && s->meta.sequence == s->consumed) {
    int result = pthread_cond_timedwait(&s->cond, &s->mutex, &limit);
    if (result == ETIMEDOUT) { pthread_mutex_unlock(&s->mutex); return 0; }
    if (result) { pthread_mutex_unlock(&s->mutex); return -1; }
  }
  if (s->failed || !s->running) { pthread_mutex_unlock(&s->mutex); return -1; }
  *meta = s->meta;
  if (capacity < s->meta.bytes) { pthread_mutex_unlock(&s->mutex); return -2; }
  memcpy(out, s->data, s->meta.bytes);
  s->consumed = s->meta.sequence;
  pthread_mutex_unlock(&s->mutex);
  return 1;
}

int ps_control(ps_session *s, int control, int value) {
  if (control == 0) {
    int error = uvc_set_ae_mode(s->handle, 1);
    if (error && error != UVC_ERROR_PIPE && error != UVC_ERROR_NOT_SUPPORTED) return error;
    return uvc_set_exposure_abs(s->handle, value);
  }
  if (control == 1) return uvc_set_gain(s->handle, value);
  return UVC_ERROR_INVALID_PARAM;
}

int ps_control_range(ps_session *s, int control, double *minimum, double *maximum, double *current) {
  if (control == 0) {
    uint32_t lo, hi, value;
    int error = uvc_get_exposure_abs(s->handle, &lo, UVC_GET_MIN);
    if (error) return error;
    error = uvc_get_exposure_abs(s->handle, &hi, UVC_GET_MAX);
    if (error) return error;
    error = uvc_get_exposure_abs(s->handle, &value, UVC_GET_CUR);
    if (error) return error;
    *minimum = lo / 10.0; *maximum = hi / 10.0; *current = value / 10.0;
    return 0;
  }
  if (control == 1) {
    uint16_t lo, hi, value;
    int error = uvc_get_gain(s->handle, &lo, UVC_GET_MIN);
    if (error) return error;
    error = uvc_get_gain(s->handle, &hi, UVC_GET_MAX);
    if (error) return error;
    error = uvc_get_gain(s->handle, &value, UVC_GET_CUR);
    if (error) return error;
    *minimum = lo; *maximum = hi; *current = value;
    return 0;
  }
  return UVC_ERROR_INVALID_PARAM;
}

void ps_close(ps_session *s) {
  if (!s) return;
  if (s->running) uvc_stop_streaming(s->handle);
  uvc_close(s->handle);
  uvc_exit(s->ctx);
  pthread_cond_destroy(&s->cond);
  pthread_mutex_destroy(&s->mutex);
  free(s->data);
  free(s);
}
