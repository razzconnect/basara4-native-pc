/* SPDX-License-Identifier: GPL-2.0-or-later */
/* The level lines of the audio log as the game reaches them: through audio_open, audio_output (a buffer with sound
 * and a NULL one), runtime_audio_marker and audio_close, with the audio log ON (SPARTAN_AUDIO_DEBUG) and no audio
 * device (SPARTAN_AUDIO=none). audio-levels-test feeds the level functions directly; this one checks that the
 * production code feeds them: every buffer is counted, the sound has a level, the last bin of a port is written when
 * it closes, and a port that gets an id back does not carry the bins of the port that had it before. */
#define _GNU_SOURCE
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/runtime_audio.c"

uintptr_t runtime_lookup(const RuntimeExport *table, size_t count, const char *name) {
    (void)table; (void)count; (void)name;
    return 0;
}

static const char LOG_PATH[] = "audio_wiring_test.log";

static char *slurp(void) {
    FILE *f = fopen(LOG_PATH, "rb");
    assert(f);
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *text = calloc((size_t)n + 1, 1);
    assert(text && fread(text, 1, (size_t)n, f) == (size_t)n);
    fclose(f);
    return text;
}
static char *segment(const char *from, const char *to) {
    size_t n = to ? (size_t)(to - from) : strlen(from);
    char *s = malloc(n + 1);
    assert(s);
    memcpy(s, from, n);
    s[n] = 0;
    return s;
}
static int count_of(const char *text, const char *needle) {
    int n = 0;
    for (const char *p = text; (p = strstr(p, needle)); p += strlen(needle)) ++n;
    return n;
}
/* Sum of the integer after `field` on every line that starts with `kind port=<port> `. */
static unsigned long long sum_field(const char *seg, const char *kind, int port, const char *field) {
    char key[64];
    snprintf(key, sizeof key, "%s port=%d ", kind, port);
    unsigned long long total = 0;
    for (const char *p = seg; (p = strstr(p, key)); p += strlen(key)) {
        const char *end = strchr(p, '\n');
        const char *f = strstr(p, field);
        assert(f && (!end || f < end));
        total += strtoull(f + strlen(field), NULL, 10);
    }
    return total;
}
/* The t=<ms> values of the lines `kind port=<port> `: how many, the smallest, and whether one repeats. */
static int times_of(const char *seg, const char *kind, int port, unsigned long long *lowest, int *repeats) {
    char key[64];
    snprintf(key, sizeof key, "%s port=%d ", kind, port);
    unsigned long long seen[4096];
    int n = 0;
    *lowest = ~0ull;
    *repeats = 0;
    for (const char *p = seg; (p = strstr(p, key)); p += strlen(key)) {
        const char *t = strstr(p, " t=");
        assert(t && n < 4096);
        unsigned long long ms = strtoull(t + 3, NULL, 10);
        for (int i = 0; i < n; ++i) if (seen[i] == ms) *repeats = 1;
        seen[n++] = ms;
        if (ms < *lowest) *lowest = ms;
    }
    return n;
}
/* Every 1 s line that has loud buffers must show a level: a buffer with sound has an rms above zero. */
static int lines_with_sound_and_no_rms(const char *seg, int port) {
    char key[64];
    snprintf(key, sizeof key, "audio.level1s port=%d ", port);
    int bad = 0, with_sound = 0;
    for (const char *p = seg; (p = strstr(p, key)); p += strlen(key)) {
        const char *loud = strstr(p, "loud="), *rms = strstr(p, "rms=");
        assert(loud && rms);
        if (strtoull(loud + 5, NULL, 10) > 0) {
            ++with_sound;
            if (!(strtod(rms + 4, NULL) > 0.0)) ++bad;
        }
    }
    assert(with_sound > 0);
    return bad;
}

static void play(int32_t handle, int sound, int silent) {
    static float signal[256 * 8];
    for (int i = 0; i < 256 * 8; ++i) signal[i] = 0.5f * sinf((float)i * 0.05f);
    for (int i = 0; i < sound; ++i) {
        assert(audio_output(handle, signal) == 256 * 8);
        if (silent && i % (sound / silent) == sound / silent - 1) assert(audio_output(handle, NULL) == 0);
    }
}

int main(void) {
    remove(LOG_PATH);
    static char device[] = "SPARTAN_AUDIO=none", log[] = "SPARTAN_AUDIO_DEBUG=audio_wiring_test.log";
    putenv(device);
    putenv(log);
    assert(runtime_audio_log_enabled());
    assert(audio_init() == 0);

    /* Life 1: 30 buffers with sound and 5 the game left out, no marker. */
    int32_t first = audio_open(0, 0, 0, 256, 48000, 5);
    assert(first > 0);
    int id = first & 0xff;
    play(first, 30, 5);
    assert(audio_close(first) == 0);

    SDL_Delay(150); /* so the bins of life 2 start after the last one of life 1 */
    uint64_t second_start_ms = runtime_audio_log_ms();

    /* Life 2: the same id again; a marker in the middle of 10 buffers with sound. */
    int32_t second = audio_open(0, 0, 0, 256, 48000, 5);
    assert(second > 0 && (second & 0xff) == id);
    play(second, 5, 0);
    runtime_audio_marker("hotkey_f11");
    play(second, 5, 0);
    assert(audio_close(second) == 0);

    char *text = slurp();
    char key[48];
    snprintf(key, sizeof key, "audioout.open port=%d ", id);
    const char *open1 = strstr(text, key);
    assert(open1);
    const char *open2 = strstr(open1 + 1, key);
    assert(open2);
    char *one = segment(open1, open2), *two = segment(open2, NULL);
    unsigned long long lowest;
    int repeats;

    /* (a) every buffer is counted, the ones the game left out too; (b) the sound has a level */
    assert(sum_field(one, "audio.level1s", id, "buffers=") == 35);
    assert(sum_field(one, "audio.level1s", id, "loud=") == 30);
    assert(lines_with_sound_and_no_rms(one, id) == 0);
    assert(count_of(one, "capture.marker") == 0);
    assert(count_of(one, "audio.level port=") == 0); /* no 100 ms line without a marker */

    /* life 2: the marker, its window, and the last bin written when the port closed */
    assert(count_of(two, "capture.marker reason=hotkey_f11 epoch_ms=") == 1);
    assert(sum_field(two, "audio.level1s", id, "buffers=") == 10);
    assert(sum_field(two, "audio.level1s", id, "loud=") == 10);
    assert(lines_with_sound_and_no_rms(two, id) == 0);
    assert(sum_field(two, "audio.level", id, "buffers=") == 10); /* all of life 2 is inside the marker's window */
    assert(sum_field(two, "audio.level", id, "loud=") == 10);

    /* (c) a port that gets an id back has none of the bins of the port that had it, and none twice */
    assert(times_of(two, "audio.level", id, &lowest, &repeats) > 0);
    assert(lowest >= second_start_ms / 100 * 100);
    assert(!repeats);
    assert(times_of(two, "audio.level1s", id, &lowest, &repeats) > 0);
    assert(lowest >= second_start_ms / 1000 * 1000);
    assert(!repeats);

    free(one);
    free(two);
    free(text);
    remove(LOG_PATH);
    puts("audio wiring: ok");
    return 0;
}
