/* Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (LICENSE, NOTICE.md). */
/* The controller mapper (tools/gamepad_mapper.c) with an SDL3 virtual joystick: a stick prompt takes the
 * axis and direction of the first push, a rotated or diagonal push is asked again, an input another
 * entry has is not taken twice, flip turns an axis around, and "~" in a mapping really reverses the
 * axis the game reads. Saving keeps other controllers and never leaves a half-written file. */
#pragma clang diagnostic ignored "-Wunused-function"
#define MAPPER_NO_MAIN
#include "../tools/gamepad_mapper.c"
#include <assert.h>

/* A scripted stick: phases of (polls, axis 0..3 values). The last phase lasts. */
typedef struct { int polls; Sint16 axis[4]; } Phase;
static const Phase *script;
static int script_phases, script_step;
static SDL_JoystickID virtual_id;

/* A capture that keeps asking again would wait forever on the last phase: fail instead (about 10 s). */
enum { kScriptPollLimit = 1000 };

static void drive(void) {
    if (script_step >= kScriptPollLimit) {
        fprintf(stderr, "mapper-test: the capture never finished (asked again %d times)\n", stick_rejections);
        abort();
    }
    int step = script_step++, p = 0;
    while (p + 1 < script_phases && step >= script[p].polls) step -= script[p++].polls;
    for (int a = 0; a < 4; ++a) SDL_SetJoystickVirtualAxis(joy, a, script[p].axis[a]);
}

static void play(const Phase *phases, int count) {
    script = phases;
    script_phases = count;
    script_step = 0;
}

static void reset_bindings(void) {
    memset(bound, 0, sizeof bound);
    kept_count = 0;
    stick_rejections = 0;
}

static int run_capture(const char *target, char *out) {
    return capture(target_index(target), out, BINDING_SIZE);
}

static void test_verdict(void) {
    int all_free[4] = {1, 1, 1, 1};
    AxisRange right[4] = {{-500, 30000}, {-3000, 4000}, {0, 0}, {0, 0}};
    assert(stick_verdict(right, 4, all_free, 0, 0) == STICK_OK);
    AxisRange left[4] = {{-30000, 400}, {-2000, 1000}, {0, 0}, {0, 0}};
    assert(stick_verdict(left, 4, all_free, 0, 1) == STICK_OK);
    AxisRange rotated[4] = {{-15000, 30000}, {-9000, 9000}, {0, 0}, {0, 0}};
    assert(stick_verdict(rotated, 4, all_free, 0, 0) == STICK_ROTATED);
    AxisRange diagonal[4] = {{0, 30000}, {0, 22000}, {0, 0}, {0, 0}};
    assert(stick_verdict(diagonal, 4, all_free, 0, 0) == STICK_TWO_AXES);
    AxisRange wobble[4] = {{0, 30000}, {-10000, 0}, {0, 0}, {0, 0}};
    assert(stick_verdict(wobble, 4, all_free, 0, 0) == STICK_OK);  /* a small sideways wobble is fine */
    int x_taken[4] = {0, 1, 1, 1};                                   /* axes another entry has do not count */
    AxisRange down_with_x[4] = {{0, 26000}, {0, 30000}, {0, 0}, {0, 0}};
    assert(stick_verdict(down_with_x, 4, x_taken, 1, 0) == STICK_OK);
}

static void test_capture_with_a_virtual_stick(void) {
    char out[BINDING_SIZE];
    mapper_poll_hook = drive;
    /* Straight push right: leftx = a0. */
    reset_bindings();
    const Phase right[] = {{3, {0, 0, 0, 0}}, {1, {30000, 2000, 0, 0}}};
    play(right, 2);
    assert(run_capture("leftx", out) == CAP_OK && !strcmp(out, "a0"));
    assert(stick_rejections == 0);
    /* Push right, then swing far left within the same push: rejected and asked again; the second, straight
     * push decides. The old "largest peak" rule stored a0~ here. */
    reset_bindings();
    const Phase swing[] = {{3, {0, 0, 0, 0}}, {10, {30000, 0, 0, 0}}, {100, {-32000, 0, 0, 0}},
                           {40, {0, 0, 0, 0}}, {1, {30000, 0, 0, 0}}};
    play(swing, 5);
    assert(run_capture("leftx", out) == CAP_OK && !strcmp(out, "a0"));
    assert(stick_rejections == 1);
    /* Diagonal with the vertical axis moving furthest: rejected, never leftx = a1 (axes swapped). */
    reset_bindings();
    const Phase diagonal[] = {{3, {0, 0, 0, 0}}, {100, {21000, 30000, 0, 0}}, {40, {0, 0, 0, 0}},
                              {1, {30000, 0, 0, 0}}};
    play(diagonal, 4);
    assert(run_capture("leftx", out) == CAP_OK && !strcmp(out, "a0"));
    assert(stick_rejections == 1);
    /* lefty pushed up (negative): the sign of that push is kept, so the binding says so (a1~). */
    reset_bindings();
    snprintf(bound[target_index("leftx")], BINDING_SIZE, "a0");
    const Phase up[] = {{3, {0, 0, 0, 0}}, {1, {3000, -30000, 0, 0}}};
    play(up, 2);
    assert(run_capture("lefty", out) == CAP_OK && !strcmp(out, "a1~"));
    /* lefty with only the axis leftx already has: reported as used, asked again, then a1. */
    reset_bindings();
    snprintf(bound[target_index("leftx")], BINDING_SIZE, "a0");
    const Phase wrong_axis[] = {{3, {0, 0, 0, 0}}, {20, {30000, 0, 0, 0}}, {40, {0, 0, 0, 0}},
                                {1, {0, 30000, 0, 0}}};
    play(wrong_axis, 4);
    assert(run_capture("lefty", out) == CAP_OK && !strcmp(out, "a1"));
    /* Rest is taken again before each prompt: a right stick resting off centre still reads its push. */
    reset_bindings();
    const Phase drifted[] = {{1, {0, 0, 9000, 0}}};
    play(drifted, 1);
    sample_rest();
    const Phase push_right_stick[] = {{3, {0, 0, 9000, 0}}, {1, {0, 0, -25000, 0}}};
    play(push_right_stick, 2);
    assert(run_capture("rightx", out) == CAP_OK && !strcmp(out, "a2~"));
    /* An axis that rests at one end (a trigger on some DirectInput pads) is its rest, not a push: with the
     * rest taken again it is ignored and the stick that really moved is stored. */
    reset_bindings();
    play((const Phase[]){{1, {0, 0, 0, -32768}}}, 1);
    sample_rest();
    const Phase trigger_at_rest[] = {{3, {0, 0, 0, -32768}}, {1, {0, 0, 30000, -32768}}};
    play(trigger_at_rest, 2);
    assert(run_capture("rightx", out) == CAP_OK && !strcmp(out, "a2"));
    assert(stick_rejections == 0);
    play((const Phase[]){{1, {0, 0, 0, 0}}}, 1);
    sample_rest();
    mapper_poll_hook = NULL;
}

static void test_flip_and_mapping_line(void) {
    reset_bindings();
    const int leftx = target_index("leftx"), a = target_index("a");
    snprintf(bound[a], BINDING_SIZE, "b0");
    snprintf(bound[leftx], BINDING_SIZE, "a0");
    assert(flip_binding(leftx) && !strcmp(bound[leftx], "a0~"));
    assert(flip_binding(leftx) && !strcmp(bound[leftx], "a0"));
    assert(!flip_binding(a) && !strcmp(bound[a], "b0"));  /* buttons are not flipped */
    char line[512];
    assert(build_mapping(line, sizeof line, "0300aaaa", "Pad"));
    assert(!strcmp(line, "0300aaaa,Pad,a:b0,leftx:a0,platform:Windows,"));
    /* --sticks keeps every non-stick field of the saved line and treats those inputs as taken. */
    reset_bindings();
    assert(keep_buttons("0300aaaa,Pad,a:b0,b:b1,leftx:a3,lefty:a4~,platform:Windows,\n"));
    assert(kept_count == 2 && !strcmp(kept[0], "a:b0") && !strcmp(kept[1], "b:b1"));
    assert(binding_taken("b1", -1) && !binding_taken("a3", -1));
    snprintf(bound[leftx], BINDING_SIZE, "a0~");
    assert(build_mapping(line, sizeof line, "0300aaaa", "Pad"));
    assert(!strcmp(line, "0300aaaa,Pad,a:b0,b:b1,leftx:a0~,platform:Windows,"));
}

/* "~" in a mapping turns the axis the game reads (SDL_GetGamepadAxis) around: flip really flips. */
static void test_tilde_reverses_the_axis_the_game_reads(void) {
    char guid[64];
    SDL_GUIDToString(SDL_GetJoystickGUIDForID(virtual_id), guid, sizeof guid);
    SDL_Gamepad *pad = NULL;
    for (int reversed = 0; reversed < 2; ++reversed) {
        char mapping[256];
        snprintf(mapping, sizeof mapping, "%s,Basara test pad,a:b0,leftx:a0%s,lefty:a1,platform:%s,", guid,
                 reversed ? "~" : "", SDL_GetPlatform());
        assert(SDL_AddGamepadMapping(mapping) >= 0);
        if (!pad) pad = SDL_OpenGamepad(virtual_id);
        assert(pad);
        SDL_SetJoystickVirtualAxis(joy, 0, 30000);
        SDL_UpdateJoysticks();
        const Sint16 x = SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_LEFTX);
        assert(reversed ? x < -20000 : x > 20000);
    }
    SDL_SetJoystickVirtualAxis(joy, 0, 0);
    SDL_UpdateJoysticks();
    SDL_CloseGamepad(pad);
}

/* Saving replaces only this controller's line, keeps the others, and a failed save leaves the old file. */
static void test_save_keeps_other_controllers(void) {
    char dir[512], path[600], temp[620];
    const char *base = getenv("TEMP");
    snprintf(dir, sizeof dir, "%s/basara4-mapper-test-%llu/", base ? base : ".",
             (unsigned long long)SDL_GetTicksNS());
    assert(SDL_CreateDirectory(dir));
    snprintf(path, sizeof path, "%sgamepad_mapping.txt", dir);
    snprintf(temp, sizeof temp, "%s.tmp", path);
    FILE *f = fopen(path, "w");
    assert(f);
    fputs("0300bbbb,Other,a:b3,platform:Windows,\n0300aaaa,Pad,a:b9,platform:Windows,\n", f);
    fclose(f);
    MappingFile file = {0};
    assert(read_mapping_file(path, &file) && file.count == 2);
    assert(save_mapping_file(path, &file, "0300aaaa", "0300aaaa,Pad,a:b0,platform:Windows,"));
    free_mapping_file(&file);
    char text[512] = {0};
    f = fopen(path, "r");
    assert(f);
    const size_t length = fread(text, 1, sizeof text - 1, f);
    fclose(f);
    text[length] = 0;
    assert(!strcmp(text, "0300bbbb,Other,a:b3,platform:Windows,\n0300aaaa,Pad,a:b0,platform:Windows,\n"));
    assert(!SDL_GetPathInfo(temp, NULL));  /* no temporary file left */
    /* The temporary file cannot be written (a folder has its name): nothing changes. */
    assert(SDL_CreateDirectory(temp));
    assert(read_mapping_file(path, &file));
    assert(!save_mapping_file(path, &file, "0300aaaa", "0300aaaa,Pad,a:b7,platform:Windows,"));
    free_mapping_file(&file);
    f = fopen(path, "r");
    assert(f);
    char after[512] = {0};
    after[fread(after, 1, sizeof after - 1, f)] = 0;
    fclose(f);
    assert(!strcmp(after, text));
    SDL_RemovePath(temp);
    SDL_RemovePath(path);
    SDL_RemovePath(dir);
}

/* --ui review: commands from the launcher flip a stick and save. */
static void test_review_commands(void) {
    reset_bindings();
    ui_mode = 1;
    snprintf(bound[target_index("leftx")], BINDING_SIZE, "a0");
    queue_command("flip lefty");   /* not set: an error, the review goes on */
    queue_command("flip leftx");
    queue_command("save");
    assert(review("0300aaaa", "Pad", 0) == 1);
    assert(!strcmp(bound[target_index("leftx")], "a0~"));
    queue_command("cancel");
    assert(review("0300aaaa", "Pad", 0) == 0);
    ui_mode = 0;
}

/* ---- stdout of the mapper (--ui events) into a temporary file ---- */
#ifdef _WIN32
#include <io.h>
#define TEST_DUP _dup
#define TEST_DUP2 _dup2
#define TEST_CLOSE _close
#define TEST_FILENO _fileno
#else
#include <unistd.h>
#define TEST_DUP dup
#define TEST_DUP2 dup2
#define TEST_CLOSE close
#define TEST_FILENO fileno
#endif
static int saved_stdout = -1;
static char events_path[600];

static void events_begin(void) {
    const char *base = getenv("TEMP");
    snprintf(events_path, sizeof events_path, "%s/basara4-mapper-events-%llu.txt", base ? base : ".",
             (unsigned long long)SDL_GetTicksNS());
    fflush(stdout);
    saved_stdout = TEST_DUP(TEST_FILENO(stdout));
    FILE *sink = fopen(events_path, "wb");
    assert(saved_stdout >= 0 && sink);
    TEST_DUP2(TEST_FILENO(sink), TEST_FILENO(stdout));
    fclose(sink);
}

/* Restores stdout; returns what the mapper wrote (free() it). */
static char *events_end(void) {
    fflush(stdout);
    TEST_DUP2(saved_stdout, TEST_FILENO(stdout));
    TEST_CLOSE(saved_stdout);
    FILE *f = fopen(events_path, "rb");
    assert(f);
    fseek(f, 0, SEEK_END);
    const long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *text = malloc((size_t)size + 1);
    assert(text);
    text[fread(text, 1, (size_t)size, f)] = 0;
    fclose(f);
    remove(events_path);
    return text;
}

static int count_of(const char *text, const char *what) {
    int n = 0;
    for (const char *p = strstr(text, what); p; p = strstr(p + 1, what)) ++n;
    return n;
}

/* Live lines are limited in rate and only sent when a value moved. */
static void test_live_rate_and_change(void) {
    ui_mode = 1;
    events_begin();
    live_reset();
    for (int i = 0; i < 200; ++i) emit_live("leftx", "push", 5000, "side", 0);  /* the stick holds still */
    emit_live("leftx", "push", 30000, "side", 0);  /* moved, but within 33 ms of the last line */
    char *text = events_end();
    assert(count_of(text, "\"ev\":\"live\"") == 1);  /* one line for 201 polls */
    assert(strstr(text, "{\"ev\":\"live\",\"target\":\"leftx\",\"push\":5000,\"side\":0}"));
    free(text);
    events_begin();
    SDL_Delay(kLiveIntervalMs + 15);
    emit_live("leftx", "push", 30000, "side", 0);  /* now it is sent */
    SDL_Delay(kLiveIntervalMs + 15);
    emit_live("leftx", "push", 30100, "side", 0);  /* below kLiveStep: not sent, also when time allows */
    text = events_end();
    ui_mode = 0;
    assert(count_of(text, "\"ev\":\"live\"") == 1);
    assert(strstr(text, "{\"ev\":\"live\",\"target\":\"leftx\",\"push\":30000,\"side\":0}"));
    free(text);
}

/* A stick prompt sends the player's own stick while it moves: rising push values, about 30 a second. */
static void test_live_during_a_stick_prompt(void) {
    char out[BINDING_SIZE];
    reset_bindings();
    mapper_poll_hook = drive;
    Phase ramp[34];
    ramp[0] = (Phase){5, {0, 0, 0, 0}};
    for (int i = 1; i < 33; ++i) ramp[i] = (Phase){3, {(Sint16)(i * 900), 0, 0, 0}};
    ramp[33] = (Phase){1, {30000, 0, 0, 0}};
    play(ramp, 34);
    ui_mode = 1;
    events_begin();
    const Uint64 start = SDL_GetTicks();
    const int result = run_capture("leftx", out);
    const Uint64 elapsed = SDL_GetTicks() - start;
    char *text = events_end();
    ui_mode = 0;
    mapper_poll_hook = NULL;
    assert(result == CAP_OK && !strcmp(out, "a0"));
    const int lines = count_of(text, "\"ev\":\"live\",\"target\":\"leftx\"");
    assert(lines >= 5);
    assert(lines <= (int)(elapsed / kLiveIntervalMs) + 2);  /* never more than one per interval */
    int last = -1, rising = 1;
    for (const char *p = strstr(text, "\"push\":"); p; p = strstr(p + 1, "\"push\":")) {
        const int push = atoi(p + 7);
        rising &= push >= last;
        last = push;
    }
    assert(rising && last >= 29000);
    free(text);
}

/* Commands the launcher would send while the review polls the check: (poll step, command). */
typedef struct { int step; const char *command; } Scheduled;
static const Scheduled *schedule;
static int schedule_count, schedule_next;
static void drive_and_queue(void) {
    drive();
    while (schedule_next < schedule_count && script_step >= schedule[schedule_next].step)
        queue_command(schedule[schedule_next++].command);
}

static void live_xy(const char *line, int *x, int *y) {
    const char *px = strstr(line, "\"x\":"), *py = strstr(line, "\"y\":");
    assert(px && py);
    *x = atoi(px + 4);
    *y = atoi(py + 4);
}

/* The stick check reads the stick through SDL with the recorded mapping, as the game does: "~" turns
 * the value around, and a flip changes what the check shows right away. */
static void test_stick_check_shows_what_the_game_reads(void) {
    char guid[64];
    SDL_GUIDToString(SDL_GetJoystickGUIDForID(virtual_id), guid, sizeof guid);
    reset_bindings();
    snprintf(bound[target_index("leftx")], BINDING_SIZE, "a0");
    snprintf(bound[target_index("lefty")], BINDING_SIZE, "a1~");
    play((const Phase[]){{1, {30000, -30000, 0, 0}}}, 1);  /* raw: x to the right, y pushed up */
    static const Scheduled commands[] = {{25, "flip lefty"}, {60, "save"}};
    schedule = commands;
    schedule_count = 2;
    schedule_next = 0;
    mapper_poll_hook = drive_and_queue;
    ui_mode = 1;
    queue_command("check middle");  /* unknown: an error, the review goes on */
    queue_command("check left");
    events_begin();
    const int saved = review(guid, "Basara test pad", 0);
    char *text = events_end();
    ui_mode = 0;
    mapper_poll_hook = NULL;
    assert(saved == 1);
    assert(!strcmp(bound[target_index("lefty")], "a1"));
    assert(strstr(text, "\"message\":\"Unknown command.\""));
    /* live lines for the left stick: before the flip y is turned around by "~" (raw up -> positive,
     * which SDL calls down); after the flip the game reads up (negative) */
    const char *flipped = strstr(text, "\"ev\":\"flipped\"");
    assert(flipped);
    int before = 0, after = 0;
    for (const char *p = strstr(text, "\"ev\":\"live\",\"target\":\"left\""); p;
         p = strstr(p + 1, "\"ev\":\"live\",\"target\":\"left\"")) {
        int x, y;
        live_xy(p, &x, &y);
        assert(x > 20000);
        if (p < flipped) {
            assert(y > 20000);
            ++before;
        } else {
            assert(y < -20000);
            ++after;
        }
    }
    assert(before >= 1 && after >= 1);
    assert(!check_pad && check_side < 0);  /* saving ends the check */
    free(text);
    /* "check right" with the right stick not set: the game reads the centre */
    reset_bindings();
    static const Scheduled cancel_later[] = {{10, "cancel"}};
    schedule = cancel_later;
    schedule_count = 1;
    schedule_next = 0;
    play((const Phase[]){{1, {30000, -30000, 30000, 30000}}}, 1);
    mapper_poll_hook = drive_and_queue;
    ui_mode = 1;
    queue_command("check right");
    events_begin();
    assert(review(guid, "Basara test pad", 0) == 0);
    text = events_end();
    ui_mode = 0;
    mapper_poll_hook = NULL;
    const char *line = strstr(text, "\"ev\":\"live\",\"target\":\"right\"");
    assert(line);
    int x, y;
    live_xy(line, &x, &y);
    assert(x == 0 && y == 0);
    free(text);
    play((const Phase[]){{1, {0, 0, 0, 0}}}, 1);
    drive();
    SDL_UpdateJoysticks();
}

/* After a rejected push the rest values are taken again: an axis that settled off centre while the
 * player let go still reads its next push from where it rests now. */
static void test_rest_is_taken_again_after_a_rejected_push(void) {
    char out[BINDING_SIZE];
    reset_bindings();
    mapper_poll_hook = drive;
    const Phase settle[] = {{3, {0, 0, 0, 0}}, {10, {0, 0, 30000, 0}}, {100, {0, 0, -32000, 0}},
                            {60, {0, 0, 9000, 0}}, {1, {0, 0, -14000, 0}}};
    play(settle, 5);
    /* swung (rejected), then it rests at 9000: the push to -14000 is 23000 from there, but only 14000
     * from the old rest, which is not a push at all */
    assert(run_capture("rightx", out) == CAP_OK && !strcmp(out, "a2~"));
    assert(stick_rejections == 1);
    play((const Phase[]){{1, {0, 0, 0, 0}}}, 1);
    sample_rest();
    mapper_poll_hook = NULL;
}

/* A button that is pressed again every few polls (a used input pressed again and again). */
static int toggle_polls;
static void press_button_again_and_again(void) {
    SDL_SetJoystickVirtualButton(joy, 0, (toggle_polls++ % 40) < 5);
    if (toggle_polls > 2000) {
        fprintf(stderr, "mapper-test: the prompt never ended while a used button was pressed\n");
        abort();
    }
}

/* The prompt deadline: pressing a used button again and again does not keep a prompt open; a rejected
 * stick push gives the player the full time again for the new push. */
static void test_prompt_deadline(void) {
    char out[BINDING_SIZE];
    prompt_timeout_ms = 600;
    reset_bindings();
    snprintf(bound[target_index("a")], BINDING_SIZE, "b0");
    toggle_polls = 0;
    mapper_poll_hook = press_button_again_and_again;
    const Uint64 start = SDL_GetTicks();
    assert(run_capture("b", out) == CAP_TIMEOUT);
    assert(SDL_GetTicks() - start < 600 + 1500);  /* the deadline plus at most one release wait */
    SDL_SetJoystickVirtualButton(joy, 0, 0);
    /* rejected after the first deadline has passed (the 700 ms watch), then a straight push: kept */
    reset_bindings();
    mapper_poll_hook = drive;
    const Phase late[] = {{3, {0, 0, 0, 0}}, {10, {30000, 0, 0, 0}}, {100, {-32000, 0, 0, 0}},
                          {40, {0, 0, 0, 0}}, {1, {30000, 0, 0, 0}}};
    play(late, 5);
    assert(run_capture("leftx", out) == CAP_OK && !strcmp(out, "a0"));
    assert(stick_rejections == 1);
    play((const Phase[]){{1, {0, 0, 0, 0}}}, 1);
    sample_rest();
    mapper_poll_hook = NULL;
    prompt_timeout_ms = 15000;
}

/* The launcher closing its input reads as cancel: in a prompt and in the review. */
static void test_end_of_input_cancels(void) {
    char path[600];
    const char *base = getenv("TEMP");
    snprintf(path, sizeof path, "%s/basara4-mapper-eof-%llu.txt", base ? base : ".",
             (unsigned long long)SDL_GetTicksNS());
    FILE *f = fopen(path, "wb");
    assert(f);
    fclose(f);
    assert(freopen(path, "rb", stdin));
    command_eof = 0;
    read_commands(NULL);  /* the real reader: empty input, then end of input */
    char command[COMMAND_SIZE];
    assert(next_command(command, sizeof command) && !strcmp(command, "cancel"));
    char out[BINDING_SIZE];
    reset_bindings();
    ui_mode = 1;
    play((const Phase[]){{1, {0, 0, 0, 0}}}, 1);
    mapper_poll_hook = drive;
    assert(run_capture("leftx", out) == CAP_CANCEL);
    events_begin();
    assert(review("0300aaaa", "Pad", 0) == 0);
    free(events_end());
    ui_mode = 0;
    mapper_poll_hook = NULL;
    command_eof = 0;
    remove(path);
}

int main(void) {
    test_verdict();
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    assert(SDL_Init(SDL_INIT_JOYSTICK | SDL_INIT_GAMEPAD));
    command_lock = SDL_CreateMutex();
    SDL_VirtualJoystickDesc desc;
    SDL_INIT_INTERFACE(&desc);
    desc.type = SDL_JOYSTICK_TYPE_GAMEPAD;
    desc.naxes = 4;
    desc.nbuttons = 4;
    desc.nhats = 1;
    desc.name = "Basara test pad";
    virtual_id = SDL_AttachVirtualJoystick(&desc);
    assert(virtual_id);
    joy = SDL_OpenJoystick(virtual_id);
    assert(joy);
    axis_count = SDL_GetNumJoystickAxes(joy);
    assert(axis_count == 4);
    sample_rest();
    test_capture_with_a_virtual_stick();
    test_flip_and_mapping_line();
    test_tilde_reverses_the_axis_the_game_reads();
    test_save_keeps_other_controllers();
    test_review_commands();
    test_live_rate_and_change();
    test_live_during_a_stick_prompt();
    test_stick_check_shows_what_the_game_reads();
    test_rest_is_taken_again_after_a_rejected_push();
    test_prompt_deadline();
    test_end_of_input_cancels();  /* last: it replaces stdin */
    SDL_CloseJoystick(joy);
    SDL_DetachVirtualJoystick(virtual_id);
    SDL_DestroyMutex(command_lock);
    SDL_Quit();
    puts("mapper-test: ok");
    return 0;
}
