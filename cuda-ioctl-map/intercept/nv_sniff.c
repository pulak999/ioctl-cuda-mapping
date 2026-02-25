/*
 * nv_sniff.c — LD_PRELOAD interposer for NVIDIA ioctl capture.
 *
 * For every open/ioctl call on /dev/nvidia*, records:
 *   - open: path, return value (including failed opens)
 *   - ioctl: fd, device path, request code, before/after arg buffers (hex), return value
 *
 * Output format: JSON-lines, one record per line, to $NV_SNIFF_LOG.
 *
 * Build:  gcc -fPIC -shared -O2 -Wall -o libnv_sniff.so nv_sniff.c -ldl
 * Use:    NV_SNIFF_LOG=out.jsonl LD_PRELOAD=./libnv_sniff.so ./program
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <pthread.h>
#include <dlfcn.h>
#include <sys/ioctl.h>

/* ── ioctl size extraction (standard Linux encoding) ── */
#define _NV_IOC_SIZEBITS  14
#define _NV_IOC_SIZEMASK  ((1u << _NV_IOC_SIZEBITS) - 1)
#define _NV_IOC_SIZE(nr)  (((unsigned)(nr) >> 16) & _NV_IOC_SIZEMASK)

/* Buffer cap: we never try to snapshot more than this many bytes.
 * For UVM ioctls whose _IOC_SIZE is 0, we use exactly this as fallback.
 * For any ioctl whose encoded size exceeds this, we clamp to it. */
#define MAX_CAPTURE_SZ  4096

/* Maximum number of simultaneously open /dev/nvidia* fds we track. */
#define MAX_FDS  4096

/* ── real function pointers (resolved once at init) ── */
static int  (*real_open)  (const char *, int, ...)  = NULL;
static int  (*real_openat)(int, const char *, int, ...) = NULL;
static int  (*real_close) (int)                     = NULL;
static int  (*real_ioctl) (int, unsigned long, ...) = NULL;

/* ── per-fd tracking ── */
static int   nv_fd_active[MAX_FDS]; /* 1 if fd is an open /dev/nvidia* fd */
static char *nv_fd_path  [MAX_FDS]; /* malloced path string, or NULL       */

/* ── log state ── */
static FILE           *log_fp      = NULL;
static long            seq_counter = 0;
static pthread_mutex_t lock        = PTHREAD_MUTEX_INITIALIZER;

/* ── helpers ── */

static int is_nvidia_path(const char *path) {
    return path && strncmp(path, "/dev/nvidia", 11) == 0;
}

static void track_fd(int fd, const char *path) {
    if (fd < 0 || fd >= MAX_FDS) return;
    pthread_mutex_lock(&lock);
    nv_fd_active[fd] = 1;
    free(nv_fd_path[fd]);
    nv_fd_path[fd] = strdup(path);
    pthread_mutex_unlock(&lock);
}

static void untrack_fd(int fd) {
    if (fd < 0 || fd >= MAX_FDS) return;
    pthread_mutex_lock(&lock);
    nv_fd_active[fd] = 0;
    free(nv_fd_path[fd]);
    nv_fd_path[fd] = NULL;
    pthread_mutex_unlock(&lock);
}

static int fd_is_nvidia(int fd) {
    if (fd < 0 || fd >= MAX_FDS) return 0;
    pthread_mutex_lock(&lock);
    int active = nv_fd_active[fd];
    pthread_mutex_unlock(&lock);
    return active;
}

/* Caller must hold lock when calling this. */
static const char *fd_path(int fd) {
    if (fd < 0 || fd >= MAX_FDS || !nv_fd_path[fd]) return "";
    return nv_fd_path[fd];
}

static void hex_encode(const uint8_t *buf, size_t sz, char *out) {
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < sz; i++) {
        out[i * 2]     = hex[(buf[i] >> 4) & 0xf];
        out[i * 2 + 1] = hex[ buf[i]       & 0xf];
    }
    out[sz * 2] = '\0';
}

/* ── constructor / destructor ── */

__attribute__((constructor))
static void nv_sniff_init(void) {
    real_open   = dlsym(RTLD_NEXT, "open");
    real_openat = dlsym(RTLD_NEXT, "openat");
    real_close  = dlsym(RTLD_NEXT, "close");
    real_ioctl  = dlsym(RTLD_NEXT, "ioctl");

    memset(nv_fd_active, 0, sizeof(nv_fd_active));
    memset(nv_fd_path,   0, sizeof(nv_fd_path));

    const char *logpath = getenv("NV_SNIFF_LOG");
    if (logpath) {
        log_fp = fopen(logpath, "w");
        if (!log_fp) {
            fprintf(stderr, "[nv_sniff] WARNING: cannot open log '%s': %s\n",
                    logpath, strerror(errno));
        }
    }
}

__attribute__((destructor))
static void nv_sniff_fini(void) {
    if (log_fp) {
        fflush(log_fp);
        fclose(log_fp);
        log_fp = NULL;
    }
}

/* ── open ── */

static void log_open_event(const char *path, int ret) {
    if (!log_fp) return;
    pthread_mutex_lock(&lock);
    long s = seq_counter++;
    fprintf(log_fp,
            "{\"type\":\"open\",\"seq\":%ld,\"path\":\"%s\",\"ret\":%d}\n",
            s, path, ret);
    fflush(log_fp);
    pthread_mutex_unlock(&lock);
}

int open(const char *path, int flags, ...) {
    if (!real_open) real_open = dlsym(RTLD_NEXT, "open");

    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }

    int fd = (flags & O_CREAT) ? real_open(path, flags, mode)
                                : real_open(path, flags);

    if (is_nvidia_path(path)) {
        int logged_ret = (fd < 0) ? -errno : fd;
        if (fd >= 0) track_fd(fd, path);
        log_open_event(path, logged_ret);
    }
    return fd;
}

/* Also hook open64 (same as open on 64-bit Linux but belt-and-suspenders). */
int open64(const char *path, int flags, ...) __attribute__((alias("open")));

int openat(int dirfd, const char *path, int flags, ...) {
    if (!real_openat) real_openat = dlsym(RTLD_NEXT, "openat");

    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }

    int fd = (flags & O_CREAT) ? real_openat(dirfd, path, flags, mode)
                                : real_openat(dirfd, path, flags);

    if (is_nvidia_path(path)) {
        int logged_ret = (fd < 0) ? -errno : fd;
        if (fd >= 0) track_fd(fd, path);
        log_open_event(path, logged_ret);
    }
    return fd;
}

int openat64(int dirfd, const char *path, int flags, ...)
    __attribute__((alias("openat")));

/* ── close ── */

int close(int fd) {
    if (!real_close) real_close = dlsym(RTLD_NEXT, "close");
    untrack_fd(fd);   /* evict before the real close */
    return real_close(fd);
}

/* ── ioctl ── */

int ioctl(int fd, unsigned long request, ...) {
    if (!real_ioctl) real_ioctl = dlsym(RTLD_NEXT, "ioctl");

    va_list ap;
    va_start(ap, request);
    void *arg = va_arg(ap, void *);
    va_end(ap);

    /* Pass through immediately for non-nvidia fds or when logging is off. */
    if (!log_fp || !fd_is_nvidia(fd))
        return real_ioctl(fd, request, arg);

    /* Determine capture size. */
    size_t sz = _NV_IOC_SIZE(request);
    if (sz == 0 || sz > MAX_CAPTURE_SZ)
        sz = MAX_CAPTURE_SZ;

    /* Allocate snapshot buffers. */
    uint8_t *before_buf = malloc(sz);
    uint8_t *after_buf  = malloc(sz);
    char    *before_hex = malloc(sz * 2 + 1);
    char    *after_hex  = malloc(sz * 2 + 1);

    if (!before_buf || !after_buf || !before_hex || !after_hex) {
        free(before_buf); free(after_buf);
        free(before_hex); free(after_hex);
        return real_ioctl(fd, request, arg);
    }

    /* Snapshot arg before the call. */
    if (arg)
        memcpy(before_buf, arg, sz);
    else
        memset(before_buf, 0, sz);

    /* Issue the real ioctl. */
    int ret       = real_ioctl(fd, request, arg);
    int saved_err = errno;

    /* Snapshot arg after the call. */
    if (arg)
        memcpy(after_buf, arg, sz);
    else
        memset(after_buf, 0, sz);

    hex_encode(before_buf, sz, before_hex);
    hex_encode(after_buf,  sz, after_hex);

    /* Emit the JSON-lines record.  Lock covers seq increment + write. */
    pthread_mutex_lock(&lock);
    long s = seq_counter++;
    fprintf(log_fp,
            "{\"type\":\"ioctl\",\"seq\":%ld,\"fd\":%d,\"dev\":\"%s\","
            "\"req\":\"0x%08lX\",\"sz\":%zu,"
            "\"before\":\"%s\",\"after\":\"%s\",\"ret\":%d}\n",
            s, fd, fd_path(fd), (unsigned long)request, sz,
            before_hex, after_hex, ret);
    fflush(log_fp);
    pthread_mutex_unlock(&lock);

    free(before_buf); free(after_buf);
    free(before_hex); free(after_hex);

    errno = saved_err;
    return ret;
}
