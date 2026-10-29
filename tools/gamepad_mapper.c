/* Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (LICENSE, NOTICE.md). */
/* Interactive SDL3 gamepad mapper. Asks for each PlayStation button/stick in turn, records the
 * selected joystick input, and keeps mappings for other GUIDs in the same file. */
#include <SDL3/SDL.h>
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
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
enum { MAX_AXES = 16 };
static Sint16 rest[MAX_AXES];
static SDL_Joystick *joy;
static int axis_count;
static int joy_axis(int a) { return SDL_GetJoystickAxis(joy, a); }

/* Waits until no button is held, hats are centered and axes are back near rest. */
static void wait_release(SDL_Joystick *j) {
    for (int stable = 0; stable < 15;) {
        SDL_Delay(20);
        SDL_UpdateJoysticks();
        int idle = 1;
        for (int b = 0; b < SDL_GetNumJoystickButtons(j); ++b) idle &= !SDL_GetJoystickButton(j, b);
        for (int h = 0; h < SDL_GetNumJoystickHats(j); ++h) idle &= SDL_GetJoystickHat(j, h) == SDL_HAT_CENTERED;
        for (int a = 0; a < SDL_GetNumJoystickAxes(j) && a < MAX_AXES; ++a)
            idle &= abs(SDL_GetJoystickAxis(j, a) - rest[a]) < 12000;
        stable = idle ? stable + 1 : 0;
    }
    SDL_Event e;
    while (SDL_PollEvent(&e)) {}
}

/* Bindings already given to an earlier entry: a second entry never takes one of them. */
static char used[64][16];
static int used_count;
static int already_used(const char *binding) {
    for (int i = 0; i < used_count; ++i) if (!strcmp(used[i], binding)) return 1;
    return 0;
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

/* Poll the selected device directly; SDL joystick events can be absent in a console mapper. */
static int capture(int want_axis, char *out, size_t size) {
    const Uint64 deadline = SDL_GetTicks() + 15000;
retry:
    while (SDL_GetTicks() < deadline) {
        SDL_UpdateJoysticks();
        if (!want_axis) {
            for (int b = 0; b < SDL_GetNumJoystickButtons(joy); ++b) {
                if (!SDL_GetJoystickButton(joy, b)) continue;
                snprintf(out, size, "b%d", b);
                goto captured;
            }
            for (int h = 0; h < SDL_GetNumJoystickHats(joy); ++h) {
                const Uint8 value = SDL_GetJoystickHat(joy, h);
                if (value == SDL_HAT_CENTERED) continue;
                snprintf(out, size, "h%d.%d", h, value);
                goto captured;
            }
        }

        int moved_axis = -1;
        int moved_delta = 0;
        for (int a = 0; a < MAX_AXES && a < axis_count; ++a) {
            const int delta = joy_axis(a) - rest[a];
            if (abs(delta) < 20000) continue;
            if (!want_axis) {
                if (abs(rest[a]) > 20000) {
                    snprintf(out, size, "a%d", a);
                } else {
                    snprintf(out, size, delta > 0 ? "+a%d" : "-a%d", a);
                }
                goto captured;
            }
            if (abs(delta) > abs(moved_delta)) {
                moved_axis = a;
                moved_delta = delta;
            }
        }
        if (want_axis && moved_axis >= 0) {
            /* Sticks move two axes at once and jitter: watch every axis for 700 ms and
             * take the unused axis that moved furthest. */
            int best = -1, best_delta = 0;
            const Uint64 until = SDL_GetTicks() + 700;
            while (SDL_GetTicks() < until) {
                SDL_Delay(10);
                SDL_UpdateJoysticks();
                for (int a = 0; a < MAX_AXES && a < axis_count; ++a) {
                    char name[8];
                    snprintf(name, sizeof name, "a%d", a);
                    if (already_used(name)) continue;
                    const int delta = joy_axis(a) - rest[a];
                    if (abs(delta) > abs(best_delta)) {
                        best = a;
                        best_delta = delta;
                    }
                }
            }
            if (best >= 0 && abs(best_delta) >= 20000) {
                snprintf(out, size, best_delta > 0 ? "a%d" : "a%d~", best);
                goto captured;
            }
        }
        SDL_Delay(10);
    }
    return 0;

captured:
        ;
        /* "a1" and "a1~" are the same stick axis. */
        char base[16]; snprintf(base, sizeof base, "%s", out);
        char *tilde = strchr(base, '~'); if (tilde) *tilde = 0;
        if (already_used(base)) {
            printf("[%s sudah dipakai] ", base);
            fflush(stdout);
            wait_release(joy);
            goto retry;
        }
        if (used_count < 64) snprintf(used[used_count++], sizeof used[0], "%s", base);
        return 1;
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

static int save_mapping_file(const char *path, const MappingFile *file,
                             const char *guid, const char *mapping) {
    FILE *f = fopen(path, "w");
    if (!f) {
        printf("Tidak bisa menulis %s\n", path);
        return 0;
    }
    for (size_t i = 0; i < file->count; ++i) {
        if (!line_has_guid(file->lines[i], guid) && fputs(file->lines[i], f) == EOF) {
            printf("Gagal menulis mapping ke %s\n", path);
            fclose(f);
            return 0;
        }
    }
    const int write_failed = fprintf(f, "%s\n", mapping) < 0;
    const int close_failed = fclose(f) != 0;
    if (write_failed || close_failed) {
        printf("Gagal menyimpan mapping ke %s\n", path);
        return 0;
    }
    return 1;
}

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "gamepad_mapping.txt";
    /* The console window is not an SDL window: without this SDL drops joystick events as background input. */
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (!SDL_Init(SDL_INIT_JOYSTICK | SDL_INIT_GAMEPAD)) { printf("SDL_Init gagal: %s\n", SDL_GetError()); return 1; }
    SDL_Delay(500);
    int count = 0;
    SDL_JoystickID *ids = SDL_GetJoysticks(&count);
    if (!ids || count < 1) {
        printf("Tidak ada controller terdeteksi. Colokkan lalu jalankan lagi.\n");
        SDL_free(ids);
        SDL_Quit();
        return 1;
    }
    for (int i = 0; i < count; ++i) {
        char guid[64];
        SDL_GUIDToString(SDL_GetJoystickGUIDForID(ids[i]), guid, sizeof guid);
        const char *name = SDL_GetJoystickNameForID(ids[i]);
        printf("%d. %s (GUID %s)\n", i + 1, name && *name ? name : "Gamepad", guid);
    }
    int selected = 0;
    if (count > 1) {
        char input[32];
        printf("Pilih controller yang akan di-mapping [1-%d]: ", count);
        fflush(stdout);
        if (!fgets(input, sizeof input, stdin)) {
            printf("Pilihan controller tidak terbaca.\n");
            SDL_free(ids);
            SDL_Quit();
            return 1;
        }
        char *end = NULL;
        const long choice = strtol(input, &end, 10);
        while (end && isspace((unsigned char)*end)) ++end;
        if (end == input || (end && *end) || choice < 1 || choice > count) {
            printf("Pilihan controller tidak valid.\n");
            SDL_free(ids);
            SDL_Quit();
            return 1;
        }
        selected = (int)choice - 1;
    }
    SDL_Joystick *j = SDL_OpenJoystick(ids[selected]);
    if (!j) {
        printf("Tidak bisa membuka controller: %s\n", SDL_GetError());
        SDL_free(ids);
        SDL_Quit();
        return 1;
    }
    joy = j;
    axis_count = SDL_GetNumJoystickAxes(j);
    char guid[64];
    SDL_GUIDToString(SDL_GetJoystickGUID(j), guid, sizeof guid);
    const char *name = SDL_GetJoystickName(j);
    printf("Controller: %s (GUID %s), %d tombol, %d axis, %d hat\n", name ? name : "?", guid,
           SDL_GetNumJoystickButtons(j), SDL_GetNumJoystickAxes(j), SDL_GetNumJoystickHats(j));
    printf("Lepaskan semua tombol dan stick. Tiap langkah: tekan sekali lalu lepas; diam 15 detik = lewati.\n");
    printf("Untuk menjadikan Xbox A = Circle game: saat prompt Cross tekan Xbox B, lalu saat prompt Circle tekan Xbox A.\n");
    SDL_Delay(800);
    SDL_UpdateJoysticks();
    for (int a = 0; a < SDL_GetNumJoystickAxes(j) && a < MAX_AXES; ++a) rest[a] = SDL_GetJoystickAxis(j, a);
    if (argc > 2 && !strcmp(argv[2], "--diagnose")) {
        diagnose(j, ids[selected]);
        SDL_CloseJoystick(j);
        SDL_free(ids);
        SDL_Quit();
        return 0;
    }
    SDL_Event e;
    while (SDL_PollEvent(&e)) {}

    MappingFile old_mappings = {0};
    if (!read_mapping_file(path, &old_mappings)) {
        SDL_CloseJoystick(j);
        SDL_free(ids);
        SDL_Quit();
        return 1;
    }
    char mapping[2048];
    snprintf(mapping, sizeof mapping, "%s,%s,", guid, name && *name ? name : "Gamepad");
    /* --sticks: keep the buttons of an existing mapping for this controller, record only the sticks. */
    int sticks_only = argc > 2 && !strcmp(argv[2], "--sticks");
    if (sticks_only) {
        const char *saved = mapping_for_guid(&old_mappings, guid);
        if (!saved) {
            printf("Tidak ada mapping lama untuk controller ini di %s; jalankan tanpa --sticks.\n", path);
            free_mapping_file(&old_mappings);
            SDL_CloseJoystick(j);
            SDL_free(ids);
            SDL_Quit();
            return 1;
        }
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
            size_t at = strlen(mapping);
            snprintf(mapping + at, sizeof mapping - at, "%s,", field);
            if (used_count < 64) snprintf(used[used_count++], sizeof used[0], "%s", colon + 1);
        }
        printf("Tombol lama dipakai lagi; sekarang hanya stick.\n");
    }
    for (size_t i = 0; i < sizeof targets / sizeof *targets; ++i) {
        if (sticks_only && !targets[i].axis) continue;
        printf("Tekan %s ... ", targets[i].prompt);
        fflush(stdout);
        char binding[16];
        if (capture(targets[i].axis, binding, sizeof binding)) {
            printf("%s\n", binding);
            size_t used = strlen(mapping);
            snprintf(mapping + used, sizeof mapping - used, "%s:%s,", targets[i].sdl, binding);
        } else {
            printf("dilewati\n");
        }
        wait_release(j);
    }
    size_t used = strlen(mapping);
    if (used + sizeof "platform:Windows," > sizeof mapping) {
        printf("Mapping terlalu besar.\n");
        free_mapping_file(&old_mappings);
        SDL_CloseJoystick(j);
        SDL_free(ids);
        SDL_Quit();
        return 1;
    }
    snprintf(mapping + used, sizeof mapping - used, "platform:Windows,");
    const int saved = save_mapping_file(path, &old_mappings, guid, mapping);
    free_mapping_file(&old_mappings);
    if (!saved) {
        SDL_CloseJoystick(j);
        SDL_free(ids);
        SDL_Quit();
        return 1;
    }
    printf("\nMapping disimpan ke %s:\n%s\n", path, mapping);
    SDL_CloseJoystick(j);
    SDL_free(ids);
    SDL_Quit();
    return 0;
}
