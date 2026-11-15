#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
mkdir -p out
# Third-party code is vendored: committed in this repository with this port's changes already in it
# (no submodules). Each local change is kept as a patch beside the code it changes (patch folder:
# library folder); the build checks that every one of them is in the tree, so a library update that
# dropped one stops here instead of shipping without it. tests/test_vendored_patches.py reads this list.
VENDORED_PATCHES=(
    gpu/patches/fsr-vulkan:gpu/third_party/fsr-vulkan
    third_party/patches/LibAtrac9:third_party/LibAtrac9
)
check_vendored() {  # check_vendored <file>...: required vendored files, then every recorded patch
    local file entry patch missing=()
    for file in "$@"; do
        [[ -f $file ]] || missing+=("$file (missing)")
    done
    command -v git >/dev/null || { echo 'Need git to check the vendored patches.' >&2; exit 1; }
    for entry in "${VENDORED_PATCHES[@]}"; do
        for patch in "${entry%%:*}"/*.patch; do
            git apply --reverse --check --directory="${entry#*:}" "$patch" 2>/dev/null ||
                missing+=("$patch (not in ${entry#*:})")
        done
    done
    if (( ${#missing[@]} )); then
        printf 'Vendored third-party code differs from the repository: %s\n' "${missing[@]}" >&2
        echo 'Restore the committed files: git checkout -- gpu/third_party third_party' >&2
        exit 1
    fi
}
# Windows: MSYS2 CLANG64 (packages in packaging/windows/README.md). The GPU library is static
# there and CMake links spartanport-probe.exe as well; no PGO (GCC-only flags), LTO off by default.
case $(uname -s) in
MINGW*|MSYS*)
    # shellcheck source=msys2-env.sh
    source ./msys2-env.sh
    for tool in clang cmake ninja pkg-config; do
        command -v "$tool" >/dev/null || { echo "Need $tool from MSYS2 CLANG64 (packaging/windows/README.md)." >&2; exit 1; }
    done
    check_vendored gpu/third_party/fsr-vulkan/CMakeLists.txt gpu/third_party/imgui/imgui.h \
        gpu/third_party/xbyak/xbyak/xbyak.h
    cmake -S gpu -B out/gpu -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DSPARTAN_PGO=off \
        -DSPARTAN_LTO="${SPARTAN_LTO:-OFF}" -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ >/dev/null
    echo "GPU library and loader: LTO ${SPARTAN_LTO:-OFF}"
    if ! ninja -C out/gpu spartanport-probe spartanport-gpu-capabilities spartanport-play > out/gpu-build.log 2>&1; then
        grep -v '^\[' out/gpu-build.log | tail -40 >&2
        echo 'Build failed (full log: out/gpu-build.log)' >&2; exit 1
    fi
    cp out/gpu/basara4-core.exe out/gpu/spartanport-gpu-capabilities.exe out/gpu/spartanport-play.exe out/
    echo "Built $PWD/out/basara4-core.exe"
    if [[ ${1:-} == --test ]]; then
        # Every C/C++ test is a *-test target registered with CTest in gpu/CMakeLists.txt (no error
        # dialogs: tests/support/no_dialog.c); the Python tests are discovered from tests/.
        if ! ninja -C out/gpu spartanport-tests > out/gpu-test-build.log 2>&1; then
            grep -v '^\[' out/gpu-test-build.log | tail -40 >&2
            echo 'Test build failed (full log: out/gpu-test-build.log)' >&2; exit 1
        fi
        # The Windows Python that runs the launcher and packaging/basara4/make_package.py (iced_x86,
        # Tk), not MSYS2's: the Windows launcher "py -3" when present. PYTHON overrides it.
        if [[ -n ${PYTHON:-} ]]; then python_cmd=("$PYTHON")
        elif command -v py >/dev/null; then python_cmd=(py -3)
        else python_cmd=(python); fi
        failed=()
        ctest --test-dir out/gpu --output-on-failure || failed+=('C/C++ (ctest)')
        echo "Python tests: $("${python_cmd[@]}" -c 'import sys; print(sys.executable, sys.version.split()[0])')"
        "${python_cmd[@]}" -m unittest discover -s tests || failed+=('Python (unittest)')
        if (( ${#failed[@]} )); then
            echo "Tests failed: ${failed[*]}" >&2; exit 1
        fi
        echo 'All tests passed'
    fi
    exit 0
    ;;
esac
if [[ -z ${CC:-} ]]; then
    CC=$(command -v cc || command -v gcc || true)
    if [[ -z $CC ]]; then
        for candidate in /nix/store/*-gcc-wrapper-*/bin/gcc; do
            if [[ -x $candidate ]]; then CC=$candidate; break; fi
        done
    fi
fi
if [[ -z ${CC:-} ]]; then echo 'Install GCC/Clang or set CC.' >&2; exit 1; fi
# Dependencies come from pkg-config (Vulkan loader/headers, SDL3). On NixOS the
# environment is provided by shell.nix; re-enter it automatically if needed.
if ! { command -v pkg-config >/dev/null && pkg-config --exists vulkan sdl3 && command -v cmake >/dev/null && command -v ninja >/dev/null; }; then
    if [[ -z ${SPARTAN_IN_NIX_SHELL:-} ]] && command -v nix-shell >/dev/null; then
        exec env SPARTAN_IN_NIX_SHELL=1 nix-shell shell.nix --run "bash build.sh $*"
    fi
    echo 'Need pkg-config with vulkan and sdl3, cmake and ninja (see shell.nix).' >&2; exit 1
fi
read -r -a includes <<< "$(pkg-config --cflags vulkan sdl3 libavcodec libavutil libswscale)"
read -r -a libraries <<< "$(pkg-config --libs vulkan sdl3 libavcodec libavutil libswscale)"
# GPU library (shadPS4 video core + drivers), built by CMake into out/gpu/libspartanportgpu.so.
# SPARTAN_PGO: generate (instrumented build that writes pgo/ while the game runs), use, off.
# Default: use the profile in pgo/ when there is one. SPARTAN_LTO=OFF disables link-time optimization.
pgo=${SPARTAN_PGO:-}
if [[ -z $pgo ]]; then
    if [[ -n $(find pgo -name '*.gcda' -print -quit 2>/dev/null) ]]; then pgo=use; else pgo=off; fi
fi
mkdir -p pgo
check_vendored gpu/third_party/fsr-vulkan/CMakeLists.txt gpu/third_party/imgui/imgui.h
cmake -S gpu -B out/gpu -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DSPARTAN_PGO="$pgo" \
    -DSPARTAN_LTO="${SPARTAN_LTO:-ON}" -DSPARTAN_PGO_DIR="$PWD/pgo" >/dev/null
echo "GPU library: PGO $pgo, LTO ${SPARTAN_LTO:-ON}"
# A failed GPU build must stop here: an older libspartanportgpu.so would otherwise be used silently.
if ! ninja -C out/gpu spartanportgpu > out/gpu-build.log 2>&1; then
    grep -v '^\[' out/gpu-build.log | tail -40 >&2
    echo 'GPU library build failed (full log: out/gpu-build.log)' >&2; exit 1
fi
# $ORIGIN/gpu: packaged copies keep the library next to the binary without patching it.
gpu=(-Lout/gpu -lspartanportgpu -Wl,-rpath,'$ORIGIN/gpu' -Wl,-rpath,"$PWD/out/gpu" -rdynamic)
runtime=(src/runtime*.c)
# Third-party decoders: compiled once, without this project's -Werror policy.
atrac9=(third_party/LibAtrac9/C/src/*.c)
if [[ ! -f out/libatrac9.a || -n $(find third_party/LibAtrac9/C/src -newer out/libatrac9.a -name '*.c') ]]; then
    rm -rf out/atrac9 && mkdir -p out/atrac9
    for source in "${atrac9[@]}"; do "$CC" -std=c99 -O2 -g -w -c "$source" -o "out/atrac9/$(basename "${source%.c}").o"; done
    ar rcs out/libatrac9.a out/atrac9/*.o
fi
# isa_emulate runs inside guest code: no XMM/YMM.
"$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -mgeneral-regs-only -c src/isa_emulate.c -o out/isa_emulate.o
"$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -no-pie "${includes[@]}" -I. -Isrc src/probe.c "${runtime[@]}" src/cpu_check.c src/vulkan_smoke.c out/isa_emulate.o src/isa_rewrite.c out/libatrac9.a -lm "${gpu[@]}" "${libraries[@]}" -o out/spartanport-probe
echo "Built $PWD/out/spartanport-probe"
# GPU check for run.sh (live_resolution=auto): links only the Vulkan loader.
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -Igpu/shadps4 tools/gpu_capabilities.c "${libraries[@]}" -o out/spartanport-gpu-capabilities
if [[ ${1:-} == --test ]]; then
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread "${includes[@]}" -I. -Isrc tests/test_pad.c "${libraries[@]}" -o out/pad-test
    out/pad-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -I. -Isrc tests/test_runtime.c "${runtime[@]}" out/libatrac9.a -lm "${gpu[@]}" "${libraries[@]}" -o out/runtime-test
    out/runtime-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread "${includes[@]}" -I. -Isrc tests/test_audio.c "${libraries[@]}" -o out/audio-test
    out/audio-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -Isrc tests/test_file_mods.c -o out/file-mods-test
    out/file-mods-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -I. -Isrc tests/test_sema.c "${runtime[@]}" out/libatrac9.a -lm "${gpu[@]}" "${libraries[@]}" -o out/sema-test
    out/sema-test
    "$CC" -std=c11 -D_GNU_SOURCE -O2 -g -Wall -Wextra -Werror -I. -Isrc tests/test_content.c src/runtime_content.c -o out/content-test
    out/content-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread "${includes[@]}" -I. -Isrc tests/test_mp3.c src/runtime_mp3.c out/libatrac9.a -lm "${libraries[@]}" -o out/mp3-test
    out/mp3-test tests/data/sine.mp3
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread "${includes[@]}" -I. -Isrc tests/test_videodec.c "${libraries[@]}" -o out/videodec-test
    out/videodec-test tests/data/tiny.h264
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -I. -Isrc tests/test_services.c -o out/services-test
    out/services-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -mbmi -mlzcnt -mmovbe -I. -Isrc tests/test_isa_emulate.c out/isa_emulate.o -o out/isa-emulate-test
    out/isa-emulate-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -c src/isa_rewrite.c -I. -Isrc -o out/isa_rewrite.o
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -mbmi -mlzcnt -I. -Isrc tests/test_isa_stub.c out/isa_rewrite.o out/isa_emulate.o -o out/isa-stub-test
    out/isa-stub-test tests/data/isa_fixture.bin
    ninja -C out/gpu graphics-settings-test
    out/gpu/graphics-settings-test
fi
