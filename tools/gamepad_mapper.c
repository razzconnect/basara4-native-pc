/* Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (LICENSE, NOTICE.md). */
/* Interactive SDL3 gamepad mapper. Asks for each PlayStation button/stick in turn, records the
 * selected joystick input, and keeps mappings for other GUIDs in the same file.
 * Without --ui it talks to a console. With --ui (the launcher's Controller window) it writes one JSON
 * object per line on stdout and reads one command per line on stdin, so no console is needed. */
#include <SDL3/SDL.h>
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#include <xinput.h>
#endif

typedef struct { const char *sdl, *prompt; int axis; } Target;
/* Prompts use the Xbox names printed on the controller; SDL's a/b/x/y are positions (south, east,
 * west, north), which the game reads as Cross, Circle, Square and Triangle. */
static const Target targets[] = {
    {"a", "A (bawah)  -> Cross di game", 0},       {"b", "B (kanan)  -> Circle di game", 0},
    {"x", "X (kiri)   -> Square di game", 0},      {"y", "Y (atas)   -> Triangle di game", 0},
    {"leftshoulder", "LB  -> L1", 0},              {"rightshoulder", "RB  -> R1", 0},
    {"lefttrigger", "LT  -> L2", 0},               {"righttrigger", "RT  -> R2", 0},
    {"back", "BACK / VIEW  -> touchpad (menu pause)", 0},
    {"start", "START / MENU  -> Options", 0},
    {"leftstick", "LS (tekan stick kiri)  -> L3", 0},
    {"rightstick", "RS (tekan stick kanan)  -> R3", 0},
    {"guide", "GUIDE / HOME (diam 15 detik bila tidak ada)", 0},
    {"dpup", "D-PAD ATAS", 0},                     {"dpdown", "D-PAD BAWAH", 0},
    {"dpleft", "D-PAD KIRI", 0},                   {"dpright", "D-PAD KANAN", 0},
    {"leftx", "STICK KIRI ke KANAN", 1},           {"lefty", "STICK KIRI ke BAWAH", 1},
    {"rightx", "STICK KANAN ke KANAN", 1},         {"righty", "STICK KANAN ke BAWAH", 1},
};
#define TARGET_COUNT ((int)(sizeof targets / sizeof *targets))
enum { MAX_AXES = 16, BINDING_SIZE = 16 };

/* Thresholds of a stick prompt, on SDL's -32768..32767 scale, measured from the axis's rest value.
 * kStickPeak is how far a push must go to count. A push that also travels kStickOpposite the other way
 * on the same axis was rotated or pushed back; another free axis moving kStickCrossRatio of the push
 * means a diagonal. Both are ambiguous and asked again. kStickOpposite and kStickCrossRatio are first
 * values, to be tuned with real controllers. */
enum { kStickPeak = 20000, kStickOpposite = 12000, kStickRelease = 12000 };
#define kStickCrossRatio 0.6

static Sint16 rest[MAX_AXES];
static SDL_Joystick *joy;
static int axis_count;
static int ui_mode;                     /* --ui: JSON events out, commands in */
static void (*mapper_poll_hook)(void);  /* tests: drives a virtual joystick before every poll */
#ifdef MAPPER_NO_MAIN  /* built into tests/test_gamepad_mapper.c, which reads this count */
static int stick_rejections;            /* stick pushes asked again (rotated or two axes) */
#define COUNT_STICK_REJECTION() (++stick_rejections)
#else
#define COUNT_STICK_REJECTION() ((void)0)
#endif
static int joy_axis(int a) { return SDL_GetJoystickAxis(joy, a); }
static int axes_known(void) { return axis_count < MAX_AXES ? axis_count : MAX_AXES; }

static void poll_joystick(void) {
    if (mapper_poll_hook) mapper_poll_hook();
    SDL_UpdateJoysticks();
}

/* Rest values are taken again before every prompt, once everything is released, so a stick that was
 * held while the mapper started does not shift every later prompt. */
static void sample_rest(void) {
    poll_joystick();
    for (int a = 0; a < axes_known(); ++a) rest[a] = (Sint16)joy_axis(a);
}

/* ---- events (--ui) ---- */
static void json_text(const char *s) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        if (*p == '"' || *p == '\\') printf("\\%c", *p);
        else if (*p < 0x20) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

/* {"ev":ev[,"target":target][,key:value]} on one line. */
static void emit(const char *ev, const char *target, const char *key, const char *value) {
    if (!ui_mode) return;
    printf("{\"ev\":\"%s\"", ev);
    if (target) {
        printf(",\"target\":");
        json_text(target);
    }
    if (key) {
        printf(",\"%s\":", key);
        json_text(value);
    }
    printf("}\n");
    fflush(stdout);
}

/* Live stick values for the window (--ui): during a stick prompt (how far the stick is pushed, and how
 * much it also moves another way) and during the stick check (what the game will read). Sent at most
 * every kLiveIntervalMs and only when a value moved kLiveStep since the last line, so the pipe carries
 * about 30 lines a second while the stick moves and none while it rests. */
enum { kLiveIntervalMs = 33, kLiveStep = 400 };
static Uint64 live_sent_at;
static int live_sent, live_last[2];

static void live_reset(void) { live_sent = 0; }

static void emit_live(const char *target, const char *key_a, int a, const char *key_b, int b) {
    if (!ui_mode) return;
    const Uint64 now = SDL_GetTicks();
    if (live_sent && (now - live_sent_at < kLiveIntervalMs ||
                      (abs(a - live_last[0]) < kLiveStep && abs(b - live_last[1]) < kLiveStep)))
        return;
    live_sent = 1;
    live_sent_at = now;
    live_last[0] = a;
    live_last[1] = b;
    printf("{\"ev\":\"live\",\"target\":");
    json_text(target);
    printf(",\"%s\":%d,\"%s\":%d}\n", key_a, a, key_b, b);
    fflush(stdout);
}

/* ---- commands (--ui) ---- */
enum { COMMAND_SLOTS = 8, COMMAND_SIZE = 64 };
static SDL_Mutex *command_lock;
static char command_queue[COMMAND_SLOTS][COMMAND_SIZE];
static int command_head, command_count, command_eof;

static void queue_command(const char *line) {
    SDL_LockMutex(command_lock);
    if (command_count < COMMAND_SLOTS) {
        snprintf(command_queue[(command_head + command_count) % COMMAND_SLOTS], COMMAND_SIZE, "%s", line);
        ++command_count;
    }
    SDL_UnlockMutex(command_lock);
}

static int SDLCALL read_commands(void *unused) {
    (void)unused;
    char line[COMMAND_SIZE];
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\r\n")] = 0;
        queue_command(line);
    }
    SDL_LockMutex(command_lock);
    command_eof = 1;
    SDL_UnlockMutex(command_lock);
    return 0;
}

/* The next command, or 0 when none is waiting. The end of input reads as "cancel": the launcher closed
 * its window, so nothing may be written. */
static int next_command(char *out, size_t size) {
    int got = 0;
    SDL_LockMutex(command_lock);
    if (command_count) {
        snprintf(out, size, "%s", command_queue[command_head]);
        command_head = (command_head + 1) % COMMAND_SLOTS;
        --command_count;
        got = 1;
    } else if (command_eof) {
        snprintf(out, size, "cancel");
        got = 1;
    }
    SDL_UnlockMutex(command_lock);
    return got;
}

/* The first "skip" or "cancel" anywhere in the queue, taken out of it; the other commands stay queued in
 * their order. 0 when there is none. The end of input reads as "cancel", even with commands queued. */
static int take_prompt_command(char *out, size_t size) {
    int got = 0;
    SDL_LockMutex(command_lock);
    for (int i = 0; i < command_count && !got; ++i) {
        const char *command = command_queue[(command_head + i) % COMMAND_SLOTS];
        if (strcmp(command, "skip") && strcmp(command, "cancel")) continue;
        snprintf(out, size, "%s", command);
        for (int j = i; j + 1 < command_count; ++j)
            memcpy(command_queue[(command_head + j) % COMMAND_SLOTS],
                   command_queue[(command_head + j + 1) % COMMAND_SLOTS], COMMAND_SIZE);
        --command_count;
        got = 1;
    }
    if (!got && command_eof) {  /* the window is gone: queued commands no longer matter */
        snprintf(out, size, "cancel");
        got = 1;
    }
    SDL_UnlockMutex(command_lock);
    return got;
}

/* ---- bindings ---- */
/* bound[t] is the binding recorded for targets[t] ("" = none). kept[] are fields of an earlier mapping
 * that --sticks keeps (every non-stick field), with the binding part in kept_binding[]. */
static char bound[TARGET_COUNT][BINDING_SIZE];
enum { MAX_KEPT = 48, KEPT_SIZE = 40 };
static char kept[MAX_KEPT][KEPT_SIZE], kept_binding[MAX_KEPT][BINDING_SIZE];
static int kept_count;

/* "a1" and "a1~" are the same stick axis. */
static void binding_base(const char *binding, char *base) {
    snprintf(base, BINDING_SIZE, "%s", binding);
    char *tilde = strchr(base, '~');
    if (tilde) *tilde = 0;
}

/* Whether another entry already has this input: a second entry never takes one of them. */
static int binding_taken(const char *binding, int except_target) {
    char base[BINDING_SIZE], other[BINDING_SIZE];
    binding_base(binding, base);
    for (int t = 0; t < TARGET_COUNT; ++t) {
        if (t == except_target || !bound[t][0]) continue;
        binding_base(bound[t], other);
        if (!strcmp(base, other)) return 1;
    }
    for (int k = 0; k < kept_count; ++k) {
        binding_base(kept_binding[k], other);
        if (!strcmp(base, other)) return 1;
    }
    return 0;
}

/* Flip turns a stick axis the other way: "a0" <-> "a0~". Only stick entries bound to a whole axis. */
static int flip_binding(int target) {
    char *b = bound[target];
    if (!targets[target].axis || b[0] != 'a') return 0;
    size_t length = strlen(b);
    if (length && b[length - 1] == '~') b[length - 1] = 0;
    else if (length + 1 < BINDING_SIZE) { b[length] = '~'; b[length + 1] = 0; }
    return 1;
}

static int target_index(const char *name) {
    for (int t = 0; t < TARGET_COUNT; ++t) if (!strcmp(targets[t].sdl, name)) return t;
    return -1;
}

/* The mapping line: GUID, name, kept fields, every recorded entry in prompt order, platform. Returns 0
 * when it does not fit. */
static int build_mapping(char *mapping, size_t size, const char *guid, const char *name) {
    size_t used = (size_t)snprintf(mapping, size, "%s,%s,", guid, name);
    for (int k = 0; k < kept_count && used < size; ++k)
        used += (size_t)snprintf(mapping + used, size - used, "%s,", kept[k]);
    for (int t = 0; t < TARGET_COUNT && used < size; ++t)
        if (bound[t][0]) used += (size_t)snprintf(mapping + used, size - used, "%s:%s,", targets[t].sdl, bound[t]);
    if (used < size) used += (size_t)snprintf(mapping + used, size - used, "platform:Windows,");
    return used < size;
}

/* ---- the stick decision (pure, tested) ---- */
typedef struct { int min, max; } AxisRange;  /* lowest and highest distance from rest in the watch window */
typedef enum { STICK_OK, STICK_ROTATED, STICK_TWO_AXES } StickVerdict;

/* One stick prompt. The axis and its direction are those of the first deliberate push: the first free
 * axis that passed kStickPeak, with the sign of that push. Never the largest peak, which made a rotated
 * or pushed-back stick come out reversed, or bound to the other axis, without a word. A push that went
 * kStickOpposite the other way on that axis, or that moved another free axis kStickCrossRatio as far,
 * is ambiguous and rejected. */
static StickVerdict stick_verdict(const AxisRange *range, int count, const int *free_axis, int trigger,
                                  int negative) {
    const int peak = negative ? -range[trigger].min : range[trigger].max;
    const int back = negative ? range[trigger].max : -range[trigger].min;
    if (back >= kStickOpposite) return STICK_ROTATED;
    for (int a = 0; a < count; ++a) {
        if (a == trigger || !free_axis[a]) continue;
        const int other = range[a].max > -range[a].min ? range[a].max : -range[a].min;
        if (other >= kStickCrossRatio * peak) return STICK_TWO_AXES;
    }
    return STICK_OK;
}
#ifdef _WIN32
static int diagnose_xinput(void) {
    XINPUT_STATE previous[ XUSER_MAX_COUNT ] = {0};
    int connected[ XUSER_MAX_COUNT ] = {0};
    int connected_slots = 0;
    int input_changes = 0;
    const Uint64 deadline = SDL_GetTicks() + 15000;
    printf("XInput native: pantau slot 0-%d selama 15 detik.\n", XUSER_MAX_COUNT - 1);
    fflush(stdout);
    while (SDL_GetTicks() < deadline) {
        for (DWORD slot = 0; slot < XUSER_MAX_COUNT; ++slot) {
            XINPUT_STATE current = {0};
            const DWORD result = XInputGetState(slot, &current);
            if (result != 0) {
                if (connected[slot]) {
                    printf("XInput slot %lu terputus.\n", (unsigned long)slot);
                    connected[slot] = 0;
                }
                continue;
            }
            if (!connected[slot]) {
                connected[slot] = 1;
                ++connected_slots;
                previous[slot] = current;
                printf("XInput slot %lu terhubung.\n", (unsigned long)slot);
            } else if (memcmp(&current.Gamepad, &previous[slot].Gamepad, sizeof current.Gamepad)) {
                const XINPUT_GAMEPAD *pad = &current.Gamepad;
                printf("XInput slot %lu: buttons=0x%04x LT=%u RT=%u LX=%d LY=%d RX=%d RY=%d\n",
                       (unsigned long)slot, pad->wButtons, pad->bLeftTrigger, pad->bRightTrigger,
                       pad->sThumbLX, pad->sThumbLY, pad->sThumbRX, pad->sThumbRY);
                previous[slot] = current;
                ++input_changes;
            }
        }
        SDL_Delay(10);
    }
    if (!connected_slots) printf("XInput native tidak melaporkan controller terhubung.\n");
    else if (!input_changes) printf("XInput native terhubung, tetapi tidak ada perubahan kontrol.\n");
    return input_changes;
}
#endif

static int diagnose(SDL_Joystick *j, SDL_JoystickID id) {
    const int buttons = SDL_GetNumJoystickButtons(j);
    const int axes = SDL_GetNumJoystickAxes(j);
    const int hats = SDL_GetNumJoystickHats(j);
    Uint8 *previous_buttons = calloc((size_t)(buttons ? buttons : 1), sizeof *previous_buttons);
    Sint16 *previous_axes = calloc((size_t)(axes ? axes : 1), sizeof *previous_axes);
    Uint8 *previous_hats = calloc((size_t)(hats ? hats : 1), sizeof *previous_hats);
    if (!previous_buttons || !previous_axes || !previous_hats) {
        printf("Memori tidak cukup untuk mode diagnosis.\n");
        free(previous_buttons);
        free(previous_axes);
        free(previous_hats);
        return 0;
    }

    SDL_Gamepad *gamepad = NULL;
    Uint8 previous_gamepad_buttons[SDL_GAMEPAD_BUTTON_COUNT] = {0};
    Sint16 previous_gamepad_axes[SDL_GAMEPAD_AXIS_COUNT] = {0};
    const int is_gamepad = SDL_IsGamepad(id) != 0;
    if (is_gamepad) {
        gamepad = SDL_OpenGamepad(id);
        if (!gamepad) {
            printf("SDL mengenali gamepad, tetapi gagal membukanya: %s\n", SDL_GetError());
        } else {
            printf("SDL Gamepad API: %s\n", SDL_GetGamepadName(gamepad));
        }
    } else {
        printf("SDL Gamepad API: perangkat ini tidak dikenali sebagai gamepad standar.\n");
    }

    SDL_UpdateJoysticks();
    for (int b = 0; b < buttons; ++b) previous_buttons[b] = SDL_GetJoystickButton(j, b);
    for (int a = 0; a < axes; ++a) previous_axes[a] = (Sint16)SDL_GetJoystickAxis(j, a);
    for (int h = 0; h < hats; ++h) previous_hats[h] = SDL_GetJoystickHat(j, h);
    printf("Diagnosis 15 detik: tekan tombol, gerakkan D-pad dan stick pada controller terpilih.\n");
    printf("Input joystick dilaporkan dengan nomor index mulai dari 0; input gamepad memakai nama standar SDL.\n");
    fflush(stdout);

    int changes = 0;
    const Uint64 deadline = SDL_GetTicks() + 15000;
    while (SDL_GetTicks() < deadline) {
        SDL_UpdateJoysticks();
        for (int b = 0; b < buttons; ++b) {
            const Uint8 value = SDL_GetJoystickButton(j, b);
            if (value != previous_buttons[b]) {
                printf("button %d = %d\n", b, value);
                previous_buttons[b] = value;
                ++changes;
            }
        }
        for (int a = 0; a < axes; ++a) {
            const Sint16 value = (Sint16)SDL_GetJoystickAxis(j, a);
            if (abs((int)value - previous_axes[a]) >= 4000) {
                printf("axis %d = %d\n", a, value);
                previous_axes[a] = value;
                ++changes;
            }
        }
        for (int h = 0; h < hats; ++h) {
            const Uint8 value = SDL_GetJoystickHat(j, h);
            if (value != previous_hats[h]) {
                printf("hat %d = %u\n", h, value);
                previous_hats[h] = value;
                ++changes;
            }
        }
        if (gamepad) {
            for (int b = 0; b < SDL_GAMEPAD_BUTTON_COUNT; ++b) {
                const Uint8 value = SDL_GetGamepadButton(gamepad, (SDL_GamepadButton)b);
                if (value != previous_gamepad_buttons[b]) {
                    printf("gamepad button %s = %d\n",
                           SDL_GetGamepadStringForButton((SDL_GamepadButton)b), value);
                    previous_gamepad_buttons[b] = value;
                    ++changes;
                }
            }
            for (int a = 0; a < SDL_GAMEPAD_AXIS_COUNT; ++a) {
                const Sint16 value = SDL_GetGamepadAxis(gamepad, (SDL_GamepadAxis)a);
                if (abs((int)value - previous_gamepad_axes[a]) >= 4000) {
                    printf("gamepad axis %s = %d\n",
                           SDL_GetGamepadStringForAxis((SDL_GamepadAxis)a), value);
                    previous_gamepad_axes[a] = value;
                    ++changes;
                }
            }
        }
        fflush(stdout);
        SDL_Delay(10);
    }
    if (!changes) printf("Tidak ada perubahan input yang terbaca oleh SDL pada kedua API.\n");
#ifdef _WIN32
    diagnose_xinput();
#endif
    if (gamepad) SDL_CloseGamepad(gamepad);
    free(previous_buttons);
    free(previous_axes);
    free(previous_hats);
    return 1;
}

/* ---- capture ---- */
enum { CAP_TIMEOUT, CAP_OK, CAP_SKIP, CAP_CANCEL };
enum { FOUND_NONE, FOUND_BINDING, FOUND_USED, FOUND_REJECTED };

/* A command that ends a prompt (skip, cancel), or 0. A command the window sent is never lost: the others
 * (a second "redo" queued behind the first, a flip, save) wait in the queue for the review, which carries
 * them out or answers them. */
static int prompt_command(void) {
    char command[COMMAND_SIZE];
    if (!ui_mode || !take_prompt_command(command, sizeof command)) return 0;
    return !strcmp(command, "skip") ? CAP_SKIP : CAP_CANCEL;
}

/* Waits until no button is held, hats are centered and axes are back near rest. Returns 0 when the
 * launcher cancelled meanwhile. */
static int wait_release(void) {
    for (int stable = 0; stable < 15;) {
        if (prompt_command() == CAP_CANCEL) return 0;
        SDL_Delay(20);
        poll_joystick();
        int idle = 1;
        for (int b = 0; b < SDL_GetNumJoystickButtons(joy); ++b) idle &= !SDL_GetJoystickButton(joy, b);
        for (int h = 0; h < SDL_GetNumJoystickHats(joy); ++h) idle &= SDL_GetJoystickHat(joy, h) == SDL_HAT_CENTERED;
        for (int a = 0; a < axes_known(); ++a) idle &= abs(joy_axis(a) - rest[a]) < kStickRelease;
        stable = idle ? stable + 1 : 0;
    }
    SDL_Event e;
    while (SDL_PollEvent(&e)) {}
    return 1;
}

/* A button prompt: the first held button, pressed hat, or axis (trigger) beyond kStickPeak. */
static int find_button(char *out, size_t size) {
    for (int b = 0; b < SDL_GetNumJoystickButtons(joy); ++b) {
        if (!SDL_GetJoystickButton(joy, b)) continue;
        snprintf(out, size, "b%d", b);
        return FOUND_BINDING;
    }
    for (int h = 0; h < SDL_GetNumJoystickHats(joy); ++h) {
        const Uint8 value = SDL_GetJoystickHat(joy, h);
        if (value == SDL_HAT_CENTERED) continue;
        snprintf(out, size, "h%d.%d", h, value);
        return FOUND_BINDING;
    }
    for (int a = 0; a < axes_known(); ++a) {
        const int delta = joy_axis(a) - rest[a];
        if (abs(delta) < kStickPeak) continue;
        if (abs(rest[a]) > kStickPeak) snprintf(out, size, "a%d", a);
        else snprintf(out, size, delta > 0 ? "+a%d" : "-a%d", a);
        return FOUND_BINDING;
    }
    return FOUND_NONE;
}

/* During a stick prompt: how far the most-moved free axis is from rest ("push") and how far the next
 * free axis moved ("side"), both 0..32768. The window shows them as the player's own stick. */
static void stick_live(int target) {
    int push = 0, side = 0;
    for (int a = 0; a < axes_known(); ++a) {
        char name[8];
        snprintf(name, sizeof name, "a%d", a);
        if (binding_taken(name, target)) continue;
        const int moved = abs(joy_axis(a) - rest[a]);
        if (moved > push) {
            side = push;
            push = moved;
        } else if (moved > side) {
            side = moved;
        }
    }
    emit_live(targets[target].sdl, "push", push, "side", side);
}

static void widen(AxisRange *range) {
    for (int a = 0; a < axes_known(); ++a) {
        const int delta = joy_axis(a) - rest[a];
        if (delta < range[a].min) range[a].min = delta;
        if (delta > range[a].max) range[a].max = delta;
    }
}

/* A stick prompt: once a free axis passes kStickPeak, every axis is watched for 700 ms and the push is
 * judged by stick_verdict. */
static int find_stick(int target, char *out, size_t size) {
    int free_axis[MAX_AXES] = {0};
    int trigger = -1, negative = 0, used_axis = -1;
    for (int a = 0; a < axes_known(); ++a) {
        char name[8];
        snprintf(name, sizeof name, "a%d", a);
        free_axis[a] = !binding_taken(name, target);
        const int delta = joy_axis(a) - rest[a];
        if (abs(delta) < kStickPeak) continue;
        if (!free_axis[a]) {
            if (used_axis < 0) used_axis = a;
        } else if (trigger < 0) {
            trigger = a;
            negative = delta < 0;
        }
    }
    if (trigger < 0) {
        if (used_axis < 0) return FOUND_NONE;
        snprintf(out, size, "a%d", used_axis);  /* only an axis another entry has moved */
        return FOUND_USED;
    }
    AxisRange range[MAX_AXES] = {{0, 0}};
    widen(range);
    const Uint64 until = SDL_GetTicks() + 700;
    while (SDL_GetTicks() < until) {
        SDL_Delay(10);
        poll_joystick();
        widen(range);
        stick_live(target);
    }
    const StickVerdict verdict = stick_verdict(range, axes_known(), free_axis, trigger, negative);
    if (verdict != STICK_OK) {
        COUNT_STICK_REJECTION();
        if (ui_mode) emit("warn", targets[target].sdl, "reason", verdict == STICK_ROTATED ? "stick_rotated" : "stick_two_axes");
        else printf(verdict == STICK_ROTATED ? "[stick diputar atau didorong balik; dorong lurus sekali] "
                                             : "[stick bergerak ke dua arah; dorong lurus sekali] ");
        fflush(stdout);
        return FOUND_REJECTED;
    }
    snprintf(out, size, negative ? "a%d~" : "a%d", trigger);
    return FOUND_BINDING;
}

/* How long a prompt waits for a usable input before the entry is skipped. Asking again after an input
 * another entry has keeps the same deadline (pressing the wrong button does not keep a prompt open
 * forever). A rejected stick push starts it again: the mapper asked for a new push. */
static Uint32 prompt_timeout_ms = 15000;

/* One prompt. Polls the selected device directly; SDL joystick events can be absent without a window.
 * An input another entry already has, or an ambiguous stick push, is reported and the prompt asked
 * again once everything is released, with the rest values taken again. */
static int capture(int target, char *out, size_t size) {
    Uint64 deadline = SDL_GetTicks() + prompt_timeout_ms;
    live_reset();
    while (SDL_GetTicks() < deadline) {
        const int command = prompt_command();
        if (command) return command;
        poll_joystick();
        if (targets[target].axis) stick_live(target);
        int found = targets[target].axis ? find_stick(target, out, size) : find_button(out, size);
        if (found == FOUND_BINDING && binding_taken(out, target)) found = FOUND_USED;
        if (found == FOUND_BINDING) return CAP_OK;
        if (found != FOUND_NONE) {
            if (found == FOUND_USED) {
                char base[BINDING_SIZE];
                binding_base(out, base);
                if (ui_mode) emit("used", targets[target].sdl, "binding", base);
                else printf("[%s sudah dipakai] ", base);
                fflush(stdout);
            }
            if (!wait_release()) return CAP_CANCEL;
            sample_rest();
            if (found == FOUND_REJECTED) deadline = SDL_GetTicks() + prompt_timeout_ms;
            live_reset();
            continue;
        }
        SDL_Delay(10);
    }
    return CAP_TIMEOUT;
}

/* Asks for one entry and records it in bound[]. Returns the CAP_ result. */
static int ask(int target) {
    if (ui_mode) {
        char index[32];
        snprintf(index, sizeof index, "%d", target);
        emit("prompt", targets[target].sdl, "index", index);
    } else {
        printf("Tekan %s ... ", targets[target].prompt);
        fflush(stdout);
    }
    char binding[BINDING_SIZE];
    bound[target][0] = 0;
    const int result = capture(target, binding, sizeof binding);
    if (result == CAP_OK) {
        snprintf(bound[target], BINDING_SIZE, "%s", binding);
        if (ui_mode) emit("captured", targets[target].sdl, "binding", binding);
        else printf("%s\n", binding);
    } else if (result == CAP_TIMEOUT) {
        if (ui_mode) emit("timeout", targets[target].sdl, NULL, NULL);
        else printf("dilewati\n");
    } else if (result == CAP_SKIP) {
        emit("skipped", targets[target].sdl, NULL, NULL);
    }
    if (result == CAP_CANCEL) return result;
    if (!wait_release()) return CAP_CANCEL;
    sample_rest();
    return result;
}

enum { MAX_MAPPING_LINE = 4096 };
typedef struct { char **lines; size_t count; } MappingFile;

static void free_mapping_file(MappingFile *file) {
    for (size_t i = 0; i < file->count; ++i) free(file->lines[i]);
    free(file->lines);
    file->lines = NULL;
    file->count = 0;
}

static int read_mapping_file(const char *path, MappingFile *file) {
    FILE *f = fopen(path, "r");
    if (!f) {
        if (errno == ENOENT) return 1;
        printf("Tidak bisa membaca %s\n", path);
        return 0;
    }
    char line[MAX_MAPPING_LINE];
    while (fgets(line, sizeof line, f)) {
        const size_t length = strlen(line);
        if (length == sizeof line - 1 && line[length - 1] != '\n' && !feof(f)) {
            printf("Baris mapping terlalu panjang di %s\n", path);
            fclose(f);
            free_mapping_file(file);
            return 0;
        }
        char *copy = malloc(length + 1);
        if (!copy) {
            printf("Memori tidak cukup untuk membaca mapping %s\n", path);
            fclose(f);
            free_mapping_file(file);
            return 0;
        }
        char **lines = realloc(file->lines, (file->count + 1) * sizeof *file->lines);
        if (!lines) {
            free(copy);
            printf("Memori tidak cukup untuk membaca mapping %s\n", path);
            fclose(f);
            free_mapping_file(file);
            return 0;
        }
        file->lines = lines;
        memcpy(copy, line, length + 1);
        file->lines[file->count++] = copy;
    }
    if (ferror(f)) {
        printf("Tidak bisa membaca %s\n", path);
        fclose(f);
        free_mapping_file(file);
        return 0;
    }
    fclose(f);
    return 1;
}

static int line_has_guid(const char *line, const char *guid) {
    const size_t length = strlen(guid);
    return !strncmp(line, guid, length) && line[length] == ',';
}

static const char *mapping_for_guid(const MappingFile *file, const char *guid) {
    for (size_t i = 0; i < file->count; ++i)
        if (line_has_guid(file->lines[i], guid)) return file->lines[i];
    return NULL;
}

/* Writes every other GUID's line plus this mapping to a temporary file, then replaces the file in one
 * step: a failure part way never loses the mappings of other controllers. */
static int save_mapping_file(const char *path, const MappingFile *file,
                             const char *guid, const char *mapping) {
    char temp[1024];
    if ((size_t)snprintf(temp, sizeof temp, "%s.tmp", path) >= sizeof temp) {
        if (!ui_mode) printf("Path terlalu panjang: %s\n", path);
        return 0;
    }
    FILE *f = fopen(temp, "w");
    if (!f) {
        if (!ui_mode) printf("Tidak bisa menulis %s\n", temp);
        return 0;
    }
    int failed = 0;
    for (size_t i = 0; i < file->count && !failed; ++i)
        if (!line_has_guid(file->lines[i], guid)) failed = fputs(file->lines[i], f) == EOF;
    failed |= fprintf(f, "%s\n", mapping) < 0;
    failed |= fclose(f) != 0;
#ifdef _WIN32
    if (!failed) failed = !MoveFileExA(temp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
#else
    if (!failed) failed = rename(temp, path) != 0;
#endif
    if (failed) {
        remove(temp);
        if (!ui_mode) printf("Gagal menyimpan mapping ke %s\n", path);
        return 0;
    }
    return 1;
}

/* --sticks: keep every non-stick field of this controller's saved mapping; only the sticks are asked. */
static int keep_buttons(const char *saved) {
    char line[MAX_MAPPING_LINE];
    snprintf(line, sizeof line, "%s", saved);
    line[strcspn(line, "\r\n")] = 0;
    char *field = strtok(line, ","); /* GUID */
    field = strtok(NULL, ",");       /* name */
    while ((field = strtok(NULL, ","))) {
        const char *colon = strchr(field, ':');
        if (!colon || !strncmp(field, "leftx:", 6) || !strncmp(field, "lefty:", 6) ||
            !strncmp(field, "rightx:", 7) || !strncmp(field, "righty:", 7) || !strncmp(field, "platform:", 9))
            continue;
        if (kept_count == MAX_KEPT) return 0;
        snprintf(kept[kept_count], KEPT_SIZE, "%s", field);
        snprintf(kept_binding[kept_count], BINDING_SIZE, "%s", colon + 1);
        ++kept_count;
    }
    return 1;
}

/* ---- stick check (--ui review) ---- */
/* "check left" / "check right": the mapping recorded so far is given to SDL for this controller, and
 * the stick is read through it as an SDL gamepad, the way the game reads it (the game loads the same
 * line from user\gamepad_mapping.txt). Live lines then carry what the game would receive. */
static SDL_Gamepad *check_pad;
static int check_side = -1;  /* 0 left stick, 1 right stick, -1 no check */

static void check_close(void) {
    if (check_pad) SDL_CloseGamepad(check_pad);
    check_pad = NULL;
}

static int check_apply(const char *guid, const char *name) {
    char mapping[2048];
    check_close();
    if (!build_mapping(mapping, sizeof mapping, guid, name)) return 0;
    char *platform = strstr(mapping, "platform:");
    if (platform) *platform = 0;  /* read on the platform the mapper runs on */
    if (SDL_AddGamepadMapping(mapping) < 0) return 0;
    check_pad = SDL_OpenGamepad(SDL_GetJoystickID(joy));
    live_reset();
    return check_pad != NULL;
}

static void check_live(void) {
    poll_joystick();
    const int right = check_side == 1;
    emit_live(right ? "right" : "left",
              "x", SDL_GetGamepadAxis(check_pad, right ? SDL_GAMEPAD_AXIS_RIGHTX : SDL_GAMEPAD_AXIS_LEFTX),
              "y", SDL_GetGamepadAxis(check_pad, right ? SDL_GAMEPAD_AXIS_RIGHTY : SDL_GAMEPAD_AXIS_LEFTY));
}

/* --ui after the last prompt: the window shows the result and sends save, cancel, redo <entry>,
 * flip <entry> or check left|right|off. Returns 1 to save, 0 when cancelled. */
static int review(const char *guid, const char *name, int sticks_only) {
    char mapping[2048], command[COMMAND_SIZE];
    for (;;) {
        if (!build_mapping(mapping, sizeof mapping, guid, name)) {
            emit("error", NULL, "message", "The mapping is too long to save.");
            check_close();
            return 0;
        }
        emit("review", NULL, "mapping", mapping);
        if (check_side >= 0 && !check_apply(guid, name)) {  /* a flip or redo changed what the game reads */
            emit("error", NULL, "message", "The stick check could not start.");
            check_side = -1;
        }
        for (;;) {
            if (!next_command(command, sizeof command)) {
                if (check_side >= 0) check_live();
                SDL_Delay(check_side >= 0 ? 10 : 20);
                continue;
            }
            if (!strcmp(command, "save") || !strcmp(command, "cancel")) {
                check_close();
                check_side = -1;
                return !strcmp(command, "save");
            }
            if (!strncmp(command, "check ", 6)) {
                const char *side = command + 6;
                check_side = !strcmp(side, "left") ? 0 : !strcmp(side, "right") ? 1 : -1;
                if (check_side < 0) {
                    check_close();
                    if (strcmp(side, "off")) emit("error", NULL, "message", "Unknown command.");
                } else if (!check_apply(guid, name)) {
                    emit("error", NULL, "message", "The stick check could not start.");
                    check_side = -1;
                }
                continue;
            }
            const int redo = !strncmp(command, "redo ", 5), flip = !strncmp(command, "flip ", 5);
            const int target = redo || flip ? target_index(command + 5) : -1;
            if (target < 0 || (sticks_only && redo && !targets[target].axis)) {
                emit("error", NULL, "message", "Unknown command.");
                continue;
            }
            if (redo && ask(target) == CAP_CANCEL) return 0;
            if (flip) {
                if (!flip_binding(target)) {
                    emit("error", targets[target].sdl, "message", "Only a stick that is set can be flipped.");
                    continue;
                }
                emit("flipped", targets[target].sdl, "binding", bound[target]);
            }
            break;
        }
    }
}

static int finish(SDL_Joystick *j, SDL_JoystickID *ids, int code) {
    if (j) SDL_CloseJoystick(j);
    SDL_free(ids);
    SDL_Quit();
    return code;
}

/* One line of the device list, as JSON. */
static void emit_devices(const SDL_JoystickID *ids, int count) {
    if (!ui_mode) return;
    printf("{\"ev\":\"devices\",\"list\":[");
    for (int i = 0; i < count; ++i) {
        char guid[64];
        SDL_GUIDToString(SDL_GetJoystickGUIDForID(ids[i]), guid, sizeof guid);
        const char *name = SDL_GetJoystickNameForID(ids[i]);
        printf("%s{\"i\":%d,\"name\":", i ? "," : "", i);
        json_text(name && *name ? name : "Gamepad");
        printf(",\"guid\":");
        json_text(guid);
        printf("}");
    }
    printf("]}\n");
    fflush(stdout);
}

/* --ui: the launcher picks the controller with "select <index>" (0 based). Returns -1 when cancelled. */
static int ui_select(int count) {
    char command[COMMAND_SIZE];
    for (;;) {
        if (!next_command(command, sizeof command)) {
            SDL_Delay(20);
            continue;
        }
        if (!strcmp(command, "cancel")) return -1;
        char *end = NULL;
        const long choice = !strncmp(command, "select ", 7) ? strtol(command + 7, &end, 10) : -1;
        if (end && !*end && choice >= 0 && choice < count) return (int)choice;
        emit("error", NULL, "message", "Choose one of the listed controllers.");
    }
}

#ifndef MAPPER_NO_MAIN
int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "gamepad_mapping.txt";
    int diagnose_mode = 0, sticks_only = 0;
    for (int i = 2; i < argc; ++i) {
        diagnose_mode |= !strcmp(argv[i], "--diagnose");
        sticks_only |= !strcmp(argv[i], "--sticks");
        ui_mode |= !strcmp(argv[i], "--ui");
    }
    /* No SDL window here (a console, or none under --ui): without this SDL drops joystick events as
     * background input. */
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (!SDL_Init(SDL_INIT_JOYSTICK | SDL_INIT_GAMEPAD)) {
        if (ui_mode) emit("error", NULL, "message", "The controller system could not start.");
        else printf("SDL_Init gagal: %s\n", SDL_GetError());
        return 1;
    }
    if (ui_mode) {
        command_lock = SDL_CreateMutex();
        SDL_Thread *reader = SDL_CreateThread(read_commands, "mapper-commands", NULL);
        if (!command_lock || !reader) {
            emit("error", NULL, "message", "The mapper could not start.");
            return finish(NULL, NULL, 1);
        }
        SDL_DetachThread(reader);
    }
    SDL_Delay(500);
    int count = 0;
    SDL_JoystickID *ids = SDL_GetJoysticks(&count);
    if (!ids || count < 1) {
        if (ui_mode) emit("error", NULL, "message", "No controller found. Plug one in, then try again.");
        else printf("Tidak ada controller terdeteksi. Colokkan lalu jalankan lagi.\n");
        return finish(NULL, ids, 1);
    }
    int selected = 0;
    if (ui_mode) {
        emit_devices(ids, count);
        if (count > 1 && (selected = ui_select(count)) < 0) {
            emit("cancelled", NULL, NULL, NULL);
            return finish(NULL, ids, 2);
        }
    } else {
        for (int i = 0; i < count; ++i) {
            char guid[64];
            SDL_GUIDToString(SDL_GetJoystickGUIDForID(ids[i]), guid, sizeof guid);
            const char *name = SDL_GetJoystickNameForID(ids[i]);
            printf("%d. %s (GUID %s)\n", i + 1, name && *name ? name : "Gamepad", guid);
        }
        if (count > 1) {
            char input[32];
            printf("Pilih controller yang akan di-mapping [1-%d]: ", count);
            fflush(stdout);
            if (!fgets(input, sizeof input, stdin)) {
                printf("Pilihan controller tidak terbaca.\n");
                return finish(NULL, ids, 1);
            }
            char *end = NULL;
            const long choice = strtol(input, &end, 10);
            while (end && isspace((unsigned char)*end)) ++end;
            if (end == input || (end && *end) || choice < 1 || choice > count) {
                printf("Pilihan controller tidak valid.\n");
                return finish(NULL, ids, 1);
            }
            selected = (int)choice - 1;
        }
    }
    SDL_Joystick *j = SDL_OpenJoystick(ids[selected]);
    if (!j) {
        if (ui_mode) emit("error", NULL, "message", "The controller could not be opened.");
        else printf("Tidak bisa membuka controller: %s\n", SDL_GetError());
        return finish(NULL, ids, 1);
    }
    joy = j;
    axis_count = SDL_GetNumJoystickAxes(j);
    char guid[64];
    SDL_GUIDToString(SDL_GetJoystickGUID(j), guid, sizeof guid);
    const char *joy_name = SDL_GetJoystickName(j);
    const char *name = joy_name && *joy_name ? joy_name : "Gamepad";
    if (ui_mode) {
        printf("{\"ev\":\"controller\",\"name\":");
        json_text(name);
        printf(",\"guid\":");
        json_text(guid);
        printf("}\n");
        fflush(stdout);
    } else {
        printf("Controller: %s (GUID %s), %d tombol, %d axis, %d hat\n", joy_name ? joy_name : "?", guid,
               SDL_GetNumJoystickButtons(j), SDL_GetNumJoystickAxes(j), SDL_GetNumJoystickHats(j));
        printf("Lepaskan semua tombol dan stick. Tiap langkah: tekan sekali lalu lepas; diam 15 detik = lewati.\n");
        printf("Untuk menjadikan Xbox A = Circle game: saat prompt Cross tekan Xbox B, lalu saat prompt Circle tekan Xbox A.\n");
    }
    SDL_Delay(800);
    sample_rest();
    if (diagnose_mode) {
        diagnose(j, ids[selected]);
        return finish(j, ids, 0);
    }
    SDL_Event e;
    while (SDL_PollEvent(&e)) {}

    MappingFile old_mappings = {0};
    if (!read_mapping_file(path, &old_mappings)) {
        emit("error", NULL, "message", "The saved mapping file could not be read.");
        return finish(j, ids, 1);
    }
    if (sticks_only) {
        const char *saved = mapping_for_guid(&old_mappings, guid);
        if (!saved || !keep_buttons(saved)) {
            if (ui_mode) emit("error", NULL, "message", "This controller has no saved mapping yet: map all first.");
            else printf("Tidak ada mapping lama untuk controller ini di %s; jalankan tanpa --sticks.\n", path);
            free_mapping_file(&old_mappings);
            return finish(j, ids, 1);
        }
        if (!ui_mode) printf("Tombol lama dipakai lagi; sekarang hanya stick.\n");
    }
    for (int t = 0; t < TARGET_COUNT; ++t) {
        if (sticks_only && !targets[t].axis) continue;
        if (ask(t) == CAP_CANCEL) {
            emit("cancelled", NULL, NULL, NULL);
            free_mapping_file(&old_mappings);
            return finish(j, ids, 2);
        }
    }
    if (ui_mode && !review(guid, name, sticks_only)) {
        emit("cancelled", NULL, NULL, NULL);
        free_mapping_file(&old_mappings);
        return finish(j, ids, 2);
    }
    char mapping[2048];
    if (!build_mapping(mapping, sizeof mapping, guid, name)) {
        if (ui_mode) emit("error", NULL, "message", "The mapping is too long to save.");
        else printf("Mapping terlalu besar.\n");
        free_mapping_file(&old_mappings);
        return finish(j, ids, 1);
    }
    const int saved = save_mapping_file(path, &old_mappings, guid, mapping);
    free_mapping_file(&old_mappings);
    if (!saved) {
        emit("error", NULL, "message", "The mapping could not be saved.");
        return finish(j, ids, 1);
    }
    if (ui_mode) emit("saved", NULL, "mapping", mapping);
    else printf("\nMapping disimpan ke %s:\n%s\n", path, mapping);
    return finish(j, ids, 0);
}
#endif
