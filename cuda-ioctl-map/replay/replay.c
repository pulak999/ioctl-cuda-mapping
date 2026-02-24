/*
 * replay.c — raw ioctl replay tool for NVIDIA driver reverse-engineering.
 *
 * Reads a capture produced by libnv_sniff.so (JSONL format), re-opens the
 * device files in the same order, and re-issues every ioctl with the captured
 * 'before' buffer.  Handle values in the input buffers are patched using a
 * mapping derived from intercept/handle_offsets.json.
 *
 * Usage:
 *   ./replay <capture.jsonl> [handle_offsets.json]
 *
 * handle_offsets.json defaults to "../intercept/handle_offsets.json" relative
 * to the directory containing the capture file.
 *
 * Exit code: 0 if all ioctls returned 0, 1 otherwise.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <sys/ioctl.h>

#include "handle_map.h"

/* ── tunables ── */

#define LINE_BUF_SZ     (1 << 17)   /* 128 KiB — fits even the largest UVM line */
#define MAX_FD_MAP      4096
#define MAX_SCHEMAS     128
#define MAX_HANDLE_OFFS 16

/* ── schema for one request code ── */

typedef struct {
    uint32_t req;
    int      handle_offsets[MAX_HANDLE_OFFS];
    int      n_handle_offsets;
    int      output_handle_offset;   /* -1 if this code never writes a new handle */
    int      fd_offsets[MAX_HANDLE_OFFS];
    int      n_fd_offsets;           /* offsets that hold kernel fd numbers (not RM handles) */
} ReqSchema;

/* ── globals ── */

static int       fd_map[MAX_FD_MAP];   /* orig_fd → replay_fd; -1 = unmapped */
static ReqSchema schemas[MAX_SCHEMAS];
static int       n_schemas = 0;
static HandleMap hmap;

/* ─────────────────────────────────────────────────────────
 * Minimal JSON field extractors (flat JSONL only).
 * ───────────────────────────────────────────────────────── */

/*
 * Extract a string value for "key":"value" from a JSON line.
 * Returns 1 on success.  Handles simple backslash-escape for \".
 */
static int json_str(const char *line, const char *key,
                    char *out, int out_sz)
{
    /* Build search pattern  "key":" */
    char pat[256];
    int plen = snprintf(pat, sizeof(pat), "\"%s\":\"", key);
    if (plen <= 0) return 0;

    const char *p = strstr(line, pat);
    if (!p) return 0;
    p += plen;   /* now points at first char of value */

    int i = 0;
    while (*p && i < out_sz - 1) {
        if (*p == '\\' && *(p+1) == '"') { out[i++] = '"'; p += 2; continue; }
        if (*p == '"') break;
        out[i++] = *p++;
    }
    out[i] = '\0';
    return 1;
}

/*
 * Extract a decimal (or negative) integer for "key":number.
 * Returns 1 on success.
 */
static int json_long(const char *line, const char *key, long *out)
{
    char pat[256];
    int plen = snprintf(pat, sizeof(pat), "\"%s\":", key);
    if (plen <= 0) return 0;

    const char *p = strstr(line, pat);
    if (!p) return 0;
    p += plen;
    while (*p == ' ') p++;

    char *end;
    *out = strtol(p, &end, 10);
    return (end > p) ? 1 : 0;
}

/*
 * Extract a hex string value (e.g. "req":"0xC020462A") into a uint32_t.
 * Returns 1 on success.
 */
static int json_u32hex(const char *line, const char *key, uint32_t *out)
{
    char val[20];
    if (!json_str(line, key, val, sizeof(val))) return 0;
    char *end;
    unsigned long v = strtoul(val, &end, 16);
    if (end == val) return 0;
    *out = (uint32_t)v;
    return 1;
}

/*
 * Decode a hex string that is the value of "key":"<hexdata>".
 * Allocates a buffer via malloc; caller frees.
 * Sets *len_out to the decoded byte count.
 * Returns NULL on failure.
 */
static uint8_t *json_hexbuf(const char *line, const char *key, int *len_out)
{
    char pat[256];
    int plen = snprintf(pat, sizeof(pat), "\"%s\":\"", key);
    if (plen <= 0) return NULL;

    const char *p = strstr(line, pat);
    if (!p) return NULL;
    p += plen;

    /* Count hex characters until closing '"' */
    const char *start = p;
    int hex_len = 0;
    while (*p && *p != '"') { p++; hex_len++; }
    if (hex_len == 0) { *len_out = 0; return calloc(1, 1); }
    if (hex_len & 1) return NULL;   /* must be even */

    int byte_len = hex_len / 2;
    uint8_t *buf = malloc(byte_len);
    if (!buf) return NULL;

    for (int i = 0; i < byte_len; i++) {
        int hi = start[i*2];
        int lo = start[i*2+1];
        hi = (hi >= '0' && hi <= '9') ? hi - '0'
           : (hi >= 'a' && hi <= 'f') ? hi - 'a' + 10
           : (hi >= 'A' && hi <= 'F') ? hi - 'A' + 10 : -1;
        lo = (lo >= '0' && lo <= '9') ? lo - '0'
           : (lo >= 'a' && lo <= 'f') ? lo - 'a' + 10
           : (lo >= 'A' && lo <= 'F') ? lo - 'A' + 10 : -1;
        if (hi < 0 || lo < 0) { free(buf); return NULL; }
        buf[i] = (uint8_t)((hi << 4) | lo);
    }
    *len_out = byte_len;
    return buf;
}

/* ── uint32 little-endian read/write ── */

static inline uint32_t rd32(const uint8_t *buf, int off) {
    uint32_t v;
    memcpy(&v, buf + off, 4);
    return v;
}

static inline void wr32(uint8_t *buf, int off, uint32_t v) {
    memcpy(buf + off, &v, 4);
}

/* ─────────────────────────────────────────────────────────
 * handle_offsets.json parser
 * ───────────────────────────────────────────────────────── */

static ReqSchema *find_schema(uint32_t req) {
    for (int i = 0; i < n_schemas; i++)
        if (schemas[i].req == req) return &schemas[i];
    return NULL;
}

static void load_schemas(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) {
        /* Non-fatal: replay without handle patching. */
        fprintf(stderr, "[replay] NOTE: %s not found — no handle patching\n", path);
        return;
    }

    /* Read entire file into a string. */
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    rewind(f);
    char *buf = malloc(fsize + 1);
    if (!buf) { fclose(f); return; }
    size_t nread = fread(buf, 1, fsize, f);
    buf[nread] = '\0';   /* nread ≤ fsize; null-terminate what we got */
    fclose(f);

    /*
     * The file looks like:
     *   {
     *     "0xC020462A": {
     *       "handle_offsets": [0, 4, 8],
     *       "output_handle_offset": 8,
     *       ...
     *     },
     *     ...
     *   }
     *
     * Strategy: scan for each  "0x  pattern at the start of an entry.
     * Then look ahead for "handle_offsets" and "output_handle_offset".
     */
    const char *p = buf;
    while ((p = strstr(p, "\"0x")) != NULL) {
        p++;  /* skip opening '"' */

        /* Extract req hex string */
        char hex_str[20] = {0};
        int  hlen = 0;
        const char *q = p;
        while (*q && *q != '"' && hlen < 18) hex_str[hlen++] = *q++;
        if (*q != '"') { p = q + 1; continue; }
        p = q + 1;   /* advance past closing '"' */

        uint32_t req = (uint32_t)strtoul(hex_str, NULL, 16);
        if (req == 0 || n_schemas >= MAX_SCHEMAS) continue;

        ReqSchema *s = &schemas[n_schemas++];
        s->req                = req;
        s->n_handle_offsets   = 0;
        s->output_handle_offset = -1;
        s->n_fd_offsets       = 0;

        /* Find the closing '}' for this entry — work within that window. */
        const char *entry_start = p;
        const char *entry_end   = strchr(p, '}');
        if (!entry_end) entry_end = buf + fsize;

        /* ── parse "handle_offsets": [...] ── */
        const char *ho_tag = strstr(entry_start, "\"handle_offsets\":");
        if (ho_tag && ho_tag < entry_end) {
            const char *arr = strchr(ho_tag + 17, '[');
            if (arr && arr < entry_end) {
                arr++;  /* skip '[' */
                while (*arr && *arr != ']' && arr < entry_end) {
                    while (*arr == ' ' || *arr == '\n' || *arr == '\r' ||
                           *arr == '\t' || *arr == ',') arr++;
                    if (*arr == ']' || arr >= entry_end) break;
                    char *end;
                    long off = strtol(arr, &end, 10);
                    if (end > arr && s->n_handle_offsets < MAX_HANDLE_OFFS) {
                        s->handle_offsets[s->n_handle_offsets++] = (int)off;
                    }
                    arr = end;
                }
            }
        }

        /* ── parse "output_handle_offset": N ── */
        const char *oho_tag = strstr(entry_start, "\"output_handle_offset\":");
        if (oho_tag && oho_tag < entry_end) {
            const char *num = oho_tag + strlen("\"output_handle_offset\":");
            while (*num == ' ') num++;
            char *end;
            long val = strtol(num, &end, 10);
            if (end > num) s->output_handle_offset = (int)val;
        }

        /* ── parse "fd_offsets": [...] ── */
        const char *fdo_tag = strstr(entry_start, "\"fd_offsets\":");
        if (fdo_tag && fdo_tag < entry_end) {
            const char *arr = strchr(fdo_tag + 13, '[');
            if (arr && arr < entry_end) {
                arr++;
                while (*arr && *arr != ']' && arr < entry_end) {
                    while (*arr == ' ' || *arr == '\n' || *arr == '\r' ||
                           *arr == '\t' || *arr == ',') arr++;
                    if (*arr == ']' || arr >= entry_end) break;
                    char *end;
                    long off = strtol(arr, &end, 10);
                    if (end > arr && s->n_fd_offsets < MAX_HANDLE_OFFS) {
                        s->fd_offsets[s->n_fd_offsets++] = (int)off;
                    }
                    arr = end;
                }
            }
        }
    }

    free(buf);
    printf("[replay] loaded %d req schemas from %s\n", n_schemas, path);
}

/* ─────────────────────────────────────────────────────────
 * Main
 * ───────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <capture.jsonl> [handle_offsets.json]\n",
                argv[0]);
        return 1;
    }

    const char *capture_path = argv[1];

    /* Derive default path for handle_offsets.json */
    char default_offsets[1024];
    {
        char tmp[1024];
        strncpy(tmp, capture_path, sizeof(tmp) - 1);
        tmp[sizeof(tmp)-1] = '\0';
        /* Find last '/' to strip filename */
        char *slash = strrchr(tmp, '/');
        if (slash) *slash = '\0'; else strcpy(tmp, ".");
        /* Go one level up from sniffed/ to root, then into intercept/ */
        snprintf(default_offsets, sizeof(default_offsets),
                 "%s/../intercept/handle_offsets.json", tmp);
    }
    const char *offsets_path = (argc >= 3) ? argv[2] : default_offsets;

    /* ── Initialise ── */
    hm_init(&hmap);
    for (int i = 0; i < MAX_FD_MAP; i++) fd_map[i] = -1;

    load_schemas(offsets_path);

    /* ── Open capture file ── */
    FILE *cap = fopen(capture_path, "r");
    if (!cap) {
        perror(capture_path);
        return 1;
    }

    char *line = malloc(LINE_BUF_SZ);
    if (!line) { perror("malloc"); fclose(cap); return 1; }

    int total   = 0;
    int ok      = 0;
    int failed  = 0;

    /* ── Process records in seq order ── */
    while (fgets(line, LINE_BUF_SZ, cap)) {
        /* Skip empty lines */
        if (line[0] == '\n' || line[0] == '\0') continue;

        /* Determine record type */
        char type[8];
        if (!json_str(line, "type", type, sizeof(type))) continue;

        /* ═══ open record ═══ */
        if (strcmp(type, "open") == 0) {
            char path[256];
            long ret_logged;
            if (!json_str(line, "path", path, sizeof(path))) continue;
            if (!json_long(line, "ret", &ret_logged)) continue;

            if (ret_logged < 0) {
                /* Failed open in the capture: attempt it anyway, expect failure */
                int fd = open(path, O_RDWR);
                if (fd >= 0) {
                    close(fd);
                    printf("[open] %s → fd=%d (expected failure but succeeded)\n",
                           path, fd);
                } else {
                    printf("[open] %s → %s (expected)\n", path, strerror(errno));
                }
            } else {
                int orig_fd = (int)ret_logged;
                int replay_fd = open(path, O_RDWR);
                if (replay_fd < 0) {
                    fprintf(stderr, "[open] FAILED: %s: %s\n", path, strerror(errno));
                    /* Continue — downstream ioctls on this fd will fail too */
                    printf("[open] %s → FAILED (%s)\n", path, strerror(errno));
                } else {
                    if (orig_fd < MAX_FD_MAP)
                        fd_map[orig_fd] = replay_fd;
                    printf("[open] %s → fd=%d (orig %d)\n", path, replay_fd, orig_fd);
                }
            }
            continue;
        }

        /* ═══ ioctl record ═══ */
        if (strcmp(type, "ioctl") != 0) continue;

        long seq_val, fd_val, sz_val;
        uint32_t req;

        if (!json_long(line, "seq", &seq_val)) continue;
        if (!json_long(line, "fd",  &fd_val))  continue;
        if (!json_u32hex(line, "req", &req))   continue;
        if (!json_long(line, "sz",  &sz_val))  continue;

        int orig_fd = (int)fd_val;
        int replay_fd = (orig_fd >= 0 && orig_fd < MAX_FD_MAP)
                        ? fd_map[orig_fd] : -1;

        if (replay_fd < 0) {
            printf("[%04ld] req=0x%08X  SKIP (fd %d not mapped)\n",
                   seq_val, req, orig_fd);
            total++;
            failed++;
            continue;
        }

        /* Decode before/after hex buffers */
        int before_len = 0, after_len = 0;
        uint8_t *before_buf = json_hexbuf(line, "before", &before_len);
        uint8_t *after_buf  = json_hexbuf(line, "after",  &after_len);

        if (!before_buf) {
            printf("[%04ld] req=0x%08X  SKIP (failed to decode before buffer)\n",
                   seq_val, req);
            free(after_buf);
            total++;
            failed++;
            continue;
        }

        /* Working buffer: copy of before bytes that we patch in-place */
        uint8_t *work = malloc(before_len > 0 ? before_len : 1);
        if (!work) {
            free(before_buf); free(after_buf);
            total++; failed++;
            continue;
        }
        memcpy(work, before_buf, before_len);

        /* ── Patch input handles ── */
        ReqSchema *s = find_schema(req);
        if (s) {
            for (int i = 0; i < s->n_handle_offsets; i++) {
                int off = s->handle_offsets[i];
                if (off + 4 > before_len) continue;
                uint32_t orig_val = rd32(work, off);
                uint32_t new_val  = 0;
                if (orig_val != 0 && hm_get(&hmap, orig_val, &new_val)) {
                    wr32(work, off, new_val);
                }
            }

            /* ── Patch embedded fd numbers (e.g. NV_ESC_REGISTER_FD) ── */
            for (int i = 0; i < s->n_fd_offsets; i++) {
                int off = s->fd_offsets[i];
                if (off + 4 > before_len) continue;
                int orig_fd_val = (int)rd32(work, off);
                if (orig_fd_val >= 0 && orig_fd_val < MAX_FD_MAP
                        && fd_map[orig_fd_val] >= 0) {
                    wr32(work, off, (uint32_t)fd_map[orig_fd_val]);
                }
            }
        }

        /* ── Issue the ioctl ── */
        int ret = ioctl(replay_fd, (unsigned long)req,
                        (before_len > 0) ? work : NULL);
        int saved_err = errno;

        total++;
        int ok_flag = (ret == 0);
        if (ok_flag) ok++; else failed++;

        /* Name lookup for pretty output */
        char dev[128] = "";
        json_str(line, "dev", dev, sizeof(dev));

        printf("[%04ld] %-30s  req=0x%08X  fd=%d  sz=%ld  ret=%d  %s\n",
               seq_val,
               (dev[0] ? dev : "(unknown)"),
               req, replay_fd, sz_val, ret,
               ok_flag ? "OK" : "FAIL");
        if (!ok_flag)
            fprintf(stderr, "         errno=%d (%s)\n", saved_err, strerror(saved_err));

        /* ── Record output handle if this is an alloc-type code ── */
        if (s && s->output_handle_offset >= 0) {
            int ooff = s->output_handle_offset;
            if (after_buf && ooff + 4 <= after_len && ooff + 4 <= before_len) {
                uint32_t orig_out   = rd32(after_buf, ooff);
                uint32_t replay_out = rd32(work, ooff);
                if (orig_out != 0 && replay_out != 0) {
                    hm_put(&hmap, orig_out, replay_out);
                }
            }
        }

        free(before_buf);
        free(after_buf);
        free(work);
    }

    fclose(cap);
    free(line);

    /* ── Summary ── */
    printf("\n");
    printf("DONE — %d/%d succeeded, %d failed\n", ok, total, failed);
    printf("[handle_map] final state: %u entries\n", hmap.count);
    if (hmap.count <= 32)
        hm_dump(&hmap);

    /* Write sentinel file so run_validation.sh knows replay finished */
    {
        FILE *sf = fopen("replay.ready", "w");
        if (sf) fclose(sf);
    }

    return (failed == 0) ? 0 : 1;
}
