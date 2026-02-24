/*
 * handle_map.h — fixed-capacity open-addressed hash map (uint32_t → uint32_t).
 *
 * Designed for NVIDIA RM handle remapping during ioctl replay.
 * Capacity: 4096 entries (cuInit allocates ~200 objects, plenty of headroom).
 * The sentinel key value 0xFFFFFFFF must never appear as a real handle.
 * Handle 0 is never a valid RM handle; lookups for key==0 always return "not found".
 */

#ifndef HANDLE_MAP_H
#define HANDLE_MAP_H

#include <stdint.h>
#include <string.h>
#include <stdio.h>

#define HM_CAPACITY  4096u
#define HM_SENTINEL  0xFFFFFFFFu

typedef struct {
    uint32_t keys[HM_CAPACITY];
    uint32_t vals[HM_CAPACITY];
    uint32_t count;
} HandleMap;

/* Initialise: mark all slots as empty. */
static inline void hm_init(HandleMap *m) {
    memset(m->keys, 0xFF, sizeof(m->keys));  /* all slots = HM_SENTINEL */
    memset(m->vals, 0x00, sizeof(m->vals));
    m->count = 0;
}

/* Knuth multiplicative hash for uint32. */
static inline uint32_t hm_hash(uint32_t key) {
    return (uint32_t)((key * 2654435769u) % HM_CAPACITY);
}

/*
 * Insert or update key→val.
 * Returns 1 on success, 0 if the table is full (should never happen in practice).
 */
static inline int hm_put(HandleMap *m, uint32_t key, uint32_t val) {
    if (key == 0 || key == HM_SENTINEL) return 0;
    uint32_t idx = hm_hash(key);
    for (uint32_t i = 0; i < HM_CAPACITY; i++) {
        uint32_t slot = (idx + i) % HM_CAPACITY;
        if (m->keys[slot] == HM_SENTINEL || m->keys[slot] == key) {
            if (m->keys[slot] == HM_SENTINEL) m->count++;
            m->keys[slot] = key;
            m->vals[slot] = val;
            return 1;
        }
    }
    fprintf(stderr, "[handle_map] ERROR: table full (capacity %u)\n", HM_CAPACITY);
    return 0;
}

/*
 * Look up key.  Sets *val_out on success.
 * Returns 1 if found, 0 if not found or key==0.
 */
static inline int hm_get(const HandleMap *m, uint32_t key, uint32_t *val_out) {
    if (key == 0 || key == HM_SENTINEL) return 0;
    uint32_t idx = hm_hash(key);
    for (uint32_t i = 0; i < HM_CAPACITY; i++) {
        uint32_t slot = (idx + i) % HM_CAPACITY;
        if (m->keys[slot] == HM_SENTINEL) return 0;  /* empty slot → not found */
        if (m->keys[slot] == key) {
            *val_out = m->vals[slot];
            return 1;
        }
    }
    return 0;
}

/* Iterate over all entries.  Call hm_dump() to print the full map. */
static inline void hm_dump(const HandleMap *m) {
    printf("[handle_map] %u entries:\n", m->count);
    for (uint32_t i = 0; i < HM_CAPACITY; i++) {
        if (m->keys[i] != HM_SENTINEL)
            printf("  orig=0x%08X  →  replay=0x%08X\n",
                   m->keys[i], m->vals[i]);
    }
}

#endif /* HANDLE_MAP_H */
