/* Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (LICENSE, NOTICE.md).
 *
 * System contracts for games beyond the one this runtime was first written for, starting with
 * Sengoku Basara 4 Sumeragi (CUSA01159). Written for this project; the behaviour of the PS4
 * calls was worked out from the game's own use of them, with shadPS4's headers as the reference
 * for structure layouts:
 *   - kernel event flags: wait / poll / set / clear / cancel;
 *   - AppContent, PlayGo, Rtc, UserService and SystemService calls the base runtime lacked;
 *   - Ngs2: a software sound graph. Sampler voices (PCM16, float, ATRAC9 through LibAtrac9, with
 *     repeats, skip samples, pitch and streaming rings), submixers that call the game's own effect
 *     callbacks, and masterers that write the buffers handed to AudioOut. Reverb, EQ and filters
 *     are not applied;
 *   - online, streaming, Remote Play, Share Play, screenshots and recording on an offline
 *     console: a "not signed in to PSN, no network" model like runtime_services.c.
 * Every entry is an explicit contract; unknown functions still stop in probe.c. */
#define _GNU_SOURCE
#include "runtime.h"
#include "gpu/spartanportgpu.h"
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <math.h>
#include "third_party/LibAtrac9/C/src/libatrac9.h"

#define ORBIS_OK 0
#define KERNEL_ESRCH ((int32_t)0x80020003)
#define KERNEL_EPERM ((int32_t)0x80020001)
#define KERNEL_EACCES ((int32_t)0x8002000D)
#define KERNEL_EBUSY ((int32_t)0x80020010)
#define KERNEL_EINVAL ((int32_t)0x80020016)
#define KERNEL_ENAMETOOLONG ((int32_t)0x8002003F)
#define KERNEL_ETIMEDOUT ((int32_t)0x8002003C)
#define KERNEL_ECANCELED ((int32_t)0x80020055)
#define APP_CONTENT_PARAMETER ((int32_t)0x80D90002)
#define APP_CONTENT_NOT_FOUND ((int32_t)0x80D90005)
#define RTC_INVALID_POINTER ((int32_t)0x80B50002)
#define RTC_BAD_PARSE ((int32_t)0x80B50007)
#define PLAYGO_BAD_POINTER ((int32_t)0x80B2000A)
#define USER_INVALID_ARGUMENT ((int32_t)0x80960005)
#define NET_CTL_NOT_CONNECTED ((int32_t)0x80412108)
#define NET_CTL_INVALID_ADDR ((int32_t)0x80412107)
#define NP_SIGNED_OUT ((int32_t)0x80550006)
#define NP_INVALID_ARGUMENT ((int32_t)0x80550003)
#define HTTP_NETWORK ((int32_t)0x80431063)
#define NGS2_INVALID_SYSTEM_HANDLE ((int32_t)0x804A0230)
#define NGS2_INVALID_RACK_HANDLE ((int32_t)0x804A0261)
#define NGS2_INVALID_VOICE_HANDLE ((int32_t)0x804A0300)
#define NGS2_INVALID_OUT_ADDRESS ((int32_t)0x804A0053)
#define NGS2_INVALID_WAVEFORM_DATA ((int32_t)0x804A0240)
#define DIALOG_ARG_NULL ((int32_t)0x80B8000D)
#define UNIX_EPOCH_TICKS UINT64_C(0xdcbffeff2bc000)

static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static int next_id=0x100;
static int32_t new_id(void) { pthread_mutex_lock(&lock); int id=next_id++; pthread_mutex_unlock(&lock); return id; }
static void note_once(int *seen, const char *what) {
    pthread_mutex_lock(&lock);
    int first=!*seen; *seen=1;
    pthread_mutex_unlock(&lock);
    if (first) printf("Runtime: %s\n",what);
}
static ABI int32_t ok(void) { return ORBIS_OK; }
static ABI int32_t object_id(void) { return new_id(); }
static ABI int32_t np_signed_out(void) { return NP_SIGNED_OUT; }
static ABI int32_t http_fail(void) { return HTTP_NETWORK; }

/* ---- Kernel event flags ---- */
#define EVF_ATTR_SINGLE 0x10
#define EVF_WAIT_AND 0x01
#define EVF_WAIT_OR 0x02
#define EVF_CLEAR_ALL 0x10
#define EVF_CLEAR_PAT 0x20
typedef struct EventFlag {
    pthread_mutex_t mutex;
    pthread_cond_t cond;
    uint64_t bits;
    uint64_t generation; /* bumped by Cancel and Delete: a waiter woken by either reports it */
    int waiters, single, deleted, canceled_gen_valid;
    uint64_t cancel_generation, cancel_pattern;
    char name[32];
} EventFlag;
static int evf_mode_valid(uint32_t mode) {
    uint32_t wait=mode&0xf, clear=mode&0xf0;
    return (wait==EVF_WAIT_AND || wait==EVF_WAIT_OR) && (clear==0 || clear==EVF_CLEAR_ALL || clear==EVF_CLEAR_PAT);
}
static int evf_met(const EventFlag *f, uint64_t pattern, uint32_t mode) {
    return (mode&0xf)==EVF_WAIT_AND ? (f->bits&pattern)==pattern : (f->bits&pattern)!=0;
}
static void evf_clear(EventFlag *f, uint64_t pattern, uint32_t mode) {
    if ((mode&0xf0)==EVF_CLEAR_ALL) f->bits=0;
    else if ((mode&0xf0)==EVF_CLEAR_PAT) f->bits&=~pattern;
}
static ABI int32_t evf_create(EventFlag **out, const char *name, uint32_t attr, uint64_t init, const void *opt) {
    if (!out || !name || opt || attr>0x22) return KERNEL_EINVAL;
    if (strlen(name)>=32) return KERNEL_ENAMETOOLONG;
    EventFlag *f=calloc(1,sizeof *f);
    if (!f) return KERNEL_EINVAL;
    pthread_mutex_init(&f->mutex,NULL);
    pthread_cond_init(&f->cond,NULL);
    f->bits=init;
    f->single=(attr&0xf0)!=0x20; /* SINGLE unless MULTI */
    snprintf(f->name,sizeof f->name,"%s",name);
    *out=f;
    return ORBIS_OK;
}
static ABI int32_t evf_delete(EventFlag *f) {
    if (!f) return KERNEL_ESRCH;
    pthread_mutex_lock(&f->mutex);
    f->deleted=1; ++f->generation;
    pthread_cond_broadcast(&f->cond);
    while (f->waiters) pthread_cond_wait(&f->cond,&f->mutex);
    pthread_mutex_unlock(&f->mutex);
    pthread_cond_destroy(&f->cond);
    pthread_mutex_destroy(&f->mutex);
    free(f);
    return ORBIS_OK;
}
static ABI int32_t evf_set(EventFlag *f, uint64_t pattern) {
    if (!f) return KERNEL_ESRCH;
    pthread_mutex_lock(&f->mutex);
    f->bits|=pattern;
    pthread_cond_broadcast(&f->cond);
    pthread_mutex_unlock(&f->mutex);
    return ORBIS_OK;
}
static ABI int32_t evf_cancel(EventFlag *f, uint64_t pattern, int32_t *waiting) {
    if (!f) return KERNEL_ESRCH;
    pthread_mutex_lock(&f->mutex);
    if (waiting) *waiting=f->waiters;
    f->bits=pattern;
    f->cancel_pattern=pattern;
    f->cancel_generation=++f->generation;
    f->canceled_gen_valid=1;
    pthread_cond_broadcast(&f->cond);
    pthread_mutex_unlock(&f->mutex);
    return ORBIS_OK;
}
static void deadline_after(struct timespec *ts, uint32_t micros) {
    clock_gettime(CLOCK_REALTIME,ts); /* winpthreads condition variables wait on CLOCK_REALTIME */
    uint64_t ns=(uint64_t)ts->tv_nsec+(uint64_t)micros*1000u;
    ts->tv_sec+=(time_t)(ns/1000000000u);
    ts->tv_nsec=(long)(ns%1000000000u);
}
static uint32_t micros_left(const struct timespec *deadline) {
    struct timespec now; clock_gettime(CLOCK_REALTIME,&now);
    int64_t left=(int64_t)(deadline->tv_sec-now.tv_sec)*1000000+(deadline->tv_nsec-now.tv_nsec)/1000;
    return left>0 ? (uint32_t)left : 0;
}
static ABI int32_t evf_wait(EventFlag *f, uint64_t pattern, uint32_t mode, uint64_t *result, uint32_t *timeout) {
    if (!f) return KERNEL_ESRCH;
    if (!pattern || !evf_mode_valid(mode)) return KERNEL_EINVAL;
    pthread_mutex_lock(&f->mutex);
    if (f->deleted) { pthread_mutex_unlock(&f->mutex); return KERNEL_EACCES; }
    if (f->single && f->waiters) { pthread_mutex_unlock(&f->mutex); return KERNEL_EPERM; }
    int32_t status=ORBIS_OK;
    if (!evf_met(f,pattern,mode)) {
        struct timespec deadline={0};
        if (timeout) deadline_after(&deadline,*timeout);
        uint64_t generation=f->generation;
        ++f->waiters;
        while (!evf_met(f,pattern,mode)) {
            if (f->generation!=generation) {
                status=f->deleted ? KERNEL_EACCES : KERNEL_ECANCELED;
                break;
            }
            int rc=timeout ? pthread_cond_timedwait(&f->cond,&f->mutex,&deadline) : pthread_cond_wait(&f->cond,&f->mutex);
            if (rc==ETIMEDOUT && !evf_met(f,pattern,mode) && f->generation==generation) { status=KERNEL_ETIMEDOUT; break; }
        }
        --f->waiters;
        if (f->deleted && !f->waiters) pthread_cond_broadcast(&f->cond); /* evf_delete waits for this */
        if (timeout) *timeout=status==KERNEL_ETIMEDOUT ? 0 : micros_left(&deadline);
    }
    if (result) *result=status==KERNEL_ECANCELED ? f->cancel_pattern : f->bits;
    if (status==ORBIS_OK) evf_clear(f,pattern,mode);
    pthread_mutex_unlock(&f->mutex);
    return status;
}
static ABI int32_t evf_poll(EventFlag *f, uint64_t pattern, uint32_t mode, uint64_t *result) {
    if (!f) return KERNEL_ESRCH;
    if (!pattern || !evf_mode_valid(mode)) return KERNEL_EINVAL;
    pthread_mutex_lock(&f->mutex);
    int32_t status=evf_met(f,pattern,mode) ? ORBIS_OK : KERNEL_EBUSY;
    if (result) *result=f->bits;
    if (status==ORBIS_OK) evf_clear(f,pattern,mode);
    pthread_mutex_unlock(&f->mutex);
    return status;
}
static ABI int32_t evf_clear_bits(EventFlag *f, uint64_t pattern) {
    if (!f) return KERNEL_ESRCH;
    pthread_mutex_lock(&f->mutex); f->bits&=pattern; pthread_mutex_unlock(&f->mutex);
    return ORBIS_OK;
}

/* A thread cannot be cancelled here: guest code has no cancellation points the host can use. */
static int cancel_seen;
static ABI int32_t pthread_cancel_guest(void *thread) {
    (void)thread;
    note_once(&cancel_seen,"scePthreadCancel ignored (threads end on their own or at exit)");
    return ORBIS_OK;
}

/* ---- AppContent: extracted add-ons and temporary data on /temp0 ---- */
typedef struct { char data[17]; char padding[3]; } EntitlementLabel;
typedef struct { char data[16]; } AppContentMountPoint;
static char dlc_directory[512];
int runtime_offline_configure_dlc_dir(const char *path) {
    if (!path || !*path || strlen(path)>=sizeof(dlc_directory)) return -1;
    snprintf(dlc_directory,sizeof(dlc_directory),"%s",path);
    return 0;
}
static ABI int32_t addcont_mount(uint32_t service_label, const EntitlementLabel *entitlement,
                                AppContentMountPoint *mount_point) {
    if (service_label>7 || !entitlement || !mount_point ||
        !memchr(entitlement->data,'\0',sizeof(entitlement->data))) return APP_CONTENT_PARAMETER;
    int index=runtime_content_addon_index(service_label,entitlement->data);
    if (index<0 || !dlc_directory[0]) return APP_CONTENT_NOT_FOUND;
    char guest[16], host[512];
    int guest_size=snprintf(guest,sizeof(guest),"/addcont%d",index);
    int host_size=snprintf(host,sizeof(host),"%s/%s",dlc_directory,entitlement->data);
    struct stat status;
    if (guest_size<0 || (size_t)guest_size>=sizeof(guest) || host_size<0 ||
        (size_t)host_size>=sizeof(host) || stat(host,&status) || !S_ISDIR(status.st_mode))
        return APP_CONTENT_NOT_FOUND;
    if (runtime_file_mount(guest,host)) return APP_CONTENT_NOT_FOUND;
    memset(mount_point,0,sizeof(*mount_point));
    memcpy(mount_point->data,guest,(size_t)guest_size);
    printf("Runtime: AppContent mounted %s -> %s\n",guest,host);
    return ORBIS_OK;
}
static ABI int32_t addcont_unmount(const AppContentMountPoint *mount_point) {
    if (!mount_point || !memchr(mount_point->data,'\0',sizeof(mount_point->data)))
        return APP_CONTENT_PARAMETER;
    runtime_file_unmount(mount_point->data);
    return ORBIS_OK;
}
static ABI int32_t temp_mount2(uint32_t option, char *mount_point) {
    (void)option; /* FORMAT would wipe user/temp0: the game recreates what it needs anyway */
    if (!mount_point) return APP_CONTENT_PARAMETER;
    memcpy(mount_point,"/temp0",7);
    return ORBIS_OK;
}
static ABI int32_t space_kb(const char *mount_point, uint64_t *kb) {
    (void)mount_point;
    if (!kb) return APP_CONTENT_PARAMETER;
    *kb=1048576; /* 1 GiB free, as shadPS4 reports */
    return ORBIS_OK;
}

/* ---- PlayGo additions (the package is fully installed) ---- */
static ABI int32_t playgo_language_mask(int32_t handle, uint64_t *mask) {
    (void)handle;
    if (!mask) return PLAYGO_BAD_POINTER;
    *mask=~UINT64_C(0); return ORBIS_OK;
}
static ABI int32_t playgo_todo_list(int32_t handle, void *list, uint32_t count, uint32_t *out) {
    (void)handle; (void)list; (void)count;
    if (!out) return PLAYGO_BAD_POINTER;
    *out=0; return ORBIS_OK;
}

/* ---- Rtc: RFC 3339 date-time to a UTC tick (microseconds since 0001-01-01) ---- */
static int64_t days_from_civil(int64_t y, unsigned m, unsigned d) {
    y-=m<=2;
    const int64_t era=(y>=0 ? y : y-399)/400;
    const unsigned yoe=(unsigned)(y-era*400);
    const unsigned doy=(153*(m+(m>2 ? -3 : 9))+2)/5+d-1;
    const unsigned doe=yoe*365+yoe/4-yoe/100+doy;
    return era*146097+(int64_t)doe-719468;
}
static ABI int32_t rtc_parse_rfc3339(uint64_t *tick, const char *text) {
    if (!tick || !text) return RTC_INVALID_POINTER;
    int year, month, day, hour, minute, second, used=0;
    if (sscanf(text,"%4d-%2d-%2d%*1[Tt ]%2d:%2d:%2d%n",&year,&month,&day,&hour,&minute,&second,&used)!=6)
        return RTC_BAD_PARSE;
    if (month<1 || month>12 || day<1 || day>31 || hour>23 || minute>59 || second>60) return RTC_BAD_PARSE;
    const char *p=text+used;
    uint64_t micros=0;
    if (*p=='.') {
        unsigned digits=0;
        for (++p; *p>='0' && *p<='9'; ++p) if (digits++<6) micros=micros*10+(uint64_t)(*p-'0');
        while (digits<6) { micros*=10; ++digits; }
    }
    int64_t offset=0;
    if (*p=='Z' || *p=='z') ++p;
    else if (*p=='+' || *p=='-') {
        int oh, om;
        if (sscanf(p+1,"%2d:%2d",&oh,&om)!=2) return RTC_BAD_PARSE;
        offset=(int64_t)(oh*60+om)*60*(*p=='-' ? -1 : 1);
    } else return RTC_BAD_PARSE;
    int64_t seconds=days_from_civil(year,(unsigned)month,(unsigned)day)*86400+hour*3600+minute*60+second-offset;
    *tick=UNIX_EPOCH_TICKS+(uint64_t)(seconds*1000000)+micros;
    return ORBIS_OK;
}

/* ---- UserService / SystemService additions ---- */
static ABI int32_t user_color(int32_t user, int32_t *color) {
    (void)user;
    if (!color) return USER_INVALID_ARGUMENT;
    *color=0; /* blue */
    return ORBIS_OK;
}

/* ---- Net / NetCtl / Http additions: no network ---- */
static ABI int32_t netctl_result(int32_t event, int32_t *error) {
    (void)event;
    if (!error) return NET_CTL_INVALID_ADDR;
    *error=NET_CTL_NOT_CONNECTED; return ORBIS_OK;
}
static ABI int32_t net_mac(unsigned char *address, int32_t flags) {
    (void)flags;
    if (!address) return (int32_t)0x80410116; /* NET EINVAL */
    static const unsigned char mac[6]={0x02,0x00,0x00,0x00,0x00,0x01}; /* locally administered */
    memcpy(address,mac,6); return ORBIS_OK;
}
static ABI int32_t http_ssl_error(int32_t id, int32_t *error, uint32_t *detail) {
    (void)id;
    if (error) *error=0;
    if (detail) *detail=0;
    return ORBIS_OK;
}
static ABI int32_t http_last_errno(int32_t id, int32_t *error) { (void)id; if (error) *error=0; return ORBIS_OK; }

/* ---- NP additions: one local user who has never signed up to PSN ---- */
static ABI int32_t np_has_signed_up(int32_t user, unsigned char *signed_up) {
    (void)user;
    if (!signed_up) return NP_INVALID_ARGUMENT;
    *signed_up=0; return ORBIS_OK;
}
static ABI int32_t np_poll(int32_t request, int32_t *result) { (void)request; if (result) *result=NP_SIGNED_OUT; return ORBIS_OK; }
static ABI int32_t voice_endpoint(int32_t *id) { if (!id) return (int32_t)0x80A20001; *id=new_id(); return ORBIS_OK; }
static ABI int32_t voice_packet(int32_t connection, void *data, uint32_t *size) {
    (void)connection; (void)data;
    if (size) *size=0; return ORBIS_OK;
}
static ABI int32_t voice_get_attribute(int32_t id, int32_t attribute, void *value, int32_t size) {
    (void)id; (void)attribute;
    if (value && size>0 && size<=256) memset(value,0,(size_t)size);
    return ORBIS_OK;
}

/* ---- Common dialogs that never appear offline: finished, canceled by the user ---- */
#define DIALOG_INITIALIZED 1
#define DIALOG_FINISHED 3
static int error_dialog_status;
static ABI int32_t error_dialog_init(void) { error_dialog_status=DIALOG_INITIALIZED; return ORBIS_OK; }
static ABI int32_t error_dialog_open(const unsigned char *param) {
    if (!param) return DIALOG_ARG_NULL;
    uint32_t code; memcpy(&code,param+4,4); /* OrbisErrorDialogParam: size, errorCode, userId */
    printf("Runtime: ErrorDialog for 0x%08x; closed immediately (no dialog UI yet)\n",code);
    error_dialog_status=DIALOG_FINISHED; return ORBIS_OK;
}
static ABI int32_t error_dialog_update(void) { return error_dialog_status; }
static ABI int32_t error_dialog_term(void) { error_dialog_status=0; return ORBIS_OK; }
static ABI int32_t dialog_update_none(void) { return 0; } /* never opened: status NONE */
static ABI int32_t dialog_result_canceled(unsigned char *result) {
    if (!result) return DIALOG_ARG_NULL;
    int32_t canceled=1; /* USER_CANCELED */
    memcpy(result,&canceled,4); return ORBIS_OK;
}
static ABI int32_t commerce_result(unsigned char *result) {
    if (!result) return DIALOG_ARG_NULL;
    int32_t canceled=1; memcpy(result,&canceled,4); result[4]=0; /* not authorized */
    return ORBIS_OK;
}

/* ---- Streaming, Share Play, Remote Play, screenshots and recording: inactive ---- */
static ABI int32_t live_status(void *status) { if (status) memset(status,0,72); return ORBIS_OK; } /* not on air */
static ABI int32_t live_message_count(uint32_t *count) { if (count) *count=0; return ORBIS_OK; }
/* OrbisSharePlayConnectionInfo: status, mode, two 20-byte online ids, two user ids (56 bytes). */
static ABI int32_t shareplay_info(void *info) { if (info) memset(info,0,56); return ORBIS_OK; } /* DORMANT */

/* ---- Ngs2: a minimal working sound graph ----
 * Racks hold real voices. SystemRender runs, in dependency order, every playing voice:
 *   - sampler (rack 0x1000): PCM (int16/float) and ATRAC9 (LibAtrac9) waveform blocks with
 *     repeats, skip samples, pitch and rate conversion;
 *   - submixer (0x2000, 0x2001): the sum of the voices patched into it, then the game's user FX
 *     callback, which receives the channel buffers (Sengoku Basara 4 mixes its sound there);
 *   - mastering (0x3000): the sum of its inputs, written to the render buffer it names.
 * Each voice output goes to the voices its ports are patched to, scaled by the port volume.
 * Matrices, envelopes, filters, compressors and reverb are accepted and not applied.
 * Parameter layouts follow shadPS4's ngs2*.h; ids were observed in Sengoku Basara 4. */
#define NGS2_MAGIC_SYSTEM 0x4e475331u
#define NGS2_MAGIC_RACK 0x4e475352u
#define NGS2_MAGIC_VOICE 0x4e475356u
#define NGS2_MAX_CHANNELS 8
#define NGS2_MAX_GRAIN 1024
#define NGS2_MAX_PORTS 4
#define NGS2_MAX_MATRICES 8
#define NGS2_MAX_MATRIX_LEVELS 64
#define NGS2_MAX_BLOCKS 64
#define NGS2_RACK_SAMPLER 0x1000
#define NGS2_RACK_MASTERING 0x3000
struct Ngs2Rack;
typedef struct Ngs2Block { uint32_t offset, size, repeats, skip, samples, reserved; uint64_t user; } Ngs2Block;
typedef struct Ngs2Wav { FILE *f; uint32_t channels, rate; uint64_t frames; } Ngs2Wav; /* SPARTAN_AUDIO_WAV_DIR capture */
typedef struct { void *buffer; uint64_t size; uint32_t waveform, channels; } Ngs2RenderBuffer;
typedef struct Ngs2Voice {
    uint32_t magic;
    struct Ngs2Rack *rack;
    uint32_t index, channels;
    int playing, paused;
    /* routing */
    float port_volume[NGS2_MAX_PORTS];
    struct Ngs2Voice *patch[NGS2_MAX_PORTS];
    int32_t port_matrix[NGS2_MAX_PORTS];
    uint32_t port_dest_input[NGS2_MAX_PORTS];
    float matrix_levels[NGS2_MAX_MATRICES][NGS2_MAX_MATRIX_LEVELS];
    uint32_t matrix_levels_count[NGS2_MAX_MATRICES];
    uint32_t output_buffer; /* mastering: the render buffer it writes */
    /* user FX (submixer) */
    uintptr_t fx, fx_data[3];
    /* sampler */
    uint32_t wave_type, wave_channels, wave_rate, wave_config;
    /* ATRAC9 (0x40): decoder, decoded frame, byte cursor in the block, superframe padding */
    void *at9;
    uint32_t at9_config;
    Atrac9CodecInfo at9_info;
    int16_t at9_pcm[256*NGS2_MAX_CHANNELS];
    int at9_count, at9_pos, at9_frames, at9_primed;
    uint32_t at9_byte, at9_skip, at9_emitted; /* byte cursor in the current block; emitted: samples of this block played */
    uint32_t at9_sf_have, at9_sf_pos;         /* the superframe being gathered / decoded */
    int at9_sf_ready, at9_starved, at9_errors, at9_decoded, at9_retries;
    unsigned char *at9_sf;                    /* staging for one superframe: a superframe may span two blocks */
    float at9_prev[NGS2_MAX_CHANNELS], at9_cur[NGS2_MAX_CHANNELS];
    double at9_phase;
    const unsigned char *wave_data;
    Ngs2Block blocks[NGS2_MAX_BLOCKS];
    const unsigned char *block_data[NGS2_MAX_BLOCKS]; /* each queued block has the data pointer it was submitted with */
    uint8_t block_starts[NGS2_MAX_BLOCKS]; /* streaming: the queued chunk begins a waveform block (flags bit 1 clear) */
    uint32_t block_count, block, repeats_done;
    double position; /* frames into the current block */
    int streaming;   /* waveform blocks flag 1: a ring the game refills; it wraps instead of ending */
    double consumed; /* source frames played since the reset: GetState numDecodedSamples */
    uint64_t consumed_bytes; /* GetState decodedDataSize */
    float pitch;
    uint32_t state_calls; /* GetState calls since creation (a streaming game polls it) */
    int log_starved;      /* last logged starved/refilled state of a streaming voice */
    uint32_t flag_calls;  /* GetStateFlags calls: a voice the game waits on is polled hundreds of times a second */
    int fresh;            /* a new waveform was submitted since the last honoured Play (event 0) */
    uint32_t stream_total; /* ATRAC9 streams: the sample count of the first waveform block (logging) */
    int stream_final;      /* streaming: the last chunk submitted had flags bit 0 clear - nothing follows it */
    int stream_track;      /* ATRAC9 stream: stream_left is valid */
    uint64_t stream_left;  /* samples the current waveform block still has to play */
    int stream_wait;       /* the current waveform block is done; the next one has not been submitted yet */
    Ngs2Wav wav;          /* capture of this long voice's output */
    int log_pending, log_major; /* SPARTAN_AUDIO_DEBUG */
    /* per-render state */
    int level, done;
    float peak_seen; /* trace: largest output since the last graph report */
    float in[NGS2_MAX_CHANNELS][NGS2_MAX_GRAIN];
    float out[NGS2_MAX_CHANNELS][NGS2_MAX_GRAIN];
    uint32_t last_start_ms;
    int spatial_pan_mode; /* 0=normal center, 1=pan left (dual stereo pair 1), 2=pan right (dual stereo pair 2) */
} Ngs2Voice;
typedef struct Ngs2Rack {
    uint32_t magic, id, voice_count;
    uint32_t serial; /* creation order: the three sampler racks all have id 0x1000 */
    struct Ngs2System *system;
    struct Ngs2Rack *next;
    Ngs2Voice *voices;
} Ngs2Rack;
typedef struct Ngs2System {
    uint32_t magic, grain, rate;
    pthread_mutex_t lock;
    Ngs2Rack *racks;
} Ngs2System;
static int ngs2_trace=-1;
static int ngs2_tracing(void) { if (ngs2_trace<0) ngs2_trace=getenv("SPARTAN_NGS2_TRACE")!=NULL; return ngs2_trace; }
static Ngs2System *as_system(void *h) { Ngs2System *s=h; return s && s->magic==NGS2_MAGIC_SYSTEM ? s : NULL; }
static Ngs2Rack *as_rack(void *h) { Ngs2Rack *r=h; return r && r->magic==NGS2_MAGIC_RACK ? r : NULL; }
static Ngs2Voice *as_voice(uintptr_t h) { Ngs2Voice *v=(Ngs2Voice *)h; return v && v->magic==NGS2_MAGIC_VOICE ? v : NULL; }
static int ngs2_system_seen;

/* ---- SPARTAN_AUDIO_DEBUG helpers ---- */
static int ngs2_measure; /* peaks are tracked when either trace is on */
static uint32_t ngs2_buffer_count;
static unsigned ngs2_starts, ngs2_major_starts, ngs2_minor_starts;
static uint32_t ngs2_total_bytes(const Ngs2Voice *v) { uint32_t n=0; for (uint32_t i=0;i<v->block_count;++i) n+=v->blocks[i].size; return n; }
static const char *ngs2_wave_name(uint32_t type) {
    switch (type) { case 0x12: return "pcm16"; case 0x13: return "float"; case 0x40: return "atrac9"; case 0: return "none"; default: return "other"; }
}
/* Long sounds (music, dialogue) are logged individually; short effects only as counts. */
static int ngs2_is_major(const Ngs2Voice *v) {
    if (v->rack->id!=NGS2_RACK_SAMPLER) return 0;
    if (v->streaming) return 1;
    if (v->wave_type!=0x12 && v->wave_type!=0x13 && v->wave_type!=0x40) return 1;
    return ngs2_total_bytes(v)>=(v->wave_type==0x40 ? 24576u : 131072u);
}
static void ngs2_describe(const Ngs2Voice *v, char *out, size_t size) {
    int n=snprintf(out,size,"type=%#x(%s) channels=%u rate=%u config=%#x blocks=%u bytes=%u streaming=%d pitch=%.3f data=%p",
                   v->wave_type,ngs2_wave_name(v->wave_type),v->wave_channels,v->wave_rate,v->wave_config,v->block_count,
                   ngs2_total_bytes(v),v->streaming,v->pitch,(const void *)v->wave_data);
    for (int p=0;p<NGS2_MAX_PORTS && n>0 && (size_t)n<size;++p) {
        if (v->patch[p]) {
            if (v->port_matrix[p] >= 0)
                n+=snprintf(out+n,size-(size_t)n," p%d->%#x/%u@%.2f(m%d)",p,v->patch[p]->rack->id,v->patch[p]->index,v->port_volume[p],v->port_matrix[p]);
            else
                n+=snprintf(out+n,size-(size_t)n," p%d->%#x/%u@%.2f",p,v->patch[p]->rack->id,v->patch[p]->index,v->port_volume[p]);
        }
    }
}
/* Whether the voice's sound can reach a playing mastering voice that writes a render buffer. */
static int ngs2_routed(const Ngs2Voice *v, int depth) {
    if (depth>8) return 0;
    if (v->rack->id==NGS2_RACK_MASTERING) return v->playing && v->output_buffer<ngs2_buffer_count;
    for (int p=0;p<NGS2_MAX_PORTS;++p) {
        const Ngs2Voice *d=v->patch[p];
        if (d && d->playing && fabsf(v->port_volume[p])>1e-6f && ngs2_routed(d,depth+1)) return 1;
    }
    return 0;
}
static double ngs2_played_ms(const Ngs2Voice *v) { return v->consumed*1000.0/(v->wave_rate ? (double)v->wave_rate : 48000.0); }

/* ---- SPARTAN_AUDIO_WAV_DIR=<existing dir>: float WAV capture, capped at 3 GiB in total ----
 * master_b<N>.wav   what the game hands to AudioOut for render buffer N (first SPARTAN_AUDIO_WAV_CH channels, default 2)
 * voice_r<R>_v<V>_<ms>.wav   one file per long sampler voice (R = rack serial), after decoding and pitch */
static uint64_t ngs2_wav_budget=3ull<<30;
static const char *ngs2_wav_dir(void) {
    static int init; static const char *dir;
    if (!init) { init=1; dir=getenv("SPARTAN_AUDIO_WAV_DIR"); if (dir && !dir[0]) dir=NULL; }
    return dir;
}
static void ngs2_wav_put32(unsigned char *p, uint32_t v) { p[0]=(unsigned char)v; p[1]=(unsigned char)(v>>8); p[2]=(unsigned char)(v>>16); p[3]=(unsigned char)(v>>24); }
static void ngs2_wav_header(Ngs2Wav *w) {
    unsigned char h[44]; uint64_t bytes=w->frames*w->channels*4u; uint32_t data=bytes>0xffffffffu ? 0xffffffffu : (uint32_t)bytes;
    memcpy(h,"RIFF",4); ngs2_wav_put32(h+4,36u+data); memcpy(h+8,"WAVEfmt ",8); ngs2_wav_put32(h+16,16);
    h[20]=3; h[21]=0; h[22]=(unsigned char)w->channels; h[23]=0; ngs2_wav_put32(h+24,w->rate);
    ngs2_wav_put32(h+28,w->rate*w->channels*4u); h[32]=(unsigned char)(w->channels*4u); h[33]=0; h[34]=32; h[35]=0;
    memcpy(h+36,"data",4); ngs2_wav_put32(h+40,data);
    fseek(w->f,0,SEEK_SET); fwrite(h,1,sizeof h,w->f); fseek(w->f,0,SEEK_END);
}
static void ngs2_wav_open(Ngs2Wav *w, const char *name, uint32_t channels, uint32_t rate) {
    const char *dir=ngs2_wav_dir();
    if (!dir || w->f || ngs2_wav_budget<(1u<<20) || !channels || channels>8) return;
    char path[1024]; snprintf(path,sizeof path,"%s/%s",dir,name);
    w->f=fopen(path,"wb");
    if (!w->f) { static int reported; if (reported++<3) runtime_audio_log("wav.open_failed path=%s",path); return; }
    w->channels=channels; w->rate=rate; w->frames=0; ngs2_wav_header(w);
    runtime_audio_log("wav.open file=%s channels=%u rate=%u",name,channels,rate);
}
static void ngs2_wav_write(Ngs2Wav *w, const float *interleaved, uint32_t frames) {
    if (!w->f) return;
    uint64_t bytes=(uint64_t)frames*w->channels*4u;
    if (bytes>ngs2_wav_budget) { ngs2_wav_header(w); fclose(w->f); w->f=NULL; runtime_audio_log("wav.budget_reached"); ngs2_wav_budget=0; return; }
    fwrite(interleaved,1,(size_t)bytes,w->f); ngs2_wav_budget-=bytes; w->frames+=frames;
}
static void ngs2_wav_close(Ngs2Wav *w) { if (w->f) { ngs2_wav_header(w); fclose(w->f); w->f=NULL; } }
static void ngs2_wav_write_planar(Ngs2Wav *w, float (*planes)[NGS2_MAX_GRAIN], uint32_t frames) {
    float mix[NGS2_MAX_GRAIN*NGS2_MAX_CHANNELS];
    if (!w->f || frames>NGS2_MAX_GRAIN) return;
    for (uint32_t f=0;f<frames;++f) for (uint32_t c=0;c<w->channels;++c) mix[f*w->channels+c]=planes[c][f];
    ngs2_wav_write(w,mix,frames);
}
static Ngs2Wav ngs2_master_wav[8];
static void ngs2_wav_masters(const Ngs2RenderBuffer *buffers, uint32_t count, uint32_t frames) {
    if (!ngs2_wav_dir()) return;
    static uint32_t keep; if (!keep) { const char *e=getenv("SPARTAN_AUDIO_WAV_CH"); keep=e ? (uint32_t)atoi(e) : 2; if (keep<1 || keep>8) keep=2; }
    for (uint32_t b=0; b<count && b<8; ++b) {
        const Ngs2RenderBuffer *out=&buffers[b];
        if (!out->buffer || !out->channels) continue;
        uint32_t channels=out->channels<keep ? out->channels : keep;
        Ngs2Wav *w=&ngs2_master_wav[b];
        if (!w->f) { char name[48]; snprintf(name,sizeof name,"master_b%u.wav",b); ngs2_wav_open(w,name,channels,48000); }
        if (!w->f) continue;
        uint64_t room=out->size/((uint64_t)out->channels*sizeof(float));
        uint32_t n=frames<room ? frames : (uint32_t)room;
        float mix[NGS2_MAX_GRAIN*8]; const float *src=out->buffer;
        for (uint32_t f=0;f<n && f<NGS2_MAX_GRAIN;++f) for (uint32_t c=0;c<channels;++c) mix[f*channels+c]=src[(size_t)f*out->channels+c];
        ngs2_wav_write(w,mix,n<NGS2_MAX_GRAIN ? n : NGS2_MAX_GRAIN);
    }
}
static void ngs2_wav_begin(Ngs2Voice *v) {
    if (!ngs2_wav_dir() || !v->log_major || v->rack->id!=NGS2_RACK_SAMPLER) return;
    ngs2_wav_close(&v->wav);
    char name[96]; snprintf(name,sizeof name,"voice_r%u_v%u_%llu.wav",v->rack->serial,v->index,(unsigned long long)runtime_audio_log_ms());
    ngs2_wav_open(&v->wav,name,v->wave_channels ? v->wave_channels : 2,v->rack->system->rate);
}

/* ---- who calls the state APIs: guest return addresses, counted per call site ---- */
typedef struct { const char *api; uint32_t tag; uintptr_t ra; uint64_t count, reported; } Ngs2Callsite;
static Ngs2Callsite ngs2_callsites[256];
static pthread_mutex_t ngs2_callsite_lock=PTHREAD_MUTEX_INITIALIZER;
static void ngs2_note_callsite(const char *api, uint32_t tag, void *ra) {
    if (!runtime_audio_log_enabled()) return;
    int fresh=0; uint64_t count=0;
    pthread_mutex_lock(&ngs2_callsite_lock);
    Ngs2Callsite *slot=NULL;
    for (size_t i=0;i<sizeof ngs2_callsites/sizeof *ngs2_callsites;++i) {
        Ngs2Callsite *c=&ngs2_callsites[i];
        if (!c->api) { slot=c; fresh=1; break; }
        if (c->api==api && c->tag==tag && c->ra==(uintptr_t)ra) { slot=c; break; }
    }
    if (slot) { if (fresh) { slot->api=api; slot->tag=tag; slot->ra=(uintptr_t)ra; } count=++slot->count; }
    pthread_mutex_unlock(&ngs2_callsite_lock);
    if (fresh) runtime_audio_log("ngs2.callsite_new api=%s tag=%#x ra=%#llx",api,tag,(unsigned long long)(uintptr_t)ra);
    (void)count;
}
static void ngs2_report_callsites(void) {
    pthread_mutex_lock(&ngs2_callsite_lock);
    for (size_t i=0;i<sizeof ngs2_callsites/sizeof *ngs2_callsites;++i) {
        Ngs2Callsite *c=&ngs2_callsites[i];
        if (!c->api || c->count==c->reported) continue;
        runtime_audio_log("ngs2.callsite api=%s tag=%#x ra=%#llx count=%llu delta=%llu",c->api,c->tag,(unsigned long long)c->ra,
                          (unsigned long long)c->count,(unsigned long long)(c->count-c->reported));
        c->reported=c->count;
    }
    pthread_mutex_unlock(&ngs2_callsite_lock);
}
static void ngs2_log_start(Ngs2Voice *v) {
    v->log_pending=0;
    v->log_major=ngs2_is_major(v);
    ngs2_wav_begin(v);
    ++ngs2_starts; if (v->log_major) ++ngs2_major_starts; else ++ngs2_minor_starts;

    /* Global Dual-Voice Stereo Panning / Deduplication:
       Capcom MT Framework generates pseudo-stereo voices by spawning twin mono voices
       with identical audio data pointers at the exact same millisecond. In HLE, route
       pair 1 to Left-biased and pair 2 to Right-biased, or mute identical clone to prevent flanging / echo. */
    v->spatial_pan_mode = 0;
    struct timespec now_ts; clock_gettime(CLOCK_MONOTONIC, &now_ts);
    uint32_t now_ms = (uint32_t)((uint64_t)now_ts.tv_sec * 1000u + (uint64_t)now_ts.tv_nsec / 1000000u);
    v->last_start_ms = now_ms;

    if (v->channels == 1 && v->rack && v->rack->id == NGS2_RACK_SAMPLER) {
        for (uint32_t other_i = 0; other_i < v->rack->voice_count; ++other_i) {
            Ngs2Voice *other = &v->rack->voices[other_i];
            if (other != v && other->playing && other->channels == 1 &&
                other->wave_data == v->wave_data && other->wave_data != NULL &&
                (now_ms - other->last_start_ms) <= 50u) {
                /* Found active twin mono voice launched simultaneously */
                if (other->spatial_pan_mode == 0 || other->spatial_pan_mode == 1) {
                    other->spatial_pan_mode = 1; /* Pan Left */
                    v->spatial_pan_mode = 2;     /* Pan Right */
                }
                break;
            }
        }
    }

    if (!runtime_audio_log_enabled() || v->rack->id!=NGS2_RACK_SAMPLER) return;
    if (!v->log_major && ngs2_starts>300) return; /* the first effects show what is used; the rest is counted */
    char what[400]; ngs2_describe(v,what,sizeof what);
    runtime_audio_log("ngs2.start rack=%#x/%u voice=%u major=%d %s",v->rack->id,v->rack->serial,v->index,v->log_major,what);
}
static void ngs2_log_end(Ngs2Voice *v) {
    runtime_audio_log("ngs2.end rack=%#x/%u voice=%u reason=finished played_ms=%.0f",v->rack->id,v->rack->serial,v->index,ngs2_played_ms(v));
    ngs2_wav_close(&v->wav);
    v->log_major=0;
}
/* Every ~2 s: what plays, how loud the mix is, which long sounds have no route to the output. */
static void ngs2_log_summary(Ngs2System *s) {
    static uint64_t last_ms; static int last_playing; static unsigned windows;
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    uint64_t ms=(uint64_t)t.tv_sec*1000u+(uint64_t)t.tv_nsec/1000000u;
    if (last_ms && ms-last_ms<2000) return;
    last_ms=ms;
    pthread_mutex_lock(&s->lock);
    int playing=0, sampler=0, submix=0, master=0, lines=0;
    float master_peak=0.0f;
    for (Ngs2Rack *r=s->racks; r; r=r->next)
        for (uint32_t i=0;i<r->voice_count;++i) {
            const Ngs2Voice *v=&r->voices[i];
            if (!v->playing) continue;
            ++playing;
            if (r->id==NGS2_RACK_SAMPLER) ++sampler; else if (r->id==NGS2_RACK_MASTERING) { ++master; if (v->peak_seen>master_peak) master_peak=v->peak_seen; } else ++submix;
        }
    if (playing || last_playing) {
        runtime_audio_log("ngs2.summary window=%u playing=%d sampler=%d submix=%d master=%d master_peak=%.4f starts=%u major=%u minor=%u",
                          ++windows,playing,sampler,submix,master,master_peak,ngs2_starts,ngs2_major_starts,ngs2_minor_starts);
        for (Ngs2Rack *r=s->racks; r; r=r->next)
            for (uint32_t i=0;i<r->voice_count && lines<24;++i) {
                const Ngs2Voice *v=&r->voices[i];
                if (!v->playing || r->id!=NGS2_RACK_SAMPLER) continue;
                int routed=ngs2_routed(v,0);
                if (!v->log_major && routed) continue;
                ++lines;
                runtime_audio_log("ngs2.voice rack=%#x/%u voice=%u routed=%d paused=%d type=%s peak=%.4f played_ms=%.0f state_calls=%u streaming=%d queued=%u flag_calls=%u",
                                  r->id,r->serial,v->index,routed,v->paused,ngs2_wave_name(v->wave_type),v->peak_seen,ngs2_played_ms(v),v->state_calls,v->streaming,v->block_count,v->flag_calls);
            }
    }
    last_playing=playing;
    for (Ngs2Rack *r=s->racks; r; r=r->next)  /* keep captured files readable even if the game is killed */
        for (uint32_t i=0;i<r->voice_count;++i) if (r->voices[i].wav.f) { ngs2_wav_header(&r->voices[i].wav); fflush(r->voices[i].wav.f); }
    for (int b=0;b<8;++b) if (ngs2_master_wav[b].f) { ngs2_wav_header(&ngs2_master_wav[b]); fflush(ngs2_master_wav[b].f); }
    { static unsigned tick; if (++tick%5==0) ngs2_report_callsites(); }
    for (Ngs2Rack *r=s->racks; r; r=r->next)
        for (uint32_t i=0;i<r->voice_count;++i) r->voices[i].peak_seen=0.0f;
    pthread_mutex_unlock(&s->lock);
}
static ABI int32_t ngs2_system_create(const unsigned char *option, const void *allocator, void **out) {
    (void)allocator;
    if (!out) return NGS2_INVALID_OUT_ADDRESS;
    Ngs2System *s=calloc(1,sizeof *s);
    if (!s) return NGS2_INVALID_SYSTEM_HANDLE;
    s->magic=NGS2_MAGIC_SYSTEM; s->grain=256; s->rate=48000;
    if (option) { /* OrbisNgs2SystemOption: size, name[16], flags, maxGrain, numGrain @32, rate @36 */
        uint32_t grain, rate; memcpy(&grain,option+32,4); memcpy(&rate,option+36,4);
        if (grain && grain<=NGS2_MAX_GRAIN) s->grain=grain;
        if (rate) s->rate=rate;
    }
    /* Recursive: a user FX callback run by SystemRender may itself call VoiceControl. */
    pthread_mutexattr_t attr; pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr,PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&s->lock,&attr); pthread_mutexattr_destroy(&attr);
    *out=s;
    note_once(&ngs2_system_seen,"Ngs2: sound graph (sampler PCM/ATRAC9, submixer user FX, mastering; no effects)");
    ngs2_measure=ngs2_tracing() || runtime_audio_log_enabled();
    runtime_audio_log("ngs2.system grain=%u rate=%u",s->grain,s->rate);
    return ORBIS_OK;
}
static ABI int32_t ngs2_system_destroy(void *handle, void *buffer_info) {
    Ngs2System *s=as_system(handle);
    if (!s) return NGS2_INVALID_SYSTEM_HANDLE;
    if (buffer_info) memset(buffer_info,0,64); /* OrbisNgs2ContextBufferInfo: nothing to free */
    pthread_mutex_lock(&s->lock);
    for (Ngs2Rack *r=s->racks,*n; r; r=n) { n=r->next; r->magic=0; free(r->voices); free(r); }
    s->racks=NULL; s->magic=0;
    pthread_mutex_unlock(&s->lock);
    return ORBIS_OK; /* the system itself stays allocated: a late call finds a dead magic */
}
static ABI int32_t ngs2_rack_create(void *system, uint32_t rack_id, const unsigned char *option, const void *allocator, void **out) {
    (void)allocator;
    Ngs2System *s=as_system(system);
    if (!s) return NGS2_INVALID_SYSTEM_HANDLE;
    if (!out) return NGS2_INVALID_OUT_ADDRESS;
    uint32_t voices=1;
    if (option) { memcpy(&voices,option+8+16+4+4,4); if (!voices) voices=1; } /* size, name[16], flags, grain, maxVoices */
    if (voices>4096) voices=4096;
    Ngs2Rack *r=calloc(1,sizeof *r);
    Ngs2Voice *v=r ? calloc(voices,sizeof *v) : NULL;
    if (!v) { free(r); return NGS2_INVALID_RACK_HANDLE; }
    static uint32_t rack_serial;
    r->magic=NGS2_MAGIC_RACK; r->id=rack_id; r->voice_count=voices; r->system=s; r->voices=v; r->serial=++rack_serial;
    uint32_t master_index=0;
    if (rack_id==NGS2_RACK_MASTERING) {
        pthread_mutex_lock(&s->lock);
        for (Ngs2Rack *cur=s->racks; cur; cur=cur->next)
            if (cur->id==NGS2_RACK_MASTERING) ++master_index;
        pthread_mutex_unlock(&s->lock);
    }
    for (uint32_t i=0;i<voices;++i) {
        v[i].magic=NGS2_MAGIC_VOICE; v[i].rack=r; v[i].index=i; v[i].pitch=1.0f;
        v[i].channels=rack_id==NGS2_RACK_SAMPLER ? 2 : NGS2_MAX_CHANNELS;
        v[i].output_buffer=rack_id==NGS2_RACK_MASTERING ? master_index : 0;
        for (int p=0;p<NGS2_MAX_PORTS;++p) {
            v[i].port_volume[p]=1.0f;
            v[i].port_matrix[p]=-1;
            v[i].port_dest_input[p]=0;
        }
        for (int m=0;m<NGS2_MAX_MATRICES;++m) {
            v[i].matrix_levels_count[m]=0;
            for (int k=0;k<NGS2_MAX_MATRIX_LEVELS;++k) v[i].matrix_levels[m][k]=0.0f;
        }
    }
    pthread_mutex_lock(&s->lock);
    Ngs2Rack **tail=&s->racks; while (*tail) tail=&(*tail)->next; *tail=r; /* creation order */
    pthread_mutex_unlock(&s->lock);
    if (ngs2_tracing()) printf("Ngs2 trace: rack %#x created, %u voices, handle %p\n",rack_id,voices,(void *)r);
    runtime_audio_log("ngs2.rack id=%#x serial=%u voices=%u",rack_id,r->serial,voices);
    *out=r;
    return ORBIS_OK;
}
static ABI int32_t ngs2_rack_destroy(void *handle, void *buffer_info) {
    Ngs2Rack *r=as_rack(handle);
    if (!r) return NGS2_INVALID_RACK_HANDLE;
    if (buffer_info) memset(buffer_info,0,64);
    Ngs2System *s=r->system;
    pthread_mutex_lock(&s->lock);
    for (Ngs2Rack **p=&s->racks; *p; p=&(*p)->next) if (*p==r) { *p=r->next; break; }
    /* Voices of other racks patched into this one lose that route. */
    for (Ngs2Rack *o=s->racks; o; o=o->next)
        for (uint32_t i=0;i<o->voice_count;++i)
            for (int p=0;p<NGS2_MAX_PORTS;++p)
                if (o->voices[i].patch[p] && o->voices[i].patch[p]->rack==r) o->voices[i].patch[p]=NULL;
    r->magic=0;
    for (uint32_t i=0;i<r->voice_count;++i) r->voices[i].magic=0;
    pthread_mutex_unlock(&s->lock);
    /* Memory is kept: a handle the game still holds then fails the magic check instead of
     * touching freed memory. Racks are created a few times per session. */
    return ORBIS_OK;
}
static ABI int32_t ngs2_rack_voice(void *rack, uint32_t index, uintptr_t *out) {
    Ngs2Rack *r=as_rack(rack);
    if (!r) return NGS2_INVALID_RACK_HANDLE;
    if (!out) return NGS2_INVALID_OUT_ADDRESS;
    if (index>=r->voice_count) return NGS2_INVALID_VOICE_HANDLE;
    ngs2_note_callsite("RackGetVoiceHandle",r->serial,__builtin_return_address(0));
    *out=(uintptr_t)&r->voices[index];
    return ORBIS_OK;
}
/* ---- ATRAC9 sampler voices (LibAtrac9, as AJM uses) ---- */
/* A streaming voice drops the block it finished; the next queued one becomes the current block. */
static void ngs2_pop_block(Ngs2Voice *v) {
    if (!v->block_count) return;
    memmove(&v->blocks[0],&v->blocks[1],(v->block_count-1)*sizeof(Ngs2Block));
    memmove(&v->block_data[0],&v->block_data[1],(v->block_count-1)*sizeof(v->block_data[0]));
    memmove(&v->block_starts[0],&v->block_starts[1],(v->block_count-1)*sizeof(v->block_starts[0]));
    --v->block_count; v->block=0;
}
/* A streaming ATRAC9 voice plays its waveform in blocks. The game cuts a block into chunks for its ring: the first chunk of
 * a block has flags bit 1 clear, the others set; bit 0 says whether anything follows. A waveform with a loop point is a
 * block up to the end of the loop followed by the loop body with an infinite repeat count, which the game submits again and
 * again - each pass starts a superframe early and drops the lead-in with its skip. The decoder therefore has to stop a block
 * after exactly its sample count, leave the rest of its chunks, and start the next block with a fresh decoder. */
static void ngs2_stream_begin(Ngs2Voice *v) {
    v->stream_wait=0;
    v->stream_track=(v->streaming && v->wave_type==0x40 && v->block_count && v->blocks[0].samples) ? 1 : 0;
    v->stream_left=v->stream_track ? v->blocks[0].samples : 0;
}
static void ngs2_at9_restart(Ngs2Voice *v) {
    if (v->at9) { Atrac9ReleaseHandle(v->at9); v->at9=NULL; }
    v->at9_config=0;
    v->at9_count=v->at9_pos=v->at9_frames=v->at9_primed=0; v->at9_emitted=0;
    v->at9_byte=0; v->at9_sf_have=v->at9_sf_pos=0; v->at9_sf_ready=v->at9_starved=v->at9_errors=0; v->at9_phase=0;
    v->at9_decoded=v->at9_retries=0;
    v->at9_skip=v->block_count ? v->blocks[0].skip : 0;
    ngs2_stream_begin(v);
}
/* The ATRAC9 configuration: the setup's configData, else the RIFF "fmt " the data begins with. */
static uint32_t ngs2_at9_config(const Ngs2Voice *v) {
    if (v->wave_config) return v->wave_config;
    const unsigned char *d=v->wave_data;
    if (!d || memcmp(d,"RIFF",4) || memcmp(d+8,"WAVE",4)) return 0;
    for (uint32_t pos=12; pos<4096;) {
        uint32_t length; memcpy(&length,d+pos+4,4);
        if (!memcmp(d+pos,"fmt ",4) && length>=52) { uint32_t config; memcpy(&config,d+pos+8+44,4); return config; }
        if (!memcmp(d+pos,"data",4)) break;
        pos+=8+length+(length&1);
    }
    return 0;
}
static int ngs2_at9_open(Ngs2Voice *v) {
    uint32_t config=ngs2_at9_config(v);
    if (v->at9 && v->at9_config==config) return 1;
    if (v->at9) { Atrac9ReleaseHandle(v->at9); v->at9=NULL; }
    v->at9_config=0;
    if (ngs2_tracing()) {
        const unsigned char *d=v->wave_data;
        printf("Ngs2 trace: ATRAC9 voice %u config=%#x data=%p head=%02x%02x%02x%02x blocks=%u b0{off=%u size=%u rep=%u skip=%u samples=%u}\n",
               v->index,config,(const void *)d,d ? d[0] : 0,d ? d[1] : 0,d ? d[2] : 0,d ? d[3] : 0,v->block_count,v->blocks[0].offset,v->blocks[0].size,
               v->blocks[0].repeats,v->blocks[0].skip,v->blocks[0].samples);
    }
    if (!config) {
        static int reported; const unsigned char *d=v->wave_data;
        if (reported++<30 && d) runtime_audio_log("ngs2.at9_no_config rack=%#x/%u voice=%u data=%p head=%02x%02x%02x%02x",v->rack->id,v->rack->serial,v->index,(const void *)d,d[0],d[1],d[2],d[3]);
        return 0;
    }
    v->at9=Atrac9GetHandle();
    int init_rc=0;
    if (!v->at9 || (init_rc=Atrac9InitDecoder(v->at9,(unsigned char *)&config))) {
        runtime_audio_log("ngs2.at9_init_failed rack=%#x/%u voice=%u config=%#x rc=%d",v->rack->id,v->rack->serial,v->index,config,init_rc);
        if (v->at9) Atrac9ReleaseHandle(v->at9);
        v->at9=NULL; v->wave_type=0; /* undecodable: the voice stays silent */
        return 0;
    }
    v->at9_config=config;
    Atrac9GetCodecInfo(v->at9,&v->at9_info);
    if (v->at9_info.channels<1 || v->at9_info.channels>NGS2_MAX_CHANNELS || v->at9_info.frameSamples>256 ||
        v->at9_info.superframeSize<=0 || v->at9_info.superframeSize>8192 || (!v->at9_sf && !(v->at9_sf=calloc(1,8192+16384)))) { /* zeroed room after a superframe: the bit reader looks ahead */
        Atrac9ReleaseHandle(v->at9); v->at9=NULL; v->wave_type=0; return 0;
    }
    v->wave_channels=(uint32_t)v->at9_info.channels;
    return 1;
}
/* Gathers the next superframe into at9_sf. 1: ready; 0: the waveform ended; -1: a streaming voice has no more
 * data yet (the game submits blocks as it refills its ring, and a superframe may start in one block and end in
 * the next). A block that is not streaming ends after its sample count, repeats, then moves to the next block. */
static int ngs2_at9_fetch(Ngs2Voice *v) {
    const uint32_t need=(uint32_t)v->at9_info.superframeSize;
    for (int guard=0; guard<64; ++guard) {
        if (v->at9_sf_have>=need) return 1;
        if (v->block>=v->block_count) return v->streaming ? -1 : 0;
        const Ngs2Block *blk=&v->blocks[v->block];
        int spent=v->at9_byte>=blk->size || (!v->streaming && blk->samples && v->at9_emitted>=blk->samples);
        if (spent) {
            if (v->streaming) { ngs2_pop_block(v); v->at9_byte=0; continue; }
            if (v->at9_sf_have && v->at9_byte>=blk->size) { /* the last superframe is short: pad it */
                memset(v->at9_sf+v->at9_sf_have,0,need-v->at9_sf_have); v->at9_sf_have=need; return 1;
            }
            v->at9_sf_have=0; v->at9_byte=0; v->at9_emitted=0;
            if (blk->repeats==0xffffffffu || v->repeats_done<blk->repeats) { ++v->repeats_done; v->at9_skip=blk->skip; continue; }
            v->repeats_done=0;
            if (++v->block>=v->block_count) return 0;
            v->at9_skip=v->blocks[v->block].skip;
            continue;
        }
        const unsigned char *data=v->block_data[v->block];
        if (!data) return v->streaming ? -1 : 0;
        uint32_t take=need-v->at9_sf_have, avail=blk->size-v->at9_byte;
        if (take>avail) take=avail;
        memcpy(v->at9_sf+v->at9_sf_have,data+blk->offset+v->at9_byte,take);
        v->at9_sf_have+=take; v->at9_byte+=take;
    }
    return v->streaming ? -1 : 0;
}
/* Decodes the next frame; 0 at the end of the waveform or while a streaming voice waits (at9_starved). */
static int ngs2_at9_decode(Ngs2Voice *v) {
    const uint32_t superframe=(uint32_t)v->at9_info.superframeSize;
    v->at9_starved=0;
    for (int guard=0; guard<64; ++guard) {
        if (!v->at9_sf_ready) {
            int ready=ngs2_at9_fetch(v);
            if (ready<=0) { v->at9_starved=ready<0; return 0; }
            v->at9_sf_ready=1; v->at9_sf_pos=0; v->at9_frames=0;
            v->consumed_bytes+=superframe; /* copied out: the game may refill these bytes now */
        }
        int used=0;
        int decode_rc=Atrac9Decode(v->at9,v->at9_sf+v->at9_sf_pos,v->at9_pcm,&used);
        if ((decode_rc || used<=0) && v->streaming) {
            /* Streaming ring buffer refill: waiting for game to supply more data or advance buffer. */
            static int reported;
            if (++v->at9_retries==1 && reported++<40) {
                const unsigned char *sf=v->at9_sf+v->at9_sf_pos;
                runtime_audio_log("ngs2.at9_waiting_for_data rack=%#x/%u voice=%u rc=%d sf_pos=%u head=%02x%02x%02x%02x%02x%02x%02x%02x data=%p blk_off=%u blk_sz=%u at9_byte=%u",
                                  v->rack->id,v->rack->serial,v->index,decode_rc,v->at9_sf_pos,sf[0],sf[1],sf[2],sf[3],sf[4],sf[5],sf[6],sf[7],
                                  (const void *)v->block_data[v->block],v->blocks[v->block].offset,v->blocks[v->block].size,v->at9_byte);
            }
            if (v->at9_retries>60 && v->block_count) {
                /* A third of a second without a decodable superframe. Only the first chunk of a waveform block is known to start
                   on a superframe boundary: leave the chunks in front of the next block start (they count as consumed, the game
                   refills its ring from that), reset the decoder and begin again there.
                   If no block start is in the queue, do NOT discard continuation chunks (flags & 2 != 0) of a looping stream;
                   doing so flushes the game's valid refill chunks and permanently kills the stream. */
                static int resynced;
                uint32_t dropped=0;
                int has_start=0;
                for (uint32_t i=0; i<v->block_count; ++i) if (v->block_starts[i]) { has_start=1; break; }
                if (has_start) {
                    while (v->block_count && !v->block_starts[0]) { v->consumed_bytes+=v->blocks[0].size; ngs2_pop_block(v); ++dropped; }
                }
                if (resynced++<40) runtime_audio_log("ngs2.resync rack=%#x/%u voice=%u dropped_chunks=%u queued=%u",v->rack->id,v->rack->serial,v->index,dropped,v->block_count);
                v->at9_sf_have=0; v->at9_sf_pos=0; v->at9_sf_ready=0; v->at9_byte=0;
                ngs2_at9_restart(v);
                if (!v->block_count) { v->stream_wait=1; v->stream_track=1; }
                v->at9_starved=1;
                return 0;
            }
            if (!v->at9_decoded && v->at9) { Atrac9ReleaseHandle(v->at9); v->at9=NULL; }
            v->at9_byte=0; v->at9_sf_have=0; v->at9_sf_ready=0; v->at9_frames=0;
            if (!v->at9_decoded) v->consumed_bytes-=superframe;
            v->at9_starved=1;
            return 0;
        }
        if (decode_rc || used<=0) { /* a damaged superframe is dropped, a run of them ends the voice */
            static int reported;
            if (reported++<40) runtime_audio_log("ngs2.at9_decode_failed rack=%#x/%u voice=%u rc=%d used=%d sf_pos=%u block=%u/%u",
                                                 v->rack->id,v->rack->serial,v->index,decode_rc,used,v->at9_sf_pos,v->block,v->block_count);
            v->at9_sf_ready=0; v->at9_sf_have=0;
            if (++v->at9_errors>=16) return 0;
            continue;
        }
        v->at9_errors=0; ++v->at9_decoded; v->at9_retries=0;
        v->at9_sf_pos+=(uint32_t)used;
        if (++v->at9_frames>=v->at9_info.framesInSuperframe || v->at9_sf_pos>=superframe) { v->at9_sf_ready=0; v->at9_sf_have=0; }
        v->at9_count=v->at9_info.frameSamples; v->at9_pos=0;
        if (v->at9_skip) {
            uint32_t n=v->at9_skip<(uint32_t)v->at9_count ? v->at9_skip : (uint32_t)v->at9_count;
            v->at9_pos=(int)n; v->at9_skip-=n;
            if (v->at9_pos>=v->at9_count) continue;
        }
        return 1;
    }
    return 0;
}
static int ngs2_at9_open(Ngs2Voice *v);
/* The next waveform block of a stream, once the game has submitted its first chunk. */
static int ngs2_stream_resume(Ngs2Voice *v) {
    if (!v->stream_wait) return 1;
    while (v->block_count && !v->block_starts[0]) {
        /* Discard leftover continuation chunks from previous stream pass */
        v->consumed_bytes += v->blocks[0].size;
        ngs2_pop_block(v);
    }
    if (!v->block_count) { v->at9_starved=1; return 0; }
    v->stream_left=v->blocks[0].samples; v->at9_skip=v->blocks[0].skip; v->stream_wait=0;
    if (!v->stream_left) v->stream_track=0;
    return 1;
}
/* The current block has played its samples: drop what is left of its chunks (they count as consumed, the game refills its
 * ring from that), reset the decoder, and begin the next block. */
static void ngs2_stream_advance(Ngs2Voice *v) {
    static int reported;
    uint32_t dropped=0;
    if (v->block_count) {
        const Ngs2Block *head=&v->blocks[0];
        if (v->at9_byte<head->size) v->consumed_bytes+=head->size-v->at9_byte;
        ngs2_pop_block(v);
    }
    while (v->block_count && !v->block_starts[0]) { v->consumed_bytes+=v->blocks[0].size; ngs2_pop_block(v); ++dropped; }
    if (v->at9) { Atrac9ReleaseHandle(v->at9); v->at9=NULL; }
    v->at9_config=0;
    v->at9_byte=0; v->at9_sf_have=v->at9_sf_pos=0; v->at9_sf_ready=0; v->at9_frames=0; v->at9_count=v->at9_pos=0;
    v->at9_decoded=v->at9_retries=v->at9_errors=0; v->at9_skip=0;
    v->stream_wait=1;
    ngs2_stream_resume(v);
    if (reported++<60)
        runtime_audio_log("ngs2.block_end rack=%#x/%u voice=%u played_ms=%.0f dropped_chunks=%u queued=%u next_samples=%u next_skip=%u final=%d",
                          v->rack->id,v->rack->serial,v->index,ngs2_played_ms(v),dropped,v->block_count,
                          v->block_count ? v->blocks[0].samples : 0,v->block_count ? v->blocks[0].skip : 0,v->stream_final);
}
/* The next decoded sample frame into frame[]; 0 when the waveform ends or the stream is dry. */
static int ngs2_at9_next(Ngs2Voice *v, float *frame) {
    if (v->streaming && v->stream_track) {
        if (v->stream_wait && !ngs2_stream_resume(v)) return 0;
        if (!v->stream_wait && v->stream_left==0) { ngs2_stream_advance(v); if (v->stream_wait) return 0; }
        if (!v->at9 && !ngs2_at9_open(v)) return 0;
    }
    if (!v->streaming && v->block<v->block_count) {
        const Ngs2Block *limit=&v->blocks[v->block];
        if (limit->samples && v->at9_emitted>=limit->samples) v->at9_pos=v->at9_count; /* drop the rest of the last superframe */
    }
    if (v->at9_pos>=v->at9_count && !ngs2_at9_decode(v)) return 0;
    for (uint32_t c=0;c<v->wave_channels;++c)
        frame[c]=(float)v->at9_pcm[(size_t)v->at9_pos*v->wave_channels+c]/32768.0f;
    ++v->at9_pos; ++v->at9_emitted;
    if (v->streaming && v->stream_track && v->stream_left) --v->stream_left;
    v->consumed+=1.0;
    return 1;
}
static void ngs2_run_at9(Ngs2Voice *v, uint32_t frames, double step) {
    if (!ngs2_at9_open(v)) return;
    while (v->at9_primed<2) { /* the resampler needs the frame before and the frame after */
        if (!ngs2_at9_next(v,v->at9_primed ? v->at9_cur : v->at9_prev)) {
            if (v->at9_starved || v->streaming) return; /* waiting for the game to refill its ring: silence meanwhile */
            if (ngs2_tracing()) printf("Ngs2 trace: ATRAC9 voice %u produced no audio\n",v->index);
            runtime_audio_log("ngs2.at9_no_audio rack=%#x/%u voice=%u played_ms=%.0f",v->rack->id,v->rack->serial,v->index,ngs2_played_ms(v));
            v->playing=0; return;
        }
        if (++v->at9_primed==2) v->at9_phase=0;
    }
    for (uint32_t f=0; f<frames; ++f) {
        while (v->at9_phase>=1.0) {
            memcpy(v->at9_prev,v->at9_cur,sizeof v->at9_prev);
            v->at9_phase-=1.0;
            if (!ngs2_at9_next(v,v->at9_cur)) {
                if (v->at9_starved || v->streaming) { v->at9_primed=1; return; }
                v->playing=0; return;
            }
        }
        float t=(float)v->at9_phase;
        for (uint32_t c=0;c<v->wave_channels;++c) v->out[c][f]=v->at9_prev[c]*(1.0f-t)+v->at9_cur[c]*t;
        v->at9_phase+=step;
    }
}
/* Voice events as reverse engineered in shadPS4 PR #3891: 0 resets the voice and starts it
 * (state 0x20000101), 1 pauses, 2 stops, 3 kills, 4 and 5 resume. */
#define NGS2_EVENT_RESET 0
#define NGS2_EVENT_PAUSE 1
#define NGS2_EVENT_STOP 2
#define NGS2_EVENT_KILL 3
#define NGS2_EVENT_RESUME_A 4
#define NGS2_EVENT_RESUME_B 5
static int ngs2_event1_pauses(void) { static int on=-1; if (on<0) on=getenv("SPARTAN_NGS2_EVENT1_PAUSES")!=NULL; return on; }
/* SPARTAN_NGS2_AUTOPLAY_BLOCKS=1 brings back the old behaviour (replacing the blocks starts the voice), for A/B listening. */
static int ngs2_autoplay_blocks(void) { static int on=-1; if (on<0) on=getenv("SPARTAN_NGS2_AUTOPLAY_BLOCKS")!=NULL; return on; }
/* SPARTAN_NGS2_PLAY_RESTARTS=1 brings back the old behaviour (every Play restarts), for A/B listening. */
static int ngs2_play_restarts(void) { static int on=-1; if (on<0) on=getenv("SPARTAN_NGS2_PLAY_RESTARTS")!=NULL; return on; }
static void ngs2_event(Ngs2Voice *v, uint32_t event) {
    if (v->rack->id==NGS2_RACK_SAMPLER && (v->streaming || v->block_count>1 || ngs2_total_bytes(v)>=24576u)) { /* every event on a long voice, as the game sent it */
        static int seen;
        if (seen++<1500) runtime_audio_log("ngs2.event id=%u rack=%#x/%u voice=%u before: playing=%d paused=%d fresh=%d played_ms=%.0f queued=%u streaming=%d",
                                           event,v->rack->id,v->rack->serial,v->index,v->playing,v->paused,v->fresh,ngs2_played_ms(v),v->block_count,v->streaming);
    }
    if (event==NGS2_EVENT_RESET && v->rack->id==NGS2_RACK_SAMPLER && v->playing && !v->paused && !v->fresh && v->block_count>0 &&
        !ngs2_play_restarts()) {
        /* The game sends Play again while the voice is already playing its stream (seen right after a Resume, and
           600 ms into a dialogue line). Restarting the decoder at byte 0 of a ring that has been refilled since
           produced garbage or a repeated word. A Play after new blocks (fresh) is honoured. */
        static int reported;
        if (reported++<400) runtime_audio_log("ngs2.play_ignored rack=%#x/%u voice=%u played_ms=%.0f queued=%u streaming=%d",
                                              v->rack->id,v->rack->serial,v->index,ngs2_played_ms(v),v->block_count,v->streaming);
        return;
    }
    switch (event) {
    case NGS2_EVENT_RESET: v->fresh=0; v->playing=1; v->paused=0; v->log_pending=1; v->block=0; v->repeats_done=0; v->position=0; v->consumed=0; v->consumed_bytes=0; ngs2_at9_restart(v); break;
    case NGS2_EVENT_STOP: case NGS2_EVENT_KILL: v->playing=0; v->paused=0; break;
    case NGS2_EVENT_PAUSE:
        /* Event 1 on a sampler voice: the game sends it twice to a voice it is finished with or interrupts (a dialogue line when an
           event cutscene starts). Treated as a pause the voice stayed playing=1, GetStateFlags kept answering 1, and the game waited
           for it forever (black screen, 590 flag polls a second). It now stops the voice; a later Resume still continues from the
           same position. SPARTAN_NGS2_EVENT1_PAUSES=1 brings the old pause back. */
        if (v->rack->id==NGS2_RACK_SAMPLER && !ngs2_event1_pauses()) { v->playing=0; v->paused=0; }
        else v->paused=1;
        break;
    case NGS2_EVENT_RESUME_A: case NGS2_EVENT_RESUME_B: v->paused=0; v->playing=1; break;
    default: break;
    }
    if ((event==NGS2_EVENT_STOP || event==NGS2_EVENT_KILL) && v->wav.f) ngs2_wav_close(&v->wav);
    if (event!=NGS2_EVENT_RESET && v->log_major) {
        static const char *const names[]={"reset","event1","stop","kill","resume","resume"};
        runtime_audio_log("ngs2.%s rack=%#x/%u voice=%u played_ms=%.0f",event<6 ? names[event] : "event",v->rack->id,v->rack->serial,v->index,ngs2_played_ms(v));
    }
}
static void ngs2_param(Ngs2Voice *v, uint32_t id, const unsigned char *p, uint16_t size) {
    const unsigned char *b=p+8; /* after OrbisNgs2VoiceParamHeader */
    uint32_t u0=0, u1=0; float f1=0;
    if (size>=12) memcpy(&u0,b,4);
    if (size>=16) { memcpy(&u1,b+4,4); memcpy(&f1,b+4,4); }
    switch (id) {
    case 0x01: { /* matrix levels: matrixId, numLevels, aLevel[] */
        /* OrbisNgs2VoiceMatrixLevelsParam: matrixId, numLevels, pLevels */
        if (size >= 16) {
            uint32_t matrix_id = u0;
            uint32_t num_levels = u1;
            const float *levels = NULL;
            if (size >= 24) {
                uintptr_t ptr = 0;
                memcpy(&ptr, b + 8, 8);
                levels = (const float *)ptr;
            }
            if (matrix_id < NGS2_MAX_MATRICES && levels && num_levels <= NGS2_MAX_MATRIX_LEVELS) {
                v->matrix_levels_count[matrix_id] = num_levels;
                for (uint32_t k = 0; k < num_levels; ++k) {
                    float lvl = levels[k];
                    if (lvl < -4.0f) lvl = -4.0f;
                    else if (lvl > 4.0f) lvl = 4.0f;
                    v->matrix_levels[matrix_id][k] = lvl;
                }
            }
        }
        break;
    }
    case 0x02: /* port volume: port, level */
        if (size >= 16 && u0 < NGS2_MAX_PORTS) {
            float lvl = f1;
            if (lvl < -4.0f) lvl = -4.0f;
            else if (lvl > 4.0f) lvl = 4.0f;
            v->port_volume[u0] = lvl;
        }
        break;
    case 0x03: /* port matrix: port, matrixId */
        if (size >= 16 && u0 < NGS2_MAX_PORTS) {
            v->port_matrix[u0] = (u1 == 0xffffffffu || u1 >= NGS2_MAX_MATRICES) ? -1 : (int32_t)u1;
        }
        break;
    case 0x05: /* patch: port, destInputId, destHandle */
        if (size >= 24 && u0 < NGS2_MAX_PORTS) {
            uintptr_t dest = 0;
            memcpy(&dest, b + 8, 8);
            v->port_dest_input[u0] = u1;
            v->patch[u0] = as_voice(dest);
        }
        break;
    case 0x06: /* event */
        if (size>=12) ngs2_event(v,u0);
        break;
    case 0x10000000: /* sampler setup: format {type, channels, rate, config, ...}, flags */
        if (size>=20) {
            v->wave_type=u0; v->wave_channels=u1; memcpy(&v->wave_rate,b+8,4);
            if (size>=24) memcpy(&v->wave_config,b+12,4);
            if (!v->wave_channels || v->wave_channels>NGS2_MAX_CHANNELS) v->wave_channels=1;
            v->channels=v->wave_channels;
            v->block_count=0;
            ngs2_at9_restart(v);
        }
        break;
    case 0x10000001: /* waveform blocks: data, flags, numBlocks, aBlock */
        if (size>=32) {
            const unsigned char *data; const Ngs2Block *blocks; uint32_t count, flags;
            memcpy(&data,b,8); memcpy(&flags,b+8,4); memcpy(&count,b+12,4); memcpy(&blocks,b+16,8);
            /* A streaming voice continues what is playing if it is actively playing and not paused.
               flags: bit 0 = streaming buffer active, bit 1 = stream segment end / continuation block.
               If switching streams, paused or stopped, blocks replace the waveform and unpause. */
            int append=((flags&3)!=0) && v->streaming && v->playing && !v->paused && blocks;
            uint32_t room=NGS2_MAX_BLOCKS-(append ? v->block_count : 0);
            uint32_t take=blocks ? (count<room ? count : room) : 0;
            if (take<count) runtime_audio_log("ngs2.blocks_truncated rack=%#x/%u voice=%u count=%u kept=%u append=%d",v->rack->id,v->rack->serial,v->index,count,take,append);
            if (!append) {
                v->wave_data=data;
                v->block_count=0;
                v->stream_final=0;
                v->streaming=(flags&3)!=0;
                v->block=0;
                v->repeats_done=0;
                v->position=0;
                v->consumed=0;
                v->consumed_bytes=0;
                /* New blocks do not start the voice. The game submits them, keeps refilling for about two seconds, and
                   starts the stream itself with events 5 and 0 in one call (seen in the event log). Starting here played
                   that preload, the refills overwrote the start of the ring, and the real Play then restarted on garbage. */
                if (ngs2_autoplay_blocks()) { v->paused=0; v->playing=1; }
                v->stream_total=(v->streaming && v->wave_type==0x40 && take && blocks[0].samples) ? blocks[0].samples : 0;
                v->log_pending=1;
                v->fresh=1;
                ngs2_at9_restart(v);
            }
            const int was_empty=v->block_count==0;
            const int was_final=v->stream_final;
            for (uint32_t k=0;k<take;++k) {
                v->blocks[v->block_count]=blocks[k]; v->block_data[v->block_count]=data;
                v->block_starts[v->block_count]=(flags&2)==0; ++v->block_count;
            }
            if (take && append) v->at9_retries=0;
            if (take && v->streaming) v->stream_final=(flags&1)==0;
            if (!append) ngs2_stream_begin(v);
            else if (was_empty && take && v->wave_type==0x40 && v->block_starts[0] && (!v->stream_wait || was_final)) {
                /* The voice is playing and streaming from its previous track (Play came first, or was ignored) and a chunk that
                   begins a waveform block arrives with nothing queued: that is a new stream, not the next block of the old one
                   (that case is stream_wait without a final chunk). The decoder still holds a piece of the old stream's superframe and
                   its position; every later superframe then decoded shifted ('ATRAC9' of mixed bytes), the music stayed
                   silent and the chunks were thrown away one by one. Start clean at this chunk. */
                ngs2_at9_restart(v);
                static int reported;
                if (reported++<40) runtime_audio_log("ngs2.stream_begin_late rack=%#x/%u voice=%u samples=%u skip=%u was_final=%d wait=%d",v->rack->id,v->rack->serial,v->index,
                                                     v->blocks[0].samples,v->blocks[0].skip,was_final,v->stream_wait);
            }
            if (v->streaming || ngs2_is_major(v)) { /* how a streaming game feeds its ring */
                static int reported;
                if (reported++<500) {
                    char list[360]=""; int n=0;
                    for (uint32_t k=0;k<count && k<4 && blocks && n>=0 && (size_t)n<sizeof list;++k)
                        n+=snprintf(list+n,sizeof list-(size_t)n," b%u{off=%u size=%u rep=%u skip=%u samples=%u user=%#llx}",k,blocks[k].offset,
                                    blocks[k].size,blocks[k].repeats,blocks[k].skip,blocks[k].samples,(unsigned long long)blocks[k].user);
                    runtime_audio_log("ngs2.blocks rack=%#x/%u voice=%u append=%d playing=%d played_ms=%.0f queued=%u data=%p flags=%#x count=%u%s",v->rack->id,v->rack->serial,v->index,
                                      append,v->playing,ngs2_played_ms(v),v->block_count,(const void *)data,flags,count,list);
                }
            }
        }
        break;
    case 0x10000005: { /* pitch: ratio */
        float ratio; memcpy(&ratio,b,4);
        if (ratio>0.0f && ratio<16.0f) v->pitch=ratio;
        break;
    }
    case 0x20000000: case 0x20010000: case 0x30000000: /* submixer / mastering setup: numIoChannels, flags */
        if (u0 && u0<=NGS2_MAX_CHANNELS) v->channels=u0;
        break;
    case 0x20010001: { /* submixer matrix coefficients / volume levels */
        /* Sony Ngs2 Submixer Matrix (0x1b0b0 / 0x4df0): pairs of (in_ch, out_ch, gain) */
        if (size >= 24) {
            uint32_t n1 = u0, n2 = u1;
            uint32_t total = n1 + n2;
            uintptr_t ptr = 0;
            memcpy(&ptr, b + 8, 8);
            const unsigned char *desc = (const unsigned char *)ptr;
            if (desc && total <= 64) {
                for (uint32_t k = 0; k < total; ++k) {
                    uint32_t in_ch = 0, out_ch = 0; float gain = 1.0f;
                    memcpy(&in_ch, desc + k * 12, 4);
                    memcpy(&out_ch, desc + k * 12 + 4, 4);
                    memcpy(&gain, desc + k * 12 + 8, 4);
                    if (gain < -4.0f) gain = -4.0f;
                    else if (gain > 4.0f) gain = 4.0f;
                    if (in_ch < NGS2_MAX_CHANNELS && out_ch < NGS2_MAX_CHANNELS) {
                        uint32_t idx = out_ch * NGS2_MAX_CHANNELS + in_ch;
                        if (idx < NGS2_MAX_MATRIX_LEVELS) {
                            v->matrix_levels[0][idx] = gain;
                            v->matrix_levels_count[0] = NGS2_MAX_MATRIX_LEVELS;
                        }
                    }
                }
            }
        }
        break;
    }
    case 0x10000008: /* sampler user FX: handler, userData0..2 */
    case 0x20000004: /* submixer user FX: handler, userData0..2 */
        if (size>=40) { memcpy(&v->fx,b,8); memcpy(v->fx_data,b+8,24); }
        break;
    case 0x30000005: /* mastering output: render buffer index */
        v->output_buffer=u0;
        break;
    default: { /* matrices, envelope, filters, effects: accepted, not applied; each id is logged once */
        static uint32_t seen[64]; static int seen_count; int known=0;
        for (int k=0;k<seen_count;++k) if (seen[k]==id) known=1;
        if (!known && seen_count<64) {
            seen[seen_count++]=id;
            runtime_audio_log("ngs2.param_not_applied id=%#x rack=%#x/%u voice=%u size=%u first=%#x second=%#x",id,v->rack->id,v->rack->serial,v->index,size,u0,u1);
        }
        break;
    }
    }
}
static ABI int32_t ngs2_voice_control(uintptr_t handle, const unsigned char *params) {
    Ngs2Voice *v=as_voice(handle);
    if (!v) return NGS2_INVALID_VOICE_HANDLE;
    Ngs2System *s=v->rack->system;
    void *caller=__builtin_return_address(0);
    pthread_mutex_lock(&s->lock);
    static int traced;
    for (const unsigned char *p=params; p;) {
        uint16_t size; int16_t next; uint32_t id;
        memcpy(&size,p,2); memcpy(&next,p+2,2); memcpy(&id,p+4,4);
        if (ngs2_tracing() && traced<3000 && id!=2) {
            ++traced;
            uint32_t first=0; if (size>=12) memcpy(&first,p+8,4);
            printf("Ngs2 trace: rack %#x/%u voice %u param id=%#x size=%u first=%#x\n",v->rack->id,v->rack->serial,v->index,id,size,first);
        }
        { uint32_t first_word=0; if (size>=12) memcpy(&first_word,p+8,4);
          ngs2_note_callsite("VoiceControl",id==0x06 ? (0x60000000u|first_word) : id,caller); }
        if (size>=8) ngs2_param(v,id,p,size);
        if (next<=0) break;
        p+=next;
    }
    pthread_mutex_unlock(&s->lock);
    return ORBIS_OK;
}
/* GetStateFlags reports the low byte of the state: 0x01 while the voice plays (paused too). */
static uint32_t ngs2_state_flags(const Ngs2Voice *v) {
    if (!v->playing) return 0u;
    uint32_t flags = 0x20000101u;
    if (v->paused) flags |= 0x00000200u;
    return flags;
}
static ABI int32_t ngs2_voice_state(uintptr_t handle, unsigned char *state, uint64_t size) {
    Ngs2Voice *v=as_voice(handle);
    static int traced_state; if (v && ngs2_tracing() && traced_state<20) { ++traced_state; printf("Ngs2 trace: GetState rack %#x/%u voice %u size=%llu playing=%d\n",v->rack->id,v->rack->serial,v->index,(unsigned long long)size,v->playing); }
    if (!v) return NGS2_INVALID_VOICE_HANDLE;
    ++v->state_calls;
    ngs2_note_callsite("GetState",v->playing ? 1u : 0u,__builtin_return_address(0));
    if (v->streaming && (v->state_calls<=3 || v->state_calls%60==0)) {
        static int reported;
        if (reported++<600) runtime_audio_log("ngs2.state voice=%u call=%u playing=%d consumed=%.0f bytes=%llu block=%u/%u",v->index,v->state_calls,v->playing,
                                              v->consumed,(unsigned long long)v->consumed_bytes,v->block,v->block_count);
    }
    if (!state || size<4) return NGS2_INVALID_OUT_ADDRESS;
    if (size<=256) memset(state,0,size);
    uint32_t flags=ngs2_state_flags(v);
    memcpy(state,&flags,4);
    /* OrbisNgs2SamplerVoiceState (48 bytes): flags, envelopeHeight, peakHeight, reserved,
     * numDecodedSamples @16, decodedDataSize @24, userData @32, waveformData @40. A game streaming
     * into a ring reads these to know how far playback has gone. */
    if (v->rack->id==NGS2_RACK_SAMPLER && size>=48) {
        float envelope=1.0f; memcpy(state+4,&envelope,4);
        uint64_t samples=(uint64_t)v->consumed; memcpy(state+16,&samples,8);
        memcpy(state+24,&v->consumed_bytes,8);
        if (v->block_count) {
            const Ngs2Block *blk=&v->blocks[v->block<v->block_count ? v->block : 0];
            memcpy(state+32,&blk->user,8);
            uint32_t bytes=v->wave_type==0x12 ? 2u : v->wave_type==0x13 ? 4u : 0u;
            const unsigned char *base=v->block_data[v->block<v->block_count ? v->block : 0];
            const unsigned char *at=base ? base+blk->offset : NULL;
            if (at && bytes) at+=(size_t)v->position*v->wave_channels*bytes;
            else if (at) at+=v->at9_byte;
            memcpy(state+40,&at,8);
        }
    }
    return ORBIS_OK;
}
static ABI int32_t ngs2_voice_flags(uintptr_t handle, uint32_t *flags) {
    Ngs2Voice *v=as_voice(handle);
    if (!v) return NGS2_INVALID_VOICE_HANDLE;
    if (!flags) return NGS2_INVALID_OUT_ADDRESS;
    *flags=ngs2_state_flags(v) & 0xffu;
    ++v->flag_calls;
    ngs2_note_callsite("GetStateFlags",*flags,__builtin_return_address(0));
    return ORBIS_OK;
}
/* One sample of channel c at frame f of a block whose data starts at base (PCM int16 0x12 or float 0x13). */
static float ngs2_sample(const Ngs2Voice *v, const unsigned char *base, uint32_t frame, uint32_t c) {
    if (v->wave_type==0x12) { int16_t s; memcpy(&s,base+((size_t)frame*v->wave_channels+c)*2,2); return (float)s/32768.0f; }
    if (v->wave_type==0x13) { float s; memcpy(&s,base+((size_t)frame*v->wave_channels+c)*4,4); return s; }
    return 0.0f;
}
static uint32_t ngs2_block_frames(const Ngs2Voice *v, const Ngs2Block *blk) {
    uint32_t bytes=v->wave_type==0x12 ? 2 : v->wave_type==0x13 ? 4 : 0;
    if (!bytes) return 0;
    uint32_t frames=blk->size/(bytes*v->wave_channels);
    return blk->samples && blk->samples<frames ? blk->samples : frames;
}
static int ngs2_atrac9_seen;
static void ngs2_run_sampler(Ngs2Voice *v, uint32_t frames, uint32_t system_rate) {
    if (!v->block_count) return;
    double step=(double)v->pitch*(v->wave_rate ? (double)v->wave_rate/system_rate : 1.0);
    if (v->wave_type==0x40) { ngs2_run_at9(v,frames,step); return; }
    if (v->wave_type!=0x12 && v->wave_type!=0x13) {
        if (runtime_audio_log_enabled()) {
            static uint32_t seen[8]; static int seen_count; int known=0;
            for (int k=0;k<seen_count;++k) if (seen[k]==v->wave_type) known=1;
            if (!known && seen_count<8) {
                char what[400]; seen[seen_count++]=v->wave_type; ngs2_describe(v,what,sizeof what);
                runtime_audio_log("ngs2.unsupported_waveform rack=%#x/%u voice=%u %s",v->rack->id,v->rack->serial,v->index,what);
            }
        }
        note_once(&ngs2_atrac9_seen,"Ngs2: a sampler waveform type is not supported (silent)");
        return;
    }
    for (uint32_t f=0; f<frames && v->playing; ++f) {
        const Ngs2Block *blk=&v->blocks[v->block];
        uint32_t length=ngs2_block_frames(v,blk);
        while (length && v->position>=length) {
            v->position-=length;
            if (!v->streaming && (blk->repeats==0xffffffffu || v->repeats_done<blk->repeats)) { ++v->repeats_done; continue; }
            v->repeats_done=0;
            if (v->streaming) {                        /* the next queued block, or silence until the game sends one */
                ngs2_pop_block(v);
                if (!v->block_count) { length=0; v->position=0; break; }
            } else if (++v->block>=v->block_count) { v->playing=0; break; }
            blk=&v->blocks[v->block];
            length=ngs2_block_frames(v,blk);
        }
        if (!v->playing) break;
        if (!length) { if (!v->streaming) v->playing=0; break; }
        const unsigned char *base=v->block_data[v->block];
        if (!base) break;
        base+=blk->offset;
        uint32_t i0=(uint32_t)v->position, i1=i0+1<length ? i0+1 : i0;
        float t=(float)(v->position-i0);
        for (uint32_t c=0;c<v->wave_channels;++c)
            v->out[c][f]=ngs2_sample(v,base,i0,c)*(1.0f-t)+ngs2_sample(v,base,i1,c)*t;
        v->position+=step;
        v->consumed+=step;
    }
    v->consumed_bytes=(uint64_t)v->consumed*v->wave_channels*(v->wave_type==0x12 ? 2u : 4u);
}
typedef int32_t (ABI *Ngs2UserFx)(void *context);
typedef struct { float **channels; uintptr_t data[3]; uint32_t flags, channel_count, grain, rate; } Ngs2UserFxContext;
static void ngs2_run_voice(Ngs2Voice *v, uint32_t frames, uint32_t rate) {
    for (uint32_t c=0;c<NGS2_MAX_CHANNELS;++c) memset(v->out[c],0,frames*sizeof(float));
    if (v->rack->id==NGS2_RACK_SAMPLER) ngs2_run_sampler(v,frames,rate);
    else for (uint32_t c=0;c<v->channels;++c) memcpy(v->out[c],v->in[c],frames*sizeof(float));
    if (v->fx) {
        float *channels[NGS2_MAX_CHANNELS];
        for (uint32_t c=0;c<NGS2_MAX_CHANNELS;++c) channels[c]=v->out[c];
        Ngs2UserFxContext context={channels,{v->fx_data[0],v->fx_data[1],v->fx_data[2]},0,v->channels,frames,rate};
        ((Ngs2UserFx)v->fx)(&context);
    }
    if (ngs2_measure)
        for (uint32_t c=0;c<v->channels;++c) for (uint32_t f=0;f<frames;++f) if (fabsf(v->out[c][f])>v->peak_seen) v->peak_seen=fabsf(v->out[c][f]);
}
static void ngs2_send(Ngs2Voice *v, uint32_t frames) {
    for (int p=0;p<NGS2_MAX_PORTS;++p) {
        Ngs2Voice *d=v->patch[p];
        if (!d || !d->playing) continue;

        /* Port 1 on sampler voices (rack 0x1000) targets the wet/reverb send rack (0x2001).
           In clean-room HLE without dedicated DSP reverb, passing dry signal at full gain (1.0)
           into 0x2001 sums duplicate phase-aligned dialogue back into master, causing harsh echo/flanging.
           Mute or heavily attenuate wet send to keep dialogue clean and single-voiced. */
        if (v->rack && v->rack->id==NGS2_RACK_SAMPLER && d->rack && d->rack->id==0x2001) continue;

        float level=v->port_volume[p];
        if (fabsf(level) < 1e-6f) continue;

        int mat=v->port_matrix[p];
        uint32_t mat_count=(mat>=0 && mat<NGS2_MAX_MATRICES) ? v->matrix_levels_count[mat] : 0;

        if (mat>=0 && mat<NGS2_MAX_MATRICES && mat_count>0) {
            const float *mlevels=v->matrix_levels[mat];
            uint32_t src_channels=v->channels ? v->channels : 1;
            uint32_t dst_channels=d->channels ? d->channels : 1;
            if (src_channels>NGS2_MAX_CHANNELS) src_channels=NGS2_MAX_CHANNELS;
            if (dst_channels>NGS2_MAX_CHANNELS) dst_channels=NGS2_MAX_CHANNELS;

            for (uint32_t dc=0; dc<dst_channels; ++dc) {
                for (uint32_t sc=0; sc<src_channels; ++sc) {
                    uint32_t idx=dc*src_channels + sc;
                    if (idx>=mat_count) break;
                    float gain=mlevels[idx]*level;
                    if (fabsf(gain)<1e-6f) continue;
                    for (uint32_t f=0; f<frames; ++f)
                        d->in[dc][f]+=v->out[sc][f]*gain;
                }
            }
        } else {
            uint32_t channels=v->channels<d->channels ? v->channels : d->channels;
            if (v->channels==1 && d->channels>=2) {
                /* A mono source feeding stereo/surround destination:
                   Apply spatial pan bias if this is a twin stereo voice pair */
                float gl = 1.0f, gr = 1.0f;
                if (v->spatial_pan_mode == 1) { gl = 1.0f; gr = 0.05f; } /* Left bias */
                else if (v->spatial_pan_mode == 2) { gl = 0.05f; gr = 1.0f; } /* Right bias */

                for (uint32_t f=0;f<frames;++f) {
                    float s = v->out[0][f] * level;
                    d->in[0][f] += s * gl;
                    d->in[1][f] += s * gr;
                }
            } else {
                for (uint32_t c=0;c<channels;++c)
                    for (uint32_t f=0;f<frames;++f) d->in[c][f]+=v->out[c][f]*level;
            }
        }
    }
}
static ABI int32_t ngs2_render(void *system, const Ngs2RenderBuffer *buffers, uint32_t count) {
    Ngs2System *s=as_system(system);
    if (!s) return NGS2_INVALID_SYSTEM_HANDLE;
    ngs2_buffer_count=count;
    ngs2_note_callsite("SystemRender",count,__builtin_return_address(0));
    uint32_t frames=s->grain;
    if (buffers && count && buffers[0].channels)
        frames=(uint32_t)(buffers[0].size/((uint64_t)buffers[0].channels*sizeof(float)));
    if (!frames || frames>NGS2_MAX_GRAIN) frames=s->grain;
    pthread_mutex_lock(&s->lock);
    /* Depth of each playing voice in the patch graph: sources before the voices they feed. */
    int max_level=0;
    for (Ngs2Rack *r=s->racks; r; r=r->next)
        for (uint32_t i=0;i<r->voice_count;++i) {
            Ngs2Voice *v=&r->voices[i];
            v->level=0; v->done=0;
            if (v->playing) for (uint32_t c=0;c<NGS2_MAX_CHANNELS;++c) memset(v->in[c],0,frames*sizeof(float));
        }
    for (int pass=0, changed=1; changed && pass<16; ++pass) {
        changed=0;
        for (Ngs2Rack *r=s->racks; r; r=r->next)
            for (uint32_t i=0;i<r->voice_count;++i) {
                Ngs2Voice *v=&r->voices[i];
                if (!v->playing) continue;
                for (int p=0;p<NGS2_MAX_PORTS;++p)
                    if (v->patch[p] && v->patch[p]->level<=v->level) {
                        v->patch[p]->level=v->level+1; changed=1;
                        if (v->level+1>max_level) max_level=v->level+1;
                    }
            }
    }
    for (int level=0; level<=max_level; ++level)
        for (Ngs2Rack *r=s->racks; r; r=r->next)
            for (uint32_t i=0;i<r->voice_count;++i) {
                Ngs2Voice *v=&r->voices[i];
                if (!v->playing || v->paused || v->level!=level) continue;
                if (v->log_pending) ngs2_log_start(v);
                int was_playing=v->playing;
                ngs2_run_voice(v,frames,s->rate);
                if (v->wav.f) ngs2_wav_write_planar(&v->wav,v->out,frames);
                if (v->rack->id==NGS2_RACK_SAMPLER && v->log_major) { /* a streaming voice running dry, and recovering */
                    int starved_now=v->streaming && (v->block_count==0 || v->at9_starved);
                    if (starved_now!=v->log_starved) {
                        v->log_starved=starved_now;
                        runtime_audio_log(starved_now ? "ngs2.starved rack=%#x/%u voice=%u played_ms=%.0f queued=%u" : "ngs2.refilled rack=%#x/%u voice=%u played_ms=%.0f queued=%u",
                                          v->rack->id,v->rack->serial,v->index,ngs2_played_ms(v),v->block_count);
                    }
                }
                ngs2_send(v,frames);
                if (was_playing && !v->playing && v->log_major) ngs2_log_end(v);
                if (v->playing && v->streaming && v->wave_type==0x40 && v->stream_final && v->block_count==0 &&
                    (v->stream_wait || (v->at9_starved && v->at9_pos>=v->at9_count && !v->at9_sf_ready))) {
                    /* The last chunk the game submitted (flags bit 0 clear) has been played out and nothing follows: the voice is
                       finished. A voice that stays 'playing' here is waited on forever (black screen, a thread spinning on the
                       state flags). A looping waveform never gets here: the game keeps submitting its loop block. */
                    v->playing=0; v->paused=0;
                    runtime_audio_log("ngs2.end rack=%#x/%u voice=%u reason=stream_complete played_ms=%.0f total_samples=%u",
                                      v->rack->id,v->rack->serial,v->index,ngs2_played_ms(v),v->stream_total);
                    ngs2_wav_close(&v->wav);
                    v->log_major=0; v->stream_total=0;
                }
            }
    /* Mastering voices write their render buffer: 32-bit float, interleaved. */
    for (uint32_t b=0; buffers && b<count; ++b)
        if (buffers[b].buffer && buffers[b].size) memset(buffers[b].buffer,0,buffers[b].size);
    for (Ngs2Rack *r=s->racks; r; r=r->next) {
        if (r->id!=NGS2_RACK_MASTERING) continue;
        for (uint32_t i=0;i<r->voice_count;++i) {
            Ngs2Voice *v=&r->voices[i];
            if (!v->playing || v->output_buffer>=count) continue;
            const Ngs2RenderBuffer *out=&buffers[v->output_buffer];
            if (!out->buffer || !out->channels) continue;
            uint32_t channels=out->channels<v->channels ? out->channels : v->channels;
            uint64_t room=out->size/((uint64_t)out->channels*sizeof(float));
            float *dst=out->buffer;
            for (uint32_t f=0; f<frames && f<room; ++f)
                for (uint32_t c=0;c<channels;++c) {
                    float sample = v->out[c][f];
                    /* Soft limit master buffer write if multiple mastering sources sum or exceed 1.0 */
                    dst[(size_t)f*out->channels+c] += sample;
                }
        }
    }
    ngs2_wav_masters(buffers,count,frames);
    pthread_mutex_unlock(&s->lock);
    if (ngs2_measure && runtime_audio_log_enabled()) ngs2_log_summary(s);
    /* SPARTAN_NGS2_TRACE: every ~4 s, the playing voices, their routes and output peaks. */
    static unsigned renders;
    if (ngs2_tracing() && ++renders%750==1) {
        pthread_mutex_lock(&s->lock);
        printf("Ngs2 trace: graph of system %p after %u renders (%u frames, %u buffers)\n",(void *)s,renders,frames,count);
        for (Ngs2Rack *r=s->racks; r; r=r->next)
            for (uint32_t i=0;i<r->voice_count;++i) {
                Ngs2Voice *v=&r->voices[i];
                if (!v->playing) continue;
                float peak=v->peak_seen; v->peak_seen=0;
                char routes[160]=""; int n=0;
                for (int p=0;p<NGS2_MAX_PORTS;++p) if (v->patch[p])
                    n+=snprintf(routes+n,sizeof routes-(size_t)n," p%d->%#x/%u@%.2f",p,v->patch[p]->rack->id,v->patch[p]->index,v->port_volume[p]);
                printf("Ngs2 trace:   %#x/%u ch=%u lvl=%d fx=%d type=%#x out=%u peak=%.4f%s\n",r->id,i,v->channels,v->level,
                       v->fx!=0,v->wave_type,v->output_buffer,peak,routes);
            }
        pthread_mutex_unlock(&s->lock);
    }
    return ORBIS_OK;
}
/* OrbisNgs2WaveformInfo (shadPS4 ngs2.h): format{type,channels,rate,config,frameOffset,frameMargin},
 * dataOffset, dataSize, loopBegin, loopEnd, numSamples, ..., numBlocks, aBlock[4]. */
typedef struct {
    uint32_t type, channels, rate, config, frame_offset, frame_margin;
    uint32_t data_offset, data_size, loop_begin, loop_end, samples;
    uint32_t unit_size, unit_samples, units_per_frame, frame_size, frame_samples, delay_samples;
    uint32_t blocks;
    struct { uint32_t offset, size, repeats, skip, samples, reserved; uint64_t user; } block[4];
} Ngs2WaveformInfo;
static uint32_t le32(const unsigned char *p) { return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24; }
/* RIFF/WAVE (PCM or ATRAC9 in a RIFF container): format, data range and sample count. */
static int32_t ngs2_parse(const unsigned char *data, uint64_t size, Ngs2WaveformInfo *info) {
    if (!info) return NGS2_INVALID_OUT_ADDRESS;
    memset(info,0,sizeof *info);
    if (!data || size<12 || memcmp(data,"RIFF",4) || memcmp(data+8,"WAVE",4)) return NGS2_INVALID_WAVEFORM_DATA;
    uint32_t format=0, bits=16, fact_samples=0, fact_delay=0, block_align=0, config=0, loop_start=0, loop_stop=0;
    int has_loop=0;
    for (uint64_t pos=12; pos+8<=size;) {
        uint32_t chunk=le32(data+pos+4);
        const unsigned char *body=data+pos+8;
        int is_data=!memcmp(data+pos,"data",4);
        if (pos+8+chunk>size && !is_data) break;
        if (!memcmp(data+pos,"fmt ",4) && chunk>=16) {
            format=data[pos+8]|data[pos+9]<<8;
            info->channels=body[2]|body[3]<<8;
            info->rate=le32(body+4);
            block_align=body[12]|body[13]<<8;
            bits=body[14]|body[15]<<8;
            if (chunk>=52) config=le32(body+44); /* ATRAC9 WAVEFORMATEXTENSIBLE: configData */
        } else if (!memcmp(data+pos,"fact",4) && chunk>=4) {
            fact_samples=le32(body);
            if (chunk>=8) fact_delay=le32(body+4);
        } else if (!memcmp(data+pos,"smpl",4) && chunk>=36+24 && le32(body+28)>0) { /* first loop: id, type, start, end */
            loop_start=le32(body+36+8); loop_stop=le32(body+36+12); has_loop=1;
        } else if (is_data) {
            info->data_offset=(uint32_t)(pos+8);
            info->data_size=chunk;
            if (pos+8+chunk>size) break; /* the sound runs past what was read: nothing after it is needed */
        }
        pos+=8+chunk+(chunk&1);
    }
    if (!info->data_size || !info->channels) return NGS2_INVALID_WAVEFORM_DATA;
    info->type=format==1 ? (bits==8 ? 0x10 : 0x12) : 0x40; /* PCM U8/I16LE, else ATRAC9 */
    info->config=config;
    info->samples=fact_samples ? fact_samples : (format==1 && bits ? info->data_size/(info->channels*(bits/8)) : 0);
    info->unit_size=block_align;
    info->delay_samples=fact_delay;
    if (info->type==0x40 && config) {
        void *probe=Atrac9GetHandle();
        if (probe && !Atrac9InitDecoder(probe,(unsigned char *)&config)) {
            Atrac9CodecInfo ci; Atrac9GetCodecInfo(probe,&ci);
            info->unit_samples=(uint32_t)ci.frameSamples;
            info->units_per_frame=(uint32_t)ci.framesInSuperframe;
            info->frame_size=(uint32_t)ci.superframeSize;
            info->frame_samples=(uint32_t)(ci.frameSamples*ci.framesInSuperframe);
        }
        if (probe) Atrac9ReleaseHandle(probe);
    }
    info->loop_begin=has_loop ? loop_start : 0;
    info->loop_end=has_loop ? loop_stop : (info->samples ? info->samples-1 : 0);
    /* The format is handed back to CalcWaveformBlock: where the sound data starts in the waveform, and the
       decoder delay in samples (ATRAC9 starts decoding that much before the first sample it plays). */
    info->frame_offset=info->data_offset;
    info->frame_margin=info->type==0x40 ? fact_delay : 0;
    info->blocks=1;
    info->block[0].offset=info->data_offset;
    info->block[0].size=info->data_size;
    info->block[0].samples=info->samples;
    info->block[0].skip=fact_delay;
    /* Loop point (RIFF "smpl", positions in the decoder's timeline, which starts fact_delay samples before the first sample
       played): block 0 plays up to the end of the loop, block 1 is the loop body with an infinite repeat count. The game keeps
       submitting block 1 for as long as the voice plays - the stage music loops that way; without it the music ended after
       one pass and stayed silent until something started another track. */
    if (has_loop && loop_stop>loop_start && !getenv("SPARTAN_NGS2_NO_LOOP_BLOCKS")) {
        const uint64_t end=(uint64_t)loop_stop+1;
        if (info->type==0x40 && info->frame_samples && info->frame_size) {
            const uint64_t per=info->frame_samples, sf=info->frame_size;
            uint64_t last0=(end+per-1)/per, first=loop_start/per, last1=last0;
            uint64_t size0=last0*sf, off1=first*sf, size1=(last1-first)*sf;
            if (size0>info->data_size) size0=info->data_size;
            if (off1<info->data_size) {
                if (off1+size1>info->data_size) size1=info->data_size-off1;
                info->block[0].size=(uint32_t)size0;
                info->block[0].samples=end>fact_delay ? (uint32_t)(end-fact_delay) : (uint32_t)end;
                info->block[1].offset=(uint32_t)(info->data_offset+off1);
                info->block[1].size=(uint32_t)size1;
                info->block[1].repeats=0xffffffffu;
                info->block[1].skip=(uint32_t)(loop_start-first*per);
                info->block[1].samples=(uint32_t)(end-loop_start);
                info->blocks=2;
            }
        } else if (info->type==0x12 || info->type==0x10) {
            const uint64_t fb=(uint64_t)info->channels*(info->type==0x12 ? 2u : 1u);
            if (end*fb<=info->data_size && fb) {
                info->block[0].size=(uint32_t)(end*fb);
                info->block[0].samples=(uint32_t)end;
                info->block[1].offset=(uint32_t)(info->data_offset+(uint64_t)loop_start*fb);
                info->block[1].size=(uint32_t)((end-loop_start)*fb);
                info->block[1].repeats=0xffffffffu;
                info->block[1].skip=0;
                info->block[1].samples=(uint32_t)(end-loop_start);
                info->blocks=2;
            }
        }
    }
    return ORBIS_OK;
}
static ABI int32_t ngs2_parse_data(const unsigned char *data, uint64_t size, Ngs2WaveformInfo *info) {
    int32_t rc=ngs2_parse(data,size,info);
    static int reported;
    if (rc && reported++<50) runtime_audio_log("ngs2.parse_data_failed rc=%#x size=%llu",(unsigned)rc,(unsigned long long)size);
    return rc;
}
static ABI int32_t ngs2_parse_file(const char *path, uint64_t offset, Ngs2WaveformInfo *info) {
    if (!info) return NGS2_INVALID_OUT_ADDRESS;
    if (!path) return NGS2_INVALID_WAVEFORM_DATA;
    int fd=(int)runtime_file_open(path,0,0);
    if (fd<0) return NGS2_INVALID_WAVEFORM_DATA;
    unsigned char header[4096];
    int64_t got=runtime_file_pread(fd,header,sizeof header,(int64_t)offset);
    runtime_file_close(fd);
    int32_t rc=got>0 ? ngs2_parse(header,(uint64_t)got,info) : NGS2_INVALID_WAVEFORM_DATA;
    static int reported;
    if (reported++<200) runtime_audio_log("ngs2.parse_file path=%s offset=%llu rc=%#x type=%#x channels=%u rate=%u samples=%u data_size=%u loop=%u..%u blocks=%u b0{off=%u size=%u skip=%u samples=%u} b1{off=%u size=%u rep=%u skip=%u samples=%u}",path,
                                          (unsigned long long)offset,(unsigned)rc,info->type,info->channels,info->rate,info->samples,info->data_size,
                                          info->loop_begin,info->loop_end,info->blocks,info->block[0].offset,info->block[0].size,info->block[0].skip,info->block[0].samples,
                                          info->block[1].offset,info->block[1].size,info->block[1].repeats,info->block[1].skip,info->block[1].samples);
    return rc;
}
/* The bytes of a waveform that hold `samples` samples from sample `position`, as a block a sampler voice can play.
 * format = {type, channels, rate, config, frameOffset, frameMargin}; frameOffset and frameMargin are what
 * ParseWaveform* returned (data start, decoder delay). The block offset is in the same coordinates as a parsed
 * waveform's blocks (the data start included). ATRAC9 starts a superframe early after a seek so that the
 * overlap of the first frame is right, and the block's skip drops that lead-in again. */
static ABI int32_t ngs2_calc_block(const void *format, uint32_t position, uint32_t samples, void *block, uint64_t arg5, uint64_t arg6) {
    if (!block) return NGS2_INVALID_OUT_ADDRESS;
    Ngs2Block out; memset(&out,0,sizeof out);
    uint32_t f[6]={0};
    if (format) memcpy(f,format,sizeof f);
    int result=ORBIS_OK;
    if (!format) result=NGS2_INVALID_WAVEFORM_DATA;
    else if (f[0]==0x10 || f[0]==0x12 || f[0]==0x13) {
        uint32_t frame_bytes=f[1]*(f[0]==0x13 ? 4u : f[0]==0x12 ? 2u : 1u);
        out.offset=f[4]+position*frame_bytes;
        out.size=out.offset+samples*frame_bytes; /* Sony Ngs2: end offset so (end - start) == size */
        out.samples=samples;
    } else if (f[0]==0x40 && f[3]) {
        void *probe=Atrac9GetHandle(); Atrac9CodecInfo ci;
        unsigned char config[4]; memcpy(config,&f[3],4);
        if (!probe || Atrac9InitDecoder(probe,config)) result=NGS2_INVALID_WAVEFORM_DATA;
        else {
            Atrac9GetCodecInfo(probe,&ci);
            uint64_t per_superframe=(uint64_t)ci.frameSamples*(uint64_t)ci.framesInSuperframe;
            if (!per_superframe || ci.superframeSize<=0) result=NGS2_INVALID_WAVEFORM_DATA;
            else {
                uint64_t begin=(uint64_t)position+f[5];                  /* in the decoder's own timeline */
                uint64_t first=begin/per_superframe;
                /* Capcom MT Framework passes `samples` as the loop end point in the file timeline (e.g. 4171836),
                 * not the remaining duration. When position > 0, calculating (begin + samples) double-counted the
                 * seek position, producing out.size far larger than data_size and causing the feeder to overread into
                 * adjacent subfiles (ASCII "ver."). */
                uint64_t end_sample=(uint64_t)samples+f[5];
                if (end_sample<begin) end_sample=begin;
                uint64_t last=(end_sample+per_superframe-1)/per_superframe;
                if (last<=first) last=first+1;
                /* Capcom MT Framework adds data chunk base offset to out.offset when seeking file.
                 * out.offset must be purely superframe-aligned stream byte offset, without f[4] added again. */
                out.offset=(uint32_t)(first*(uint64_t)ci.superframeSize);
                out.size=(uint32_t)(last*(uint64_t)ci.superframeSize); /* Sony Ngs2: end offset so (end - start) == size */
                out.skip=(uint32_t)(begin-first*per_superframe);
                out.samples=(uint32_t)(end_sample-begin);
            }
        }
        if (probe) Atrac9ReleaseHandle(probe);
    }
    memcpy(block,&out,sizeof out);
    static int reported;
    if (reported++<60)
        runtime_audio_log("ngs2.calc_block type=%#x channels=%u rate=%u config=%#x frame_offset=%u frame_margin=%u position=%u samples=%u -> offset=%u size=%u skip=%u samples=%u rc=%#x extra_args=%llu,%llu",
                          f[0],f[1],f[2],f[3],f[4],f[5],position,samples,out.offset,out.size,out.skip,out.samples,(unsigned)result,
                          (unsigned long long)arg5,(unsigned long long)arg6);
    return result;
}

/* SPARTAN_AVPLAYER_TRACE=1: AvPlayer calls a game makes for movie sound, passed through to the
 * GPU library's implementation (diagnostics only). */
typedef int32_t (ABI *AvAddSource)(void *, const char *);
typedef int32_t (ABI *AvHandleInt)(void *, uint32_t);
typedef int32_t (ABI *AvHandle)(void *);
typedef uint8_t (ABI *AvFrame)(void *, unsigned char *);
static AvAddSource av_add_source;
static AvHandleInt av_enable_stream;
static AvHandle av_stream_count;
static AvFrame av_audio_data;
static ABI int32_t trace_av_add_source(void *h, const char *path) {
    int32_t r=av_add_source(h,path);
    printf("AvPlayer trace: AddSource(%s) = %#x\n",path ? path : "(null)",(unsigned)r);
    runtime_audio_log("avplayer.add_source path=%s result=%#x",path ? path : "(null)",(unsigned)r);
    return r;
}
static ABI int32_t trace_av_enable_stream(void *h, uint32_t index) {
    int32_t r=av_enable_stream(h,index);
    printf("AvPlayer trace: EnableStream(%u) = %#x\n",index,(unsigned)r);
    runtime_audio_log("avplayer.enable_stream index=%u result=%#x",index,(unsigned)r);
    return r;
}
static ABI int32_t trace_av_stream_count(void *h) {
    int32_t r=av_stream_count(h);
    printf("AvPlayer trace: StreamCount = %d\n",r);
    runtime_audio_log("avplayer.stream_count count=%d",r);
    return r;
}
static ABI uint8_t trace_av_audio_data(void *h, unsigned char *info) {
    uint8_t r=av_audio_data(h,info);
    static unsigned calls, frames;
    ++calls; if (r) ++frames;
    if (calls<=5 || (r && frames<=5) || calls%2000==0) {
        uint16_t channels=0; uint32_t rate=0, size=0;
        if (info) { memcpy(&channels,info+24,2); memcpy(&rate,info+28,4); memcpy(&size,info+32,4); }
        printf("AvPlayer trace: GetAudioData #%u -> %u (%u frames so far; %u ch, %u Hz, %u bytes)\n",calls,r,frames,channels,rate,size);
        runtime_audio_log("avplayer.audio_data calls=%u got=%u frames=%u channels=%u rate=%u bytes=%u",calls,r,frames,channels,rate,size);
    }
    return r;
}
static uintptr_t avplayer_trace_resolve(const char *name) {
    static int on=-1;
    if (on<0) on=getenv("SPARTAN_AVPLAYER_TRACE")!=NULL || runtime_audio_log_enabled();
    if (!on) return 0;
    const char *symbol=runtime_symbol(name);
    if (!symbol) return 0;
    uintptr_t real=spartanportgpu_resolve(name);
    if (!real) return 0;
    if (!strcmp(symbol,"sceAvPlayerAddSource")) { av_add_source=(AvAddSource)real; return (uintptr_t)trace_av_add_source; }
    if (!strcmp(symbol,"sceAvPlayerEnableStream")) { av_enable_stream=(AvHandleInt)real; return (uintptr_t)trace_av_enable_stream; }
    if (!strcmp(symbol,"sceAvPlayerStreamCount")) { av_stream_count=(AvHandle)real; return (uintptr_t)trace_av_stream_count; }
    if (!strcmp(symbol,"sceAvPlayerGetAudioData")) { av_audio_data=(AvFrame)real; return (uintptr_t)trace_av_audio_data; }
    return 0;
}

static const RuntimeExport exports[]={
    {"sceKernelCreateEventFlag",evf_create}, {"sceKernelDeleteEventFlag",evf_delete},
    {"sceKernelSetEventFlag",evf_set}, {"sceKernelWaitEventFlag",evf_wait},
    {"sceKernelPollEventFlag",evf_poll}, {"sceKernelClearEventFlag",evf_clear_bits},
    {"sceKernelCancelEventFlag",evf_cancel}, {"scePthreadCancel",pthread_cancel_guest},
    {"sceAppContentAddcontMount",addcont_mount}, {"sceAppContentAddcontUnmount",addcont_unmount},
    {"sceAppContentTemporaryDataMount2",temp_mount2},
    {"sceAppContentTemporaryDataGetAvailableSpaceKb",space_kb},
    {"sceAppContentDownloadDataGetAvailableSpaceKb",space_kb},
    {"scePlayGoGetLanguageMask",playgo_language_mask}, {"scePlayGoGetToDoList",playgo_todo_list},
    {"scePlayGoPrefetch",ok},
    {"sceRtcParseRFC3339",rtc_parse_rfc3339},
    {"sceUserServiceGetUserColor",user_color}, {"sceSystemServiceReenableMusicPlayer",ok},
    {"sceNetCtlGetResult",netctl_result}, {"sceNetGetMacAddress",net_mac},
    {"sceHttpsLoadCert",ok}, {"sceHttpSetAutoRedirect",ok}, {"sceHttpSetCookieEnabled",ok},
    {"sceHttpSetSendTimeOut",ok}, {"sceHttpSetRecvTimeOut",ok},
    {"sceHttpsGetSslError",http_ssl_error}, {"sceHttpGetLastErrno",http_last_errno},
    {"sceHttpGetAllResponseHeaders",http_fail}, {"sceHttpParseResponseHeader",http_fail},
    {"sceNpCreateRequest",object_id}, {"sceNpHasSignedUp",np_has_signed_up},
    {"sceNpGetAccountCountry",np_signed_out}, {"sceNpAuthAbortRequest",ok},
    {"sceNpPartyInitialize",ok}, {"sceNpPartyTerminate",ok}, {"sceNpPartyRegisterHandler",ok},
    {"sceNpPartyCheckCallback",ok}, {"sceNpPartyGetId",np_signed_out},
    {"sceNpScorePollAsync",np_poll}, {"sceNpScoreRecordGameDataAsync",np_signed_out},
    {"sceNpScoreRecordScoreAsync",np_signed_out}, {"sceNpScoreGetRankingByRangeAsync",np_signed_out},
    {"sceNpScoreGetRankingByNpIdAsync",np_signed_out}, {"sceNpScoreGetGameDataAsync",np_signed_out},
    {"sceNpTusCreateNpTitleCtx",object_id}, {"sceNpTssCreateNpTitleCtx",object_id},
    {"sceNpTusDeleteNpTitleCtx",ok}, {"sceNpTusCreateRequest",object_id},
    {"sceNpTusDeleteRequest",ok}, {"sceNpTusAbortRequest",ok}, {"sceNpTusPollAsync",np_poll},
    {"sceNpTssGetDataAsync",np_signed_out}, {"sceNpTusDeleteMultiSlotDataAsync",np_signed_out},
    {"sceNpTusSetDataAsync",np_signed_out}, {"sceNpTusGetDataAsync",np_signed_out},
    {"sceNpWebApiCreateHandle",object_id}, {"sceNpWebApiDeleteHandle",ok}, {"sceNpWebApiGetErrorCode",ok},
    {"sceNpWebApiCreateServicePushEventFilter",np_signed_out}, {"sceNpWebApiDeleteServicePushEventFilter",ok},
    {"sceNpWebApiRegisterServicePushEventCallback",np_signed_out},
    {"sceNpWebApiUnregisterServicePushEventCallback",ok},
    {"sceVoiceQoSInit",ok}, {"sceVoiceQoSEnd",ok},
    {"sceVoiceQoSCreateLocalEndpoint",voice_endpoint}, {"sceVoiceQoSDeleteLocalEndpoint",ok},
    {"sceVoiceQoSCreateRemoteEndpoint",voice_endpoint}, {"sceVoiceQoSDeleteRemoteEndpoint",ok},
    {"sceVoiceQoSConnect",voice_endpoint}, {"sceVoiceQoSDisconnect",ok},
    {"sceVoiceQoSGetLocalEndpointAttribute",voice_get_attribute},
    {"sceVoiceQoSSetLocalEndpointAttribute",ok}, {"sceVoiceQoSSetConnectionAttribute",ok},
    {"sceVoiceQoSReadPacket",voice_packet}, {"sceVoiceQoSWritePacket",ok},
    {"sceErrorDialogInitialize",error_dialog_init}, {"sceErrorDialogOpen",error_dialog_open},
    {"sceErrorDialogUpdateStatus",error_dialog_update}, {"sceErrorDialogTerminate",error_dialog_term},
    {"sceInvitationDialogUpdateStatus",dialog_update_none}, {"sceInvitationDialogGetResult",dialog_result_canceled},
    {"sceInvitationDialogTerminate",ok},
    {"sceWebBrowserDialogUpdateStatus",dialog_update_none}, {"sceWebBrowserDialogGetResult",dialog_result_canceled},
    {"sceWebBrowserDialogTerminate",ok},
    {"sceNpCommerceDialogGetResult",commerce_result}, {"sceMsgDialogProgressBarInc",ok},
    {"sceGameLiveStreamingInitialize",ok}, {"sceGameLiveStreamingTerminate",ok},
    {"sceGameLiveStreamingGetCurrentStatus",live_status}, {"sceGameLiveStreamingEnableLiveStreaming",ok},
    {"sceGameLiveStreamingPermitServerSideRecording",ok},
    {"sceGameLiveStreamingSetPresetSocialFeedbackCommandsDescription",ok},
    {"sceGameLiveStreamingSetPresetSocialFeedbackCommands",ok},
    {"sceGameLiveStreamingGetSocialFeedbackMessagesCount",live_message_count},
    {"sceGameLiveStreamingGetSocialFeedbackMessages",ok},
    {"sceRemoteplayInitialize",ok}, {"sceRemoteplayTerminate",ok},
    {"sceRemoteplayApprove",ok}, {"sceRemoteplayProhibit",ok},
    {"sceScreenShotEnable",ok}, {"sceScreenShotDisable",ok},
    {"sceScreenShotSetOverlayImage",ok}, {"sceScreenShotSetParam",ok},
    {"sceSharePlayInitialize",ok}, {"sceSharePlayTerminate",ok},
    {"sceSharePlayGetCurrentConnectionInfo",shareplay_info}, {"sceSharePlaySetProhibition",ok},
    {"sceVideoRecordingOpen",ok}, {"sceVideoRecordingClose",ok}, {"sceVideoRecordingStart",ok},
    {"sceVideoRecordingStop",ok}, {"sceVideoRecordingQueryMemSize",ok},
    {"sceVideoRecordingGetStatus",ok}, {"sceVideoRecordingSetInfo",ok},
    {"sceNgs2SystemCreateWithAllocator",ngs2_system_create}, {"sceNgs2SystemDestroy",ngs2_system_destroy},
    {"sceNgs2RackCreateWithAllocator",ngs2_rack_create}, {"sceNgs2RackDestroy",ngs2_rack_destroy},
    {"sceNgs2RackGetVoiceHandle",ngs2_rack_voice}, {"sceNgs2VoiceControl",ngs2_voice_control},
    {"sceNgs2VoiceGetState",ngs2_voice_state}, {"sceNgs2VoiceGetStateFlags",ngs2_voice_flags},
    {"sceNgs2SystemRender",ngs2_render},
    {"sceNgs2ParseWaveformData",ngs2_parse_data}, {"sceNgs2ParseWaveformFile",ngs2_parse_file},
    {"sceNgs2CalcWaveformBlock",ngs2_calc_block},
};
uintptr_t runtime_offline_resolve(const char *name) {
    uintptr_t traced=avplayer_trace_resolve(name);
    return traced ? traced : RUNTIME_LOOKUP(exports,name);
}
