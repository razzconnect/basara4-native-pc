/* libSceAudioOut on SDL3 audio streams. sceAudioOutOutput returns once per
 * buffer period on a steady clock, like PS4 hardware: FMOD's output thread
 * reads 256-frame slots out of its mixer's 512-frame ring, and returning in
 * bursts (whenever the device pulls a quantum) lets it overtake the mixer and
 * play stale halves - a click every 10.7 ms. The lowest SDL queue level of
 * each window is steered to two buffers (silence prefill on underrun, ±3%
 * cadence corrections), which also absorbs device clock drift. Without an audio device (headless runs, SPARTAN_AUDIO=none)
 * ports only follow the clock. */
#define _GNU_SOURCE
#include "runtime.h"
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <math.h>
#include <SDL3/SDL.h>

#define PORTS 25
#define ERR_NOT_OPENED ((int32_t)0x80260001)
#define ERR_INVALID_PORT ((int32_t)0x80260003)
#define ERR_INVALID_POINTER ((int32_t)0x80260004)
#define ERR_PORT_FULL ((int32_t)0x80260005)
#define ERR_INVALID_SIZE ((int32_t)0x80260006)
#define ERR_INVALID_FORMAT ((int32_t)0x80260007)
#define ERR_INVALID_FREQ ((int32_t)0x80260008)
#define ERR_INVALID_VOLUME ((int32_t)0x80260009)
#define ERR_INVALID_TYPE ((int32_t)0x8026000A)
#define ERR_ALREADY_INIT ((int32_t)0x8026000E)
#define ERR_NOT_INIT ((int32_t)0x8026000F)
#define VOLUME_0DB 32768

typedef struct {
    int used, type, channels, is_float, frames, sample_bytes, std_layout, downmix;
    int32_t volume[8];
    SDL_AudioStream *stream;
    uint64_t next_deadline_ns; /* next return of sceAudioOutOutput */
    int64_t adjust_ns; int window_min, window_count; /* queue level control */
    uint64_t last_output_us;
    pthread_mutex_t lock;
    FILE *dump;                     /* SPARTAN_AUDIO_DUMP: raw converted PCM per port */
    uint64_t lg_start_ns, lg_buffers, lg_null, lg_loud, lg_total, lg_total_loud; /* SPARTAN_AUDIO_DEBUG */
    float lg_peak; int lg_sound_seen;
    int stats;
    uint64_t stat_start_ns, stat_last_ns, stat_max_gap_ns; /* SPARTAN_AUDIO_STATS */
    unsigned stat_starved, stat_buffers; int stat_min_queued;
} Port;
typedef struct { uint16_t output; uint8_t channel, reserved; int16_t volume; uint16_t reroute; uint64_t flag, reserved64[2]; } PortState;
_Static_assert(sizeof(PortState)==32,"AudioOut port state layout");

static pthread_mutex_t table_lock=PTHREAD_MUTEX_INITIALIZER;
static Port ports[PORTS];
static int initialized, sdl_ready=-1;
static size_t buffers_out, ports_opened;

static uint64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000000000u+(uint64_t)t.tv_nsec; }
/* Wall-clock milliseconds since 1970: the audio log and the render trace both write theirs at "0 ms", so the two
 * logs (each counts from its own start) can be laid side by side. */
static uint64_t wall_ms(void) { struct timespec t; clock_gettime(CLOCK_REALTIME,&t); return (uint64_t)t.tv_sec*1000u+(uint64_t)t.tv_nsec/1000000u; }

/* ---- SPARTAN_AUDIO_DEBUG=<file>: "[  ms] kind key=value ..." lines, flushed per line, capped at 64 MiB ---- */
static FILE *alog_file;
static int alog_state=-1;
static uint64_t alog_start_ns, alog_bytes;
static pthread_mutex_t alog_lock=PTHREAD_MUTEX_INITIALIZER;
#define ALOG_LIMIT (64ull<<20)
int runtime_audio_log_enabled(void) {
    if (alog_state>=0) return alog_state>0;
    pthread_mutex_lock(&alog_lock);
    if (alog_state<0) {
        const char *path=getenv("SPARTAN_AUDIO_DEBUG");
        alog_state=0;
        if (path && path[0] && (alog_file=fopen(path,"w"))) {
            alog_start_ns=now_ns();
            fprintf(alog_file,"[       0 ms] log.start format=1 audio_mode=%s epoch_ms=%llu\n",
                    getenv("SPARTAN_AUDIO") ? getenv("SPARTAN_AUDIO") : "default",(unsigned long long)wall_ms());
            fflush(alog_file);
            alog_state=1;
        }
    }
    pthread_mutex_unlock(&alog_lock);
    return alog_state>0;
}
void runtime_audio_log(const char *fmt, ...) {
    if (!runtime_audio_log_enabled()) return;
    char line[1024];
    va_list args; va_start(args,fmt);
    int n=vsnprintf(line,sizeof line,fmt,args);
    va_end(args);
    if (n<0) return;
    if ((size_t)n>=sizeof line) n=(int)sizeof line-1;
    pthread_mutex_lock(&alog_lock);
    if (alog_file) {
        uint64_t ms=(now_ns()-alog_start_ns)/1000000u;
        if (alog_bytes>ALOG_LIMIT) {
            fprintf(alog_file,"[%8llu ms] log.limit_reached bytes=%llu (further lines dropped)\n",(unsigned long long)ms,(unsigned long long)alog_bytes);
            fclose(alog_file); alog_file=NULL; alog_state=0;
        } else {
            alog_bytes+=(uint64_t)fprintf(alog_file,"[%8llu ms] %.*s\n",(unsigned long long)ms,n,line);
            fflush(alog_file);
        }
    }
    pthread_mutex_unlock(&alog_lock);
}
uint64_t runtime_audio_log_ms(void) { return alog_start_ns ? (now_ns()-alog_start_ns)/1000000u : 0; }
static const char *port_type_name(int type) {
    switch (type) {
    case 0: return "main"; case 1: return "bgm"; case 2: return "voice"; case 3: return "personal";
    case 4: return "padspk"; case 126: return "audio3d"; case 127: return "aux"; default: return "?";
    }
}
/* ---- level summaries in the audio log (while SPARTAN_AUDIO_DEBUG is on) ----
 * "audio.level1s": one line per port per second for the whole session (peak, rms, how many buffers were silent).
 * "audio.level": one line per port per 100 ms, only within LVL_WINDOW_MS before and after an F11 marker
 * (runtime_audio_marker), so a short glitch around the moment the player saw it can be seen without writing
 * 100 ms lines for the whole session. The last LVL_RING bins (15 s) are kept in memory, so the seconds before the
 * marker are still there when it arrives. Nothing here allocates, and nothing runs while the log is off.
 * Times are milliseconds on the audio log's clock; a line's own "t=" is the start of its bin, so a bin written
 * after the marker can sit below later lines of the log. */
#define LVL_BIN_MS 100u
#define LVL_RING 150 /* 15 s of 100 ms bins */
#define LVL_WINDOW_MS 15000u
#define LVL_MARKERS 16
#define LVL_SILENT 0.001f /* a buffer whose peak is below this is silent, as in audioout.stats */
typedef struct { uint64_t start_ms; float peak; double sumsq; uint64_t samples; unsigned buffers, loud; } LevelBin;
typedef struct {
    LevelBin cur, sec;                  /* the 100 ms and 1 s bins being filled (buffers==0: none yet) */
    LevelBin ring[LVL_RING]; unsigned ring_count;
    uint64_t written_until_ms;          /* start of the newest 100 ms bin already written, +1 (0: none) */
    unsigned markers_seen;
} LevelTrack;
static LevelTrack levels[PORTS];
static uint64_t marker_ms[LVL_MARKERS];
static unsigned marker_count;           /* markers set so far; written under alog_lock, read without it */

static float bin_rms(const LevelBin *b) { return b->samples ? (float)sqrt(b->sumsq/(double)b->samples) : 0.0f; }
static void level_write100(int id, const char *type, const LevelBin *b) {
    runtime_audio_log("audio.level port=%d type=%s t=%llu peak=%.4f rms=%.4f buffers=%u loud=%u",id,type,
                      (unsigned long long)b->start_ms,b->peak,bin_rms(b),b->buffers,b->loud);
}
/* Is a 100 ms bin starting at start_ms within the window of a marker that is already known? */
static int level_wanted(uint64_t start_ms, unsigned known) {
    for (unsigned i=known>LVL_MARKERS ? known-LVL_MARKERS : 0; i<known; ++i) {
        /* read without a lock while audio_marker_at may rewrite the slot (only after LVL_MARKERS more markers) */
        uint64_t m=__atomic_load_n(&marker_ms[i%LVL_MARKERS],__ATOMIC_RELAXED);
        if (start_ms+LVL_BIN_MS>m-(m<LVL_WINDOW_MS ? m : LVL_WINDOW_MS) && start_ms<=m+LVL_WINDOW_MS) return 1;
    }
    return 0;
}
static void level_note_markers(LevelTrack *L, int id, const char *type, unsigned known) {
    if (known-L->markers_seen>LVL_MARKERS) L->markers_seen=known-LVL_MARKERS; /* too many at once: the oldest are gone */
    for (; L->markers_seen<known; ++L->markers_seen) {
        /* a marker has arrived: write the bins kept before it (oldest first) that are inside its window */
        unsigned count=L->ring_count<LVL_RING ? L->ring_count : LVL_RING;
        for (unsigned k=0; k<count; ++k) {
            const LevelBin *b=&L->ring[(L->ring_count-count+k)%LVL_RING];
            if (b->start_ms+1>L->written_until_ms && level_wanted(b->start_ms,L->markers_seen+1)) {
                level_write100(id,type,b); L->written_until_ms=b->start_ms+1;
            }
        }
    }
}
static void level_close100(LevelTrack *L, int id, const char *type, unsigned known) {
    if (!L->cur.buffers) return;
    L->ring[L->ring_count%LVL_RING]=L->cur; ++L->ring_count;
    if (L->cur.start_ms+1>L->written_until_ms && level_wanted(L->cur.start_ms,known)) {
        level_write100(id,type,&L->cur); L->written_until_ms=L->cur.start_ms+1;
    }
    memset(&L->cur,0,sizeof L->cur);
}
static void level_close1s(LevelTrack *L, int id, const char *type) {
    if (!L->sec.buffers) return;
    runtime_audio_log("audio.level1s port=%d type=%s t=%llu peak=%.4f rms=%.4f buffers=%u loud=%u",id,type,
                      (unsigned long long)L->sec.start_ms,L->sec.peak,bin_rms(&L->sec),L->sec.buffers,L->sec.loud);
    memset(&L->sec,0,sizeof L->sec);
}
/* One buffer given to port `id` at now_ms: its peak and sum of squares over `samples` values. Called with the
 * port locked, so a port's bins are only touched by one thread at a time. */
static void level_feed(int id, const char *type, uint64_t now_ms, float peak, double sumsq, uint64_t samples) {
    LevelTrack *L=&levels[id];
    unsigned known=__atomic_load_n(&marker_count,__ATOMIC_ACQUIRE);
    level_note_markers(L,id,type,known);
    uint64_t b100=now_ms/LVL_BIN_MS*LVL_BIN_MS, b1s=now_ms/1000u*1000u;
    if (L->cur.buffers && L->cur.start_ms!=b100) level_close100(L,id,type,known);
    if (L->sec.buffers && L->sec.start_ms!=b1s) level_close1s(L,id,type);
    LevelBin *bins[2]={&L->cur,&L->sec};
    uint64_t starts[2]={b100,b1s};
    for (int i=0; i<2; ++i) {
        LevelBin *b=bins[i];
        if (!b->buffers) b->start_ms=starts[i];
        if (peak>b->peak) b->peak=peak;
        b->sumsq+=sumsq; b->samples+=samples; ++b->buffers;
        if (peak>=LVL_SILENT) ++b->loud;
    }
}
/* The port closes: what is left in its bins is written, so the last second of a port is not lost. */
static void level_flush(int id, const char *type) {
    if (!runtime_audio_log_enabled()) return;
    LevelTrack *L=&levels[id];
    unsigned known=__atomic_load_n(&marker_count,__ATOMIC_ACQUIRE);
    level_note_markers(L,id,type,known);
    level_close100(L,id,type,known);
    level_close1s(L,id,type);
}
/* The player pressed F11 (or another capture was requested) at now_ms: remember it, so the 100 ms bins around it
 * are written. */
static void audio_marker_at(uint64_t now_ms, uint64_t wall, const char *reason) {
    pthread_mutex_lock(&alog_lock);
    __atomic_store_n(&marker_ms[marker_count%LVL_MARKERS],now_ms,__ATOMIC_RELAXED);
    __atomic_store_n(&marker_count,marker_count+1,__ATOMIC_RELEASE);
    pthread_mutex_unlock(&alog_lock);
    runtime_audio_log("capture.marker reason=%s epoch_ms=%llu window_s=%u",reason ? reason : "?",
                      (unsigned long long)wall,LVL_WINDOW_MS/1000u);
}
void runtime_audio_marker(const char *reason) {
    if (!runtime_audio_log_enabled()) return;
    audio_marker_at(runtime_audio_log_ms(),wall_ms(),reason);
}
static void sleep_until(uint64_t deadline) {
#ifdef _WIN32
    /* winpthreads' clock_nanosleep only takes CLOCK_REALTIME (EINVAL otherwise). */
    for (uint64_t now=now_ns(); now<deadline; now=now_ns()) compat_sleep_ns(deadline-now);
#else
    struct timespec t={(time_t)(deadline/1000000000u),(long)(deadline%1000000000u)};
    while (clock_nanosleep(CLOCK_MONOTONIC,TIMER_ABSTIME,&t,NULL)) {}
#endif
}
static int sdl_audio(void) {
    if (sdl_ready<0) {
        const char *mode=getenv("SPARTAN_AUDIO");
        sdl_ready = (!mode || strcmp(mode,"none")) && SDL_InitSubSystem(SDL_INIT_AUDIO);
        printf("Runtime: audio backend %s\n", sdl_ready ? SDL_GetCurrentAudioDriver() : "timer (silent)");
        if (runtime_audio_log_enabled()) {
            SDL_AudioSpec device={0}; int device_frames=0;
            int have=sdl_ready && SDL_GetAudioDeviceFormat(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK,&device,&device_frames);
            runtime_audio_log("audioout.backend driver=%s device_channels=%d device_rate=%d device_frames=%d",
                              sdl_ready ? SDL_GetCurrentAudioDriver() : "timer", have ? device.channels : 0, have ? device.freq : 0, device_frames);
        }
    }
    return sdl_ready;
}
static int port_range(int type, int *first, int *last) {
    switch (type) {
    case 0: *first=0; *last=7; return 1;      /* main */
    case 1: *first=8; *last=8; return 1;      /* bgm */
    case 2: *first=9; *last=12; return 1;     /* voice */
    case 3: *first=13; *last=16; return 1;    /* personal */
    case 4: *first=17; *last=20; return 1;    /* pad speaker */
    case 126: *first=21; *last=22; return 1;  /* audio3d */
    case 127: *first=23; *last=24; return 1;  /* aux */
    default: return 0;
    }
}
/* High quality soft knee limiter: transparent below threshold (0.7), smooth saturation above.
 * Prevents harsh digital clipping / square wave distortion when multiple loud SFX sum together. */
static inline float audio_soft_limit(float x) {
    if (x >= -0.7f && x <= 0.7f) return x;
    if (x > 0.7f) {
        float over = x - 0.7f;
        return 0.7f + 0.295f * tanhf(over / 0.295f);
    } else {
        float over = -x - 0.7f;
        return -(0.7f + 0.295f * tanhf(over / 0.295f));
    }
}

/* SDL's 7.1-to-stereo matrix keeps only ~0.21 of the front channels (-13.5 dB), so the game
   was far too quiet on stereo outputs. Folds SDL-ordered FL FR C LFE BL BR SL SR frames to
   stereo in place: fronts at full level, center and surrounds at -3 dB, LFE dropped, soft-limited.
   Returns the stereo byte count. */
static size_t downmix_stereo(unsigned char *buf, size_t frames, int is_float) {
    const float k=0.70710678f;
    for (size_t f=0;f<frames;++f) {
        float s[8], lr[2];
        for (int c=0;c<8;++c) {
            if (is_float) memcpy(&s[c],buf+(f*8+(size_t)c)*4,4);
            else { int16_t v; memcpy(&v,buf+(f*8+(size_t)c)*2,2); s[c]=(float)v/32768.0f; }
        }
        lr[0]=s[0]+k*(s[2]+s[4]+s[6]); lr[1]=s[1]+k*(s[2]+s[5]+s[7]);
        for (int c=0;c<2;++c) {
            float v=audio_soft_limit(lr[c]);
            if (is_float) memcpy(buf+(f*2+(size_t)c)*4,&v,4);
            else { int16_t o=(int16_t)(v*32767.0f); memcpy(buf+(f*2+(size_t)c)*2,&o,2); }
        }
    }
    return frames*2*(is_float ? 4u : 2u);
}
static Port *port_of(int32_t handle, int32_t *error) {
    int id=handle & 0xff;
    if ((handle & 0x3f000000)!=0x20000000 || id>=PORTS) { *error=ERR_INVALID_PORT; return NULL; }
    if (!ports[id].used) { *error=ERR_NOT_OPENED; return NULL; }
    return &ports[id];
}

static ABI int32_t audio_init(void) {
    pthread_mutex_lock(&table_lock);
    int32_t r=initialized ? ERR_ALREADY_INIT : 0;
    initialized=1;
    pthread_mutex_unlock(&table_lock);
    runtime_audio_log("audioout.init result=%#x",(unsigned)r);
    return r;
}
#ifdef TEST_AUDIO_HOOK
static SDL_AudioStream *(*test_open_stream)(SDL_AudioDeviceID, const SDL_AudioSpec *, SDL_AudioStreamCallback, void *) = NULL;
#define OPEN_AUDIO_STREAM(dev, spec, cb, user) (test_open_stream ? test_open_stream(dev, spec, cb, user) : SDL_OpenAudioDeviceStream(dev, spec, cb, user))
#else
#define OPEN_AUDIO_STREAM(dev, spec, cb, user) SDL_OpenAudioDeviceStream(dev, spec, cb, user)
#endif

#ifdef _WIN32
static void try_audio_fallback(Port *p, int id) {
    const char *driver = SDL_GetCurrentAudioDriver();
    /* When WASAPI fails on Windows and the user hasn't forced a driver, try DirectSound. */
    if (!driver || strcmp(driver, "wasapi") || getenv("SDL_AUDIO_DRIVER")) return;

    /* Only switch backend if no other port currently has an active stream. */
    for (int i = 0; i < PORTS; ++i) {
        if (ports[i].used && ports[i].stream) return;
    }

    int has_ds = 0, n = SDL_GetNumAudioDrivers();
    for (int i = 0; i < n; ++i) {
        const char *d = SDL_GetAudioDriver(i);
        if (d && !strcmp(d, "directsound")) { has_ds = 1; break; }
    }
    if (!has_ds) return;

    fprintf(stderr, "Runtime: SDL audio stream failed on wasapi (%s); trying directsound fallback for port %d\n", SDL_GetError(), id);
    SDL_QuitSubSystem(SDL_INIT_AUDIO);
    SDL_SetHintWithPriority(SDL_HINT_AUDIO_DRIVER, "directsound", SDL_HINT_OVERRIDE);
    sdl_ready = SDL_InitSubSystem(SDL_INIT_AUDIO);
    const char *new_driver = sdl_ready ? SDL_GetCurrentAudioDriver() : NULL;
    if (sdl_ready && new_driver && !strcmp(new_driver, "directsound")) {
        printf("Runtime: audio backend %s (fallback from wasapi)\n", new_driver);
        SDL_AudioSpec device; int device_frames;
        p->downmix = p->channels == 8 && SDL_GetAudioDeviceFormat(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &device, &device_frames)
                     && device.channels == 2;
        SDL_AudioSpec spec = {p->is_float ? SDL_AUDIO_F32 : SDL_AUDIO_S16, p->downmix ? 2 : p->channels, 48000};
        p->stream = OPEN_AUDIO_STREAM(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, NULL, NULL);
    } else {
        fprintf(stderr, "Runtime: failed to initialize directsound audio backend (current: %s, error: %s) for port %d\n",
                new_driver ? new_driver : "none", SDL_GetError(), id);
    }
}
#endif

static int32_t open_fail(int32_t code, int32_t type, uint32_t length, uint32_t freq, uint32_t param) {
    runtime_audio_log("audioout.open_failed code=%#x type=%d(%s) length=%u freq=%u param=%#x",(unsigned)code,type,port_type_name(type),length,freq,param);
    return code;
}
static ABI int32_t audio_open(int32_t user, int32_t type, int32_t index, uint32_t length, uint32_t freq, uint32_t param) {
    (void)user; (void)index;
    if (!initialized) return open_fail(ERR_NOT_INIT,type,length,freq,param);
    if (!length || length>2048 || (length & 0xff)) return open_fail(ERR_INVALID_SIZE,type,length,freq,param);
    if (freq!=48000) return open_fail(ERR_INVALID_FREQ,type,length,freq,param);
    uint32_t format=param & 0xff;
    if (format>7) return open_fail(ERR_INVALID_FORMAT,type,length,freq,param);
    int first, last;
    if (!port_range(type,&first,&last)) return open_fail(ERR_INVALID_TYPE,type,length,freq,param);
    static const int channels[8]={1,2,8,1,2,8,8,8};
    pthread_mutex_lock(&table_lock);
    int id=-1;
    for (int i=first;i<=last;++i) if (!ports[i].used) { id=i; break; }
    if (id<0) { pthread_mutex_unlock(&table_lock); return open_fail(ERR_PORT_FULL,type,length,freq,param); }
    Port *p=&ports[id];
    memset(p,0,sizeof(*p));
    /* The bins of a port that was closed before are not this port's. The markers it has "seen" start at 0, which is
     * right: its ring is empty, so catching up on the markers set before it opened writes nothing. */
    memset(&levels[id],0,sizeof levels[id]);
    pthread_mutex_init(&p->lock,NULL);
    p->used=1; p->type=type; p->channels=channels[format]; p->is_float=format>=3 && format!=6;
    p->sample_bytes=p->is_float ? 4 : 2; p->frames=(int)length; p->std_layout=format>=6;
    for (int c=0;c<8;++c) p->volume[c]=VOLUME_0DB;
    if (sdl_audio()) {
        SDL_AudioSpec device; int device_frames;
        p->downmix=p->channels==8 && SDL_GetAudioDeviceFormat(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK,&device,&device_frames)
                   && device.channels==2;
        SDL_AudioSpec spec={p->is_float ? SDL_AUDIO_F32 : SDL_AUDIO_S16, p->downmix ? 2 : p->channels, 48000};
        p->stream=OPEN_AUDIO_STREAM(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK,&spec,NULL,NULL);
#ifdef _WIN32
        if (!p->stream) try_audio_fallback(p, id);
#endif
        if (p->stream) SDL_ResumeAudioStreamDevice(p->stream);
        else fprintf(stderr,"Runtime: SDL audio stream failed (%s); port %d uses the timer sink\n",SDL_GetError(),id);
    }
    p->stats=getenv("SPARTAN_AUDIO_STATS")!=NULL;
    const char *dump=getenv("SPARTAN_AUDIO_DUMP");
    if (dump) {
        char path[4096];
        snprintf(path,sizeof(path),"%s.port%d.%dch.%s",dump,id,p->downmix ? 2 : p->channels,p->is_float ? "f32" : "s16");
        p->dump=fopen(path,"wb");
    }
    ++ports_opened;
    pthread_mutex_unlock(&table_lock);
    printf("Runtime: audio port %d opened (type %d, %d ch, %s, %u frames%s)\n",id,type,p->channels,p->is_float ? "float" : "s16",length,
           p->downmix ? ", downmixed to stereo" : "");
    runtime_audio_log("audioout.open port=%d type=%d(%s) channels=%d format=%s frames=%u stream=%d downmix=%d",id,type,port_type_name(type),
                      p->channels,p->is_float ? "float" : "s16",length,p->stream!=NULL,p->downmix);
    return (type<<16) | id | 0x20000000;
}
static ABI int32_t audio_close(int32_t handle) {
    pthread_mutex_lock(&table_lock);
    int32_t error=0;
    Port *p=port_of(handle,&error);
    if (p) {
        pthread_mutex_lock(&p->lock);
        level_flush((int)(p-ports),port_type_name(p->type));
        runtime_audio_log("audioout.close port=%d type=%s buffers=%llu loud=%llu",(int)(p-ports),port_type_name(p->type),
                          (unsigned long long)p->lg_total,(unsigned long long)p->lg_total_loud);
        if (p->stream) SDL_DestroyAudioStream(p->stream);
        if (p->dump) fclose(p->dump);
        p->dump=NULL;
        p->stream=NULL; p->used=0;
        pthread_mutex_unlock(&p->lock);
    }
    pthread_mutex_unlock(&table_lock);
    return p ? 0 : error;
}
/* `pace`: wait for this port's next period. sceAudioOutOutputs waits once for all its ports. */
static int32_t output_port(int32_t handle, const void *data, int pace) {
    int32_t error=0;
    pthread_mutex_lock(&table_lock);
    Port *p=port_of(handle,&error);
    pthread_mutex_unlock(&table_lock);
    if (!p) {
        static int reported; if (reported++<20) runtime_audio_log("audioout.output_failed handle=%#x error=%#x",(unsigned)handle,(unsigned)error);
        return error;
    }
    pthread_mutex_lock(&p->lock);
    size_t samples=(size_t)p->frames*(size_t)p->channels, bytes=samples*(size_t)p->sample_bytes;
    uint64_t period=(uint64_t)p->frames*1000000000u/48000u;
    if (data) {
        /* Apply per-channel volume and map PS4 8ch (L R C LFE SL SR BL BR) to SDL 7.1 order. */
        unsigned char converted[2048*8*4];
        static const int remap[8]={0,1,2,3,6,7,4,5};
        for (size_t f=0;f<(size_t)p->frames;++f) for (int c=0;c<p->channels;++c) {
            int target=p->channels==8 && !p->std_layout ? remap[c] : c;
            size_t from=f*(size_t)p->channels+(size_t)c, to=f*(size_t)p->channels+(size_t)target;
            float gain=(float)p->volume[c]/VOLUME_0DB;
            if (p->is_float) {
                float v; memcpy(&v,(const char *)data+from*4,4); v*=gain;
                if (!p->downmix) v=audio_soft_limit(v);
                memcpy(converted+to*4,&v,4);
            } else {
                int16_t v; memcpy(&v,(const char *)data+from*2,2);
                float fv=(float)v*gain;
                if (!p->downmix) fv=audio_soft_limit(fv/32768.0f)*32767.0f;
                int16_t out_v=(int16_t)fv;
                memcpy(converted+to*2,&out_v,2);
            }
        }
        if (p->downmix) bytes=downmix_stereo(converted,(size_t)p->frames,p->is_float);
        if (p->dump) fwrite(converted,1,bytes,p->dump);
        if (runtime_audio_log_enabled()) { /* level of what the port is given, after volume and downmix */
            float peak=0.0f; double sumsq=0.0; size_t count=bytes/(size_t)p->sample_bytes;
            for (size_t i=0;i<count;++i) {
                float v;
                if (p->is_float) memcpy(&v,converted+i*4,4); else { int16_t s; memcpy(&s,converted+i*2,2); v=(float)s/32768.0f; }
                if (v<0) v=-v;
                if (v>peak) peak=v;
                sumsq+=(double)v*(double)v;
            }
            level_feed((int)(p-ports),port_type_name(p->type),runtime_audio_log_ms(),peak,sumsq,(uint64_t)count);
            if (peak>p->lg_peak) p->lg_peak=peak;
            ++p->lg_buffers; ++p->lg_total;
            if (peak>0.001f) {
                ++p->lg_loud; ++p->lg_total_loud;
                if (!p->lg_sound_seen) { p->lg_sound_seen=1; runtime_audio_log("audioout.first_sound port=%d type=%s peak=%.4f",(int)(p-ports),port_type_name(p->type),peak); }
            }
        }
        if (p->stats) {
            uint64_t now=now_ns();
            if (!p->stat_start_ns) { p->stat_start_ns=now; p->stat_min_queued=1<<30; }
            else if (now-p->stat_last_ns>p->stat_max_gap_ns) p->stat_max_gap_ns=now-p->stat_last_ns;
            p->stat_last_ns=now; ++p->stat_buffers;
            if (p->stream) {
                int queued=SDL_GetAudioStreamQueued(p->stream);
                if (queued<p->stat_min_queued) p->stat_min_queued=queued;
                if (queued==0) ++p->stat_starved;
            }
            if (now-p->stat_start_ns>=5000000000u) {
                printf("Audio stats port %d: %u buffers/5s (expected %u), max gap %.2f ms, min queued %d bytes, starved %u\n",
                       (int)(p-ports),p->stat_buffers,(unsigned)(5u*48000u/(unsigned)p->frames),p->stat_max_gap_ns/1e6,
                       p->stat_min_queued,p->stat_starved);
                p->stat_start_ns=now; p->stat_max_gap_ns=0; p->stat_starved=0; p->stat_buffers=0; p->stat_min_queued=1<<30;
            }
        }
        if (pace) {
            uint64_t now=now_ns();
            static __thread uint64_t last_paced_ns;
            int should_sleep = 1;
            if (last_paced_ns && now - last_paced_ns < period / 2) {
                /* Same audio thread submitting multiple ports sequentially in the same grain: don't double sleep */
                should_sleep = 0;
            }
            if (!p->next_deadline_ns || now>p->next_deadline_ns+8*period) p->next_deadline_ns=now; /* start or stall: restart cadence */
            else if (should_sleep) sleep_until(p->next_deadline_ns);
            p->next_deadline_ns+=period;
            if (should_sleep) last_paced_ns = now_ns();
        }
        if (p->stream) {
            /* The device drains the queue in quanta (21 ms on PipeWire), so the level
               is a sawtooth: its minimum over ~32 buffers is what gets controlled. */
            int low=2*(int)bytes, queued=SDL_GetAudioStreamQueued(p->stream);
            if (queued<(int)bytes) {
                static const unsigned char silence[2048*8*4];
                SDL_PutAudioStreamData(p->stream,silence,low-queued<(int)bytes ? low-queued : (int)bytes);
                queued=low;
            }
            if (!p->window_count || queued<p->window_min) p->window_min=queued;
            if (++p->window_count==32) {
                p->adjust_ns=p->window_min>low+2*(int)bytes ? (int64_t)(period/32) : p->window_min<low ? -(int64_t)(period/32) : 0;
                p->window_count=0;
            }
            p->next_deadline_ns+=(uint64_t)p->adjust_ns;
            SDL_PutAudioStreamData(p->stream,converted,(int)bytes);
        }
        ++buffers_out;
    }
    if (!data) ++p->lg_null;
    if (!data && runtime_audio_log_enabled()) /* a buffer the game left out is silence: it counts in the bins */
        level_feed((int)(p-ports),port_type_name(p->type),runtime_audio_log_ms(),0.0f,0.0,0);
    if (runtime_audio_log_enabled()) {
        uint64_t now=now_ns();
        if (!p->lg_start_ns) p->lg_start_ns=now;
        else if (now-p->lg_start_ns>=5000000000u) {
            runtime_audio_log("audioout.stats port=%d type=%s buffers=%llu silent_calls=%llu loud=%llu peak=%.4f volume=%d/%d",(int)(p-ports),
                              port_type_name(p->type),(unsigned long long)p->lg_buffers,(unsigned long long)p->lg_null,
                              (unsigned long long)p->lg_loud,p->lg_peak,p->volume[0],p->volume[1]);
            p->lg_start_ns=now; p->lg_buffers=p->lg_null=p->lg_loud=0; p->lg_peak=0.0f;
        }
    }
    p->last_output_us=now_ns()/1000;
    pthread_mutex_unlock(&p->lock);
    return data ? (int32_t)samples : 0;
}
static ABI int32_t audio_output(int32_t handle, const void *data) { return output_port(handle,data,1); }
typedef struct { int32_t handle; const void *data; } OutputParam;
static ABI int32_t audio_outputs(const OutputParam *params, uint32_t count) {
    if (!params) return ERR_INVALID_POINTER;
    if (!count || count>PORTS) return ERR_PORT_FULL;
    int32_t result=0;
    /* One period per call for all ports (FMOD feeds main and BGM together): pacing every
       port would halve the rate and starve both. */
    for (uint32_t i=0;i<count;++i) { int32_t r=output_port(params[i].handle,params[i].data,i==0); if (r<0) return r; result=r; }
    return result;
}
static ABI int32_t audio_volume(int32_t handle, int32_t flags, const int32_t *volume) {
    int32_t error=0;
    pthread_mutex_lock(&table_lock);
    Port *p=port_of(handle,&error);
    pthread_mutex_unlock(&table_lock);
    if (!p) return error;
    if (!volume) return ERR_INVALID_POINTER;
    pthread_mutex_lock(&p->lock);
    for (int c=0;c<8;++c) if (flags & (1<<c)) {
        if (volume[c]<0 || volume[c]>VOLUME_0DB) {
            runtime_audio_log("audioout.volume_rejected port=%d channel=%d value=%d",(int)(p-ports),c,volume[c]);
            pthread_mutex_unlock(&p->lock); return ERR_INVALID_VOLUME;
        }
        p->volume[c]=volume[c];
    }
    { static int reported; if (reported++<400)
        runtime_audio_log("audioout.volume port=%d type=%s flags=%#x v0=%d v1=%d v2=%d v3=%d",(int)(p-ports),port_type_name(p->type),
                          (unsigned)flags,p->volume[0],p->volume[1],p->volume[2],p->volume[3]); }
    pthread_mutex_unlock(&p->lock);
    return 0;
}
static ABI int32_t audio_state(int32_t handle, PortState *state) {
    int32_t error=0;
    pthread_mutex_lock(&table_lock);
    Port *p=port_of(handle,&error);
    pthread_mutex_unlock(&table_lock);
    if (!p) return error;
    if (!state) return ERR_INVALID_POINTER;
    memset(state,0,sizeof(*state));
    switch (p->type) {
    case 2: case 3: state->output=0x40; state->channel=1; break;          /* headphone */
    case 4: state->output=0x04; state->channel=1; state->volume=127; break; /* pad speaker */
    default: state->output=0x01; state->channel=(uint8_t)(p->channels>2 ? 2 : p->channels); break;
    }
    return 0;
}
static ABI int32_t audio_last_time(int32_t handle, uint64_t *time) {
    int32_t error=0;
    pthread_mutex_lock(&table_lock);
    Port *p=port_of(handle,&error);
    pthread_mutex_unlock(&table_lock);
    if (!p) return error;
    if (!time) return ERR_INVALID_POINTER;
    *time=p->last_output_us; return 0;
}

static const RuntimeExport exports[]={
    {"sceAudioOutInit",audio_init}, {"sceAudioOutOpen",audio_open}, {"sceAudioOutClose",audio_close},
    {"sceAudioOutOutput",audio_output}, {"sceAudioOutOutputs",audio_outputs},
    {"sceAudioOutSetVolume",audio_volume}, {"sceAudioOutGetPortState",audio_state},
    {"sceAudioOutGetLastOutputTime",audio_last_time},
};
uintptr_t runtime_audio_resolve(const char *name) { return RUNTIME_LOOKUP(exports,name); }
void runtime_audio_report(void) { printf("Runtime: audio ports opened=%zu, buffers output=%zu\n",ports_opened,buffers_out); }
