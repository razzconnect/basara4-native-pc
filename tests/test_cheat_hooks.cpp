#include <cassert>
#include "../src/cheat_hook.h"
#include <array>
#include <atomic>
#include <cstdio>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif

// Basara 4 PC: every hook in cheat_hook.h gets a behaviour check that matches its code. The five
// float stores (ids 0-4) share one harness; VERY_HARD (5) and DOUBLE_JUMPS (6) decide a branch from
// the condition flags, so their harness records ZF and RAX instead. A new id must add its check.
static_assert(SPARTAN_CHEAT_COUNT == 7, "a new cheat id needs a behaviour check in this test");

namespace {
unsigned char* AllocCode() {
#ifdef _WIN32
    auto* code = static_cast<unsigned char*>(VirtualAlloc(nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
#else
    auto* code = static_cast<unsigned char*>(mmap(nullptr, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    assert(code != MAP_FAILED);
#endif
    assert(code);
    return code;
}

void MakeExecutable(unsigned char* code) {
#ifdef _WIN32
    DWORD previous;
    assert(VirtualProtect(code, 4096, PAGE_EXECUTE_READ, &previous));
    FlushInstructionCache(GetCurrentProcess(), code, 4096);
#else
    assert(!mprotect(code, 4096, PROT_READ | PROT_EXEC));
#endif
}

void FreeCode(unsigned char* code) {
#ifdef _WIN32
    VirtualFree(code, 0, MEM_RELEASE);
#else
    munmap(code, 4096);
#endif
}

// Ids 0-4: the hook stores a float into the player at the hooked offset; enabled, it keeps the
// cheat's value instead. Emulates the game's register convention, including its flags and a live
// RAX value.
void CheckFloatStore(const SpartanCheatHook& hook) {
    assert(hook.id <= SPARTAN_CHEAT_ORBS);
    unsigned char* executable = AllocCode();
    const unsigned char entry[] = {0x53,0x41,0x55,0x48,0x89,0xfb,0x49,0x89,0xfd,
        0xc5,0xf8,0x28,0xc8,0x48,0xb8,0x78,0x56,0x34,0x12,0,0,0,0,0xf9,
        0xff,0x25,0,0,0,0};
    memcpy(executable, entry, sizeof(entry));
    uintptr_t target = reinterpret_cast<uintptr_t>(executable + 64);
    memcpy(executable + sizeof(entry), &target, 8);
    std::atomic<bool> flag{false};
    assert(spartanport_cheat_build_stub(executable + 64, &hook, reinterpret_cast<uintptr_t>(&flag),
                                reinterpret_cast<uintptr_t>(executable + 256)) < 192);
    const unsigned char exit[] = {0x0f,0x92,0x87,0,3,0,0,
        0x48,0x89,0x87,8,3,0,0,0x41,0x5d,0x5b,0xc3};
    memcpy(executable + 256, exit, sizeof(exit));
    MakeExecutable(executable);
    using Execute = void (__attribute__((sysv_abi)) *)(void*, float);
    const auto execute = reinterpret_cast<Execute>(executable);
    std::array<unsigned char, 0x400> player{};
    const unsigned offsets[SPARTAN_CHEAT_ORBS + 1] = {0x1cc,0x1d0,0x1d4,0x1d8,0x1e8};
    const unsigned offset = offsets[hook.id];
    const float original = 100, health_max = 250;
    memcpy(player.data() + offset, &original, 4);
    memcpy(player.data() + 0x1d0, &health_max, 4);
    execute(player.data(), 5);
    float result;
    memcpy(&result, player.data() + offset, 4);
    assert(result == 5 && player[0x300] == 1);
    uint64_t preserved;
    memcpy(&preserved, player.data() + 0x308, 8);
    assert(preserved == 0x12345678);
    memcpy(player.data() + offset, &original, 4);
    if (hook.id == SPARTAN_CHEAT_HEALTH) memcpy(player.data() + 0x1d0, &health_max, 4);
    flag.store(true);
    execute(player.data(), 5);
    memcpy(&result, player.data() + offset, 4);
    const float expected[SPARTAN_CHEAT_ORBS + 1] = {250,100,100,1000000000,999999};
    assert(result == expected[hook.id] && player[0x300] == 1);
    memcpy(&preserved, player.data() + 0x308, 8);
    assert(preserved == 0x12345678);
    if (hook.id == SPARTAN_CHEAT_HEALTH) {
        player[0x298] = 1;
        execute(player.data(), 7);
        memcpy(&result, player.data() + offset, 4);
        assert(result == 7);
    }
    flag.store(false);
    execute(player.data(), 3);
    memcpy(&result, player.data() + offset, 4);
    assert(result == 3);
    FreeCode(executable);
}

// Ids 5 and 6: the hooked instructions set the flags the next conditional jump reads. The harness
// enters with RDI = R12 = player, ECX = the given value and RAX = 0x12345678, then records
// "not zero" (ZF clear) at player+0x300 and RAX at player+0x308.
struct FlagsResult {
    bool not_zero;
    uint64_t rax;
};

class FlagsHarness {
public:
    explicit FlagsHarness(const SpartanCheatHook& hook) : code_(AllocCode()) {
        const unsigned char entry[] = {
            0x53,                                      // push rbx
            0x41,0x54,                                 // push r12
            0x49,0x89,0xfc,                            // mov r12, rdi
            0x89,0xf1,                                 // mov ecx, esi
            0x48,0xb8,0x78,0x56,0x34,0x12,0,0,0,0,     // mov rax, 0x12345678
            0xff,0x25,0,0,0,0};                        // jmp [rip] -> the stub
        memcpy(code_, entry, sizeof(entry));
        const uintptr_t target = reinterpret_cast<uintptr_t>(code_ + 64);
        memcpy(code_ + sizeof(entry), &target, 8);
        assert(spartanport_cheat_build_stub(code_ + 64, &hook, reinterpret_cast<uintptr_t>(&flag_),
                                            reinterpret_cast<uintptr_t>(code_ + 256)) < 192);
        const unsigned char exit[] = {
            0x0f,0x95,0x87,0,3,0,0,                    // setnz [rdi+0x300]
            0x48,0x89,0x87,8,3,0,0,                    // mov [rdi+0x308], rax
            0x41,0x5c,                                 // pop r12
            0x5b,                                      // pop rbx
            0xc3};                                     // ret
        memcpy(code_ + 256, exit, sizeof(exit));
        MakeExecutable(code_);
    }
    ~FlagsHarness() { FreeCode(code_); }
    FlagsHarness(const FlagsHarness&) = delete;
    FlagsHarness& operator=(const FlagsHarness&) = delete;

    FlagsResult Run(bool enabled, std::array<unsigned char, 0x600>& player, int ecx) {
        flag_.store(enabled);
        player[0x300] = 0xcc;
        using Execute = void (__attribute__((sysv_abi)) *)(void*, int);
        reinterpret_cast<Execute>(code_)(player.data(), ecx);
        assert(player[0x300] == 0 || player[0x300] == 1);
        FlagsResult result{player[0x300] == 1, 0};
        memcpy(&result.rax, player.data() + 0x308, 8);
        return result;
    }

private:
    unsigned char* code_;
    std::atomic<bool> flag_{false};
};

// VERY_HARD: "mov eax, 3; test cl, 8" then "jne" keeps difficulty 3 only when bit 3 of the stored
// progress is set. Enabled, "mov eax, 3; test eax, eax" always takes that branch (ZF clear); the
// stored progress (ECX) is only read, never written.
void CheckVeryHard(const SpartanCheatHook& hook) {
    assert(hook.id == SPARTAN_CHEAT_VERY_HARD);
    FlagsHarness harness(hook);
    std::array<unsigned char, 0x600> player{};
    for (const int progress : {0x00, 0x08, 0x07, 0xff}) {
        const bool unlocked = (progress & 8) != 0;
        const FlagsResult off = harness.Run(false, player, progress);
        assert(off.not_zero == unlocked && off.rax == 3);
        const FlagsResult on = harness.Run(true, player, progress);
        assert(on.not_zero && on.rax == 3);
    }
    const FlagsResult again = harness.Run(false, player, 0);  // switched off again: original check
    assert(!again.not_zero && again.rax == 3);
}

// DOUBLE_JUMPS: "test byte [r12+0x4ac], 4" then "jnz" refuses a second air jump. Enabled, the
// player (byte [r12+0x298] == 0) gets ZF set, so the jump is allowed; other actors keep the
// original test. RAX is preserved either way.
void CheckDoubleJumps(const SpartanCheatHook& hook) {
    assert(hook.id == SPARTAN_CHEAT_DOUBLE_JUMPS);
    FlagsHarness harness(hook);
    std::array<unsigned char, 0x600> player{};
    for (const unsigned char used : {0, 4, 0xff}) {
        for (const unsigned char other_actor : {0, 1}) {
            player[0x4ac] = used;
            player[0x298] = other_actor;
            const bool blocked = (used & 4) != 0;
            const FlagsResult off = harness.Run(false, player, 0);
            assert(off.not_zero == blocked && off.rax == 0x12345678);
            const FlagsResult on = harness.Run(true, player, 0);
            assert(on.not_zero == (other_actor ? blocked : false) && on.rax == 0x12345678);
            assert(player[0x4ac] == used && player[0x298] == other_actor);  // read only
        }
    }
    player[0x4ac] = 4;
    player[0x298] = 0;
    const FlagsResult again = harness.Run(false, player, 0);  // switched off again: jump refused
    assert(again.not_zero);
}
} // namespace

int main(int argc, char** argv) {
    unsigned char code[256]{};
    std::vector<unsigned char> image;
    if (argc > 1) {
        FILE* file = std::fopen(argv[1], "rb");
        assert(file && !std::fseek(file, 0, SEEK_END));
        const auto size = std::ftell(file);
        assert(size > 0x4000);
        image.resize(size - 0x4000);
        assert(!std::fseek(file, 0x4000, SEEK_SET));
        assert(std::fread(image.data(), 1, image.size(), file) == image.size());
        std::fclose(file);
    }
    unsigned checked[SPARTAN_CHEAT_COUNT] = {};
    for (unsigned i = 0; i < SPARTAN_CHEAT_HOOK_COUNT; ++i) {
        const auto& hook = spartanport_cheat_hooks[i];
        assert(hook.id < SPARTAN_CHEAT_COUNT);
        assert(spartanport_cheat_signature_match(&hook, hook.original, hook.length));
        code[0] = 0;
        assert(!spartanport_cheat_signature_match(&hook, code, hook.length));
        assert(!spartanport_cheat_signature_match(&hook, hook.original, hook.length - 1));
        assert(spartanport_cheat_build_stub(code, &hook, 0x12345678, 0x87654321) < sizeof(code));
        if (!image.empty()) {
            assert(hook.offset + hook.length <= image.size());
            assert(spartanport_cheat_signature_match(&hook, image.data() + hook.offset, image.size() - hook.offset));
        }
        switch (hook.id) {
        case SPARTAN_CHEAT_VERY_HARD:
            CheckVeryHard(hook);
            break;
        case SPARTAN_CHEAT_DOUBLE_JUMPS:
            CheckDoubleJumps(hook);
            break;
        default:
            CheckFloatStore(hook);
            break;
        }
        ++checked[hook.id];
    }
    for (unsigned id = 0; id < SPARTAN_CHEAT_COUNT; ++id) {
        assert(checked[id] > 0);  // every cheat id has at least one hook, and it was run
    }
    std::printf("cheat-hooks-test: OK (%u hooks, %u cheats: live toggles, registers, flags, signatures, "
                "non-player health and jumps)\n",
                unsigned(SPARTAN_CHEAT_HOOK_COUNT), unsigned(SPARTAN_CHEAT_COUNT));
}
