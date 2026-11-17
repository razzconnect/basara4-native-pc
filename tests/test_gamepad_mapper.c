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
    SDL_CloseJoystick(joy);
    SDL_DetachVirtualJoystick(virtual_id);
    SDL_DestroyMutex(command_lock);
    SDL_Quit();
    puts("mapper-test: ok");
    return 0;
}
