/* SPDX-License-Identifier: GPL-2.0-or-later */
/* The audio log's level lines (src/runtime_audio.c): epoch in log.start, audio.level1s for the whole session,
 * audio.level (100 ms) only around an F11 marker, capture.marker. Time is passed in, so nothing here waits. */
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

static const char LOG_PATH[] = "audio_levels_test.log";

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
static int count_of(const char *text, const char *needle) {
    int n = 0;
    for (const char *p = text; (p = strstr(p, needle)); p += strlen(needle)) ++n;
    return n;
}
/* Every number t=<ms> of the lines that start with `kind port=<port> `: smallest, largest, how many. */
static int times_of(const char *text, const char *kind, int port, unsigned long long *lo, unsigned long long *hi) {
    char key[64];
    snprintf(key, sizeof key, "%s port=%d ", kind, port);
    int n = 0;
    *lo = ~0ull; *hi = 0;
    for (const char *p = text; (p = strstr(p, key)); p += strlen(key)) {
        const char *t = strstr(p, " t=");
        assert(t);
        unsigned long long ms = strtoull(t + 3, NULL, 10);
        if (ms < *lo) *lo = ms;
        if (ms > *hi) *hi = ms;
        ++n;
    }
    return n;
}
static void reset(void) {
    memset(levels, 0, sizeof levels);
    __atomic_store_n(&marker_count, 0, __ATOMIC_RELEASE);
}
/* A port at 48 kHz gives a 256-frame stereo buffer about every 5 ms; every 20 ms is enough for the bins. */
static void feed(int port, uint64_t from_ms, uint64_t to_ms, float peak) {
    for (uint64_t ms = from_ms; ms < to_ms; ms += 20)
        level_feed(port, "main", ms, peak, (double)peak * peak * 512.0, 512);
}

static void test_log_start_has_the_epoch(void) {
    char *text = slurp();
    const char *start = strstr(text, "log.start format=1 audio_mode=");
    assert(start);
    const char *epoch = strstr(start, " epoch_ms=");
    assert(epoch && epoch < strchr(start, '\n'));
    unsigned long long ms = strtoull(epoch + 10, NULL, 10);
    unsigned long long now = (unsigned long long)time(NULL) * 1000ull;
    assert(ms + 60000ull > now && ms < now + 60000ull); /* wall-clock time of the log's 0 ms */
    free(text);
}

static void test_one_second_lines_cover_the_session_and_there_is_no_100ms_line_without_a_marker(void) {
    reset();
    feed(3, 0, 2500, 0.5f);
    level_flush(3, "main");
    char *text = slurp();
    unsigned long long lo, hi;
    assert(times_of(text, "audio.level1s", 3, &lo, &hi) == 3); /* seconds 0, 1 and the part of 2 */
    assert(lo == 0 && hi == 2000);
    assert(strstr(text, "audio.level1s port=3 type=main t=0 peak=0.5000 rms=0.5000 buffers=50 loud=50"));
    assert(count_of(text, "audio.level port=3 ") == 0);
    free(text);
}

static void test_silent_buffers_are_counted_as_silence(void) {
    reset();
    feed(4, 0, 1000, 0.0f);
    level_feed(4, "main", 1000, 0.0f, 0.0, 0); /* a buffer the game left out */
    level_flush(4, "main");
    char *text = slurp();
    assert(strstr(text, "audio.level1s port=4 type=main t=0 peak=0.0000 rms=0.0000 buffers=50 loud=0"));
    assert(strstr(text, "audio.level1s port=4 type=main t=1000 peak=0.0000 rms=0.0000 buffers=1 loud=0"));
    free(text);
}

static void test_a_marker_writes_the_100ms_bins_15_seconds_around_it_and_only_those(void) {
    reset();
    feed(6, 0, 20000, 0.25f);
    audio_marker_at(20000, 1234567890123ull, "hotkey_f11");
    feed(6, 20000, 40000, 0.25f);
    level_flush(6, "main");
    char *text = slurp();
    unsigned long long lo, hi;
    int n = times_of(text, "audio.level", 6, &lo, &hi);
    assert(lo == 5000 && hi == 35000); /* 15 s before and 15 s after the marker at 20 s */
    assert(n == (35000 - 5000) / 100 + 1);
    assert(strstr(text, "capture.marker reason=hotkey_f11 epoch_ms=1234567890123 window_s=15"));
    assert(strstr(text, "audio.level port=6 type=main t=5000 peak=0.2500 rms=0.2500 buffers=5 loud=5"));
    free(text);
}

static void test_two_markers_near_each_other_write_every_bin_once(void) {
    reset();
    feed(7, 0, 30000, 0.1f);
    audio_marker_at(30000, 1, "hotkey_f11");
    feed(7, 30000, 36000, 0.1f);
    audio_marker_at(36000, 2, "hotkey_f11");
    feed(7, 36000, 70000, 0.1f);
    level_flush(7, "main");
    char *text = slurp();
    unsigned long long lo, hi;
    int n = times_of(text, "audio.level", 7, &lo, &hi);
    assert(lo == 15000 && hi == 51000);
    assert(n == (51000 - 15000) / 100 + 1); /* no bin twice, none missing */
    free(text);
}

static void test_ports_keep_their_own_bins(void) {
    reset();
    feed(8, 0, 3000, 0.2f);
    feed(9, 0, 3000, 0.9f);
    audio_marker_at(3000, 3, "hotkey_f11");
    feed(8, 3000, 4000, 0.2f);
    feed(9, 3000, 4000, 0.9f);
    level_flush(8, "main");
    level_flush(9, "main");
    char *text = slurp();
    assert(strstr(text, "audio.level port=8 type=main t=0 peak=0.2000"));
    assert(strstr(text, "audio.level port=9 type=main t=0 peak=0.9000"));
    free(text);
}

static void test_a_marker_before_any_audio_writes_nothing_until_audio_comes(void) {
    reset();
    audio_marker_at(500, 4, "hotkey_f11");
    level_flush(10, "main"); /* a port with no buffers: nothing to write, nothing breaks */
    char *text = slurp();
    assert(count_of(text, "audio.level port=10 ") == 0);
    free(text);
}

static void test_more_markers_than_the_table_holds_keep_the_newest(void) {
    reset();
    feed(11, 0, 60000, 0.3f);
    for (unsigned i = 0; i < 3 * LVL_MARKERS; ++i) audio_marker_at(60000 + i, i, "hotkey_f11");
    feed(11, 60000, 61000, 0.3f);
    level_flush(11, "main");
    char *text = slurp();
    unsigned long long lo, hi;
    assert(times_of(text, "audio.level", 11, &lo, &hi) > 0);
    assert(lo >= 45000 && hi <= 76000);
    free(text);
}

static void test_the_marker_function_writes_a_line_with_the_wall_clock(void) {
    reset();
    char *earlier = slurp(); /* the log keeps the markers the other tests set */
    int before = count_of(earlier, "capture.marker reason=hotkey_f11 epoch_ms=");
    free(earlier);
    runtime_audio_marker("hotkey_f11");
    runtime_audio_marker(NULL);
    char *text = slurp();
    assert(count_of(text, "capture.marker reason=hotkey_f11 epoch_ms=") == before + 1);
    assert(strstr(text, "capture.marker reason=? epoch_ms="));
    assert(__atomic_load_n(&marker_count, __ATOMIC_ACQUIRE) == 2);
    free(text);
}

int main(void) {
    remove(LOG_PATH);
    static char env[] = "SPARTAN_AUDIO_DEBUG=audio_levels_test.log";
    putenv(env);
    assert(runtime_audio_log_enabled());
    test_log_start_has_the_epoch();
    test_one_second_lines_cover_the_session_and_there_is_no_100ms_line_without_a_marker();
    test_silent_buffers_are_counted_as_silence();
    test_a_marker_writes_the_100ms_bins_15_seconds_around_it_and_only_those();
    test_two_markers_near_each_other_write_every_bin_once();
    test_ports_keep_their_own_bins();
    test_a_marker_before_any_audio_writes_nothing_until_audio_comes();
    test_more_markers_than_the_table_holds_keep_the_newest();
    test_the_marker_function_writes_a_line_with_the_wall_clock();
    remove(LOG_PATH);
    puts("audio levels: ok");
    return 0;
}
