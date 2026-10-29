# Sengoku Basara 4 Sumeragi for PC (Basara 4 PC)

<div align="center">

![C++](https://img.shields.io/badge/Language-C%2B%2B20%20%2F%20C17-00599C?logo=c%2B%2B)
![Python](https://img.shields.io/badge/Launcher-Python%203.11-3776AB?logo=python)
![Vulkan](https://img.shields.io/badge/Graphics-Vulkan%201.3-E10019?logo=vulkan)
![License](https://img.shields.io/badge/License-GPL%20v2.0%2B-blue.svg)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-5865F2?logo=discord&logoColor=white)](https://discord.gg/UBZc57bBMa)
[![TikTok](https://img.shields.io/badge/TikTok-@razzconnect-000000?logo=tiktok&logoColor=white)](https://www.tiktok.com/@razzconnect)

</div>

**English** | [Bahasa Indonesia](README.id.md)

An open-source native PC port and launcher for **Sengoku Basara 4 Sumeragi** (PlayStation 4, CUSA01159) for Windows 10/11.

The game's original x86-64 code executes directly on your PC's CPU without emulation. The PlayStation 4 system libraries are re-implemented function by function in native C/C++, and GPU commands are translated to Vulkan 1.3 in real-time.

> 💬 **Discord Community**: [https://discord.gg/UBZc57bBMa](https://discord.gg/UBZc57bBMa) — Setup help, controller configs & gaming community  
> 🎬 **TikTok**: [@razzconnect](https://www.tiktok.com/@razzconnect) ([https://www.tiktok.com/@razzconnect](https://www.tiktok.com/@razzconnect)) — Development progress, showcases & devlogs  
> ☕ **Support Solo Dev**: [https://sociabuzz.com/razzgame/tribe](https://sociabuzz.com/razzgame/tribe)

> **No copyrighted game files are included.** You must provide your own legally dumped copy of the game from your PlayStation 4 console (tested: CUSA01159).
>
> This project is an independent open-source reverse-engineering and preservation project made by **Razz**.
> Not affiliated with, endorsed by, or associated with Capcom or Sony Interactive Entertainment.
> "Sengoku Basara" is a trademark of Capcom Co., Ltd. "PlayStation" is a trademark of Sony Interactive Entertainment.

---

## Contents

- [Features & Highlights](#features--highlights)
- [System Requirements](#system-requirements)
- [Download and Play](#download-and-play)
- [Launcher Settings](#launcher-settings)
- [In-Game Menu & Hotkeys](#in-game-menu--hotkeys)
- [Controller Support](#controller-support)
- [How It Works (Technical Architecture)](#how-it-works-technical-architecture)
- [Building from Source](#building-from-source)
- [Troubleshooting & Logs](#troubleshooting--logs)
- [Credits & Acknowledgments](#credits--acknowledgments)
- [Community & Socials](#community--socials)
- [License](#license)

---

## Features & Highlights

- **100% Native CPU Execution**: No CPU interpreter or JIT compiler. The game's compiled x86-64 instructions run directly on host CPU cores with near-zero overhead.
- **Custom Software Ngs2 Audio Engine**: Reverse-engineered and implemented software Ngs2 sound graph handling multi-channel ATRAC9 streaming, LibAtrac9 decoding, voice synthesis, PCM playback, and submixing.
- **Vulkan 1.3 Graphics Translation**: High-performance translation of PlayStation 4 AMD GCN shader bytecode and draw calls directly into SPIR-V / Vulkan.
- **Custom Basara 4 PC Launcher**: Built from scratch with a themed Sengoku aesthetic, resolution picker, display modes, anisotropic filtering, FSR 1 upscaling, and controller configuration.
- **True Unlimited FPS Unlock**: Integrated memory patch (`basara4_patches.py`) that scans for the engine's internal frame limiter and disables it, unlocking 90, 120, 144, 165, 240, and uncapped FPS on high-refresh displays.
- **AMD FidelityFX Super Resolution (FSR 1)** & **RCAS Sharpening**: Upscale cleanly from 720p/900p/1080p to 1440p or 4K.
- **Anisotropic Filtering (up to 16x)**: Greatly enhances floor and terrain texture clarity across battlefields.
- **Dynamic Asset & Import Preparation**: Automatic game image preparation pipeline (`prepare.py`, `link_libc.py`, `link_modules.py`, `content_profile.py`) that adapts to official patches without touching your original dump.
- **Universal Gamepad Mapper**: Bundled SDL3 mapper tool (`basara4-pad-mapper.exe`) for DirectInput, generic USB gamepads, and Xbox/PlayStation controllers.

---

## System Requirements

| Component | Minimum | Recommended |
|---|---|---|
| **Operating System** | Windows 10 64-bit (1903 or newer) | Windows 10/11 64-bit (latest) |
| **Processor (CPU)** | 64-bit CPU with AVX support (Intel Sandy Bridge 2011+, AMD FX) | 6-core / 12-thread modern CPU (Intel 10th Gen+ / AMD Ryzen 3000+) |
| **Graphics (GPU)** | Vulkan 1.3 compliant GPU (GTX 1060 / Radeon RX 580) | RTX 2060 / Radeon RX 6600 or higher |
| **System Memory** | 8 GB RAM | 16 GB RAM |
| **Storage** | ~20 GB free space (SSD recommended) | Fast NVMe SSD |
| **Game Copy** | Legal dump of Sengoku Basara 4 Sumeragi (`CUSA01159`) | Dump with update installed |

---

## Download and Play

### 1. Get the Package
Download the latest `basara4-windows.zip` from the [Releases](https://github.com/razzconnect/basara4-native-pc/releases) page and extract it to a directory of your choice (e.g. `C:\Games\basara4-windows`).

### 2. Supply Your Game Dump
Place your decrypted PS4 game dump folder (which contains `eboot.bin`, `sce_module\`, and `sce_sys\`) into the `game\` directory, or click **Browse** in the launcher to select its location.

### 3. Launch and Play
1. Double-click **`Sengoku Basara 4.exe`**.
2. Select your desired resolution, display mode, and frame limit.
3. Click **PLAY**.
   - *Note:* The very first launch will automatically prepare the linked binary image into `out\` (takes roughly 30–60 seconds). Subsequent boots start immediately.
4. To launch directly with your last saved settings without opening the launcher GUI, use **`Play Sengoku Basara 4.cmd`**.

### Extracted DLC

The launcher detects extracted DLC under the `DLC` folder beside the installation, or `user\dlc` when that folder does not exist. Use **Manage extracted DLC…** to select a parent folder. Each add-on must be in a subfolder named with its 16-character entitlement label and contain `sce_sys\param.sfo`. Raw PKG files are not opened or decrypted.

---

## Launcher Settings

| Option | Available Values | Description |
|---|---|---|
| **Display Mode** | Windowed, Borderless Fullscreen, Exclusive Fullscreen | Select your preferred window presentation mode. |
| **Window Size** | Native (1080p), 720p, 900p, 1440p, 1800p, 4K | Output viewport resolution (game renders internally at 1080p). |
| **Upscaling** | Off, FSR 1 | AMD FidelityFX Super Resolution upscaling to target resolution. |
| **Sharpening (RCAS)** | Enabled / Disabled | Robust Contrast Adaptive Sharpening filter. |
| **Anisotropic Filtering**| Off, 2x, 4x, 8x, 16x | Texture filtering quality for oblique camera angles. |
| **Frame Limit** | 60 (Default), 90, 120, 144, 165, 240, Unlimited | Overrides engine cap using automated signature patching. |
| **VSync** | Enabled / Disabled | Synchronize buffer swaps to display refresh rate. |
| **Audio Debug Log** | Enabled / Disabled | Writes granular Ngs2 voice and bus diagnostics to `user\audio_debug.log`. |

---

## In-Game Menu & Hotkeys

- **Insert** (Keyboard) or **R3 + L2** (Gamepad): Opens the in-game overlay menu (FSR settings, Anisotropic filtering, controller indicators, and About).
- **F12**: Captures a screenshot to `user\`.
- **L1 / R1** (or **Q / E**): Switches tabs in the overlay menu.

---

## Controller Support

- **Native XInput & SDL3**: Xbox One / Series, DualShock 4, DualSense, and Nintendo Switch Pro controllers are recognized automatically.
- **Controller Mapping**: XInput and other SDL-supported gamepads work directly. Use **Map controller** to customize button/stick mappings; select the controller when more than one is connected. Mappings for other controllers are preserved in `user\gamepad_mapping.txt`. To make Xbox A act as Circle, press Xbox B at the Cross prompt and Xbox A at the Circle prompt.

---

## How It Works (Technical Architecture)

```
[ PS4 eboot.bin (x86-64 ELF) ]
              │
              ▼  (Direct Execution on Host CPU)
      Host CPU (x86-64)
              │
    ┌─────────┴─────────────────────────────────┐
    │                                           │
    ▼ (System Calls / Libs)                     ▼ (GPU / Draw Calls)
[ C System HLE Runtime ]             [ Vulkan 1.3 Translation Core ]
- runtime_offline.c (~130 contracts) - GCN bytecode -> SPIR-V
- Kernel Events, Threads, Sockets    - FSR 1 / RCAS / Anisotropy
- Software Ngs2 (ATRAC9, PCM)        - Vulkan Memory Allocator
- LibAtrac9 Decoder (Hardened)                  │
    │                                           ▼
    ▼                                      Host GPU (Vulkan)
AudioOut / DirectSound / WASAPI
```

1. **Native CPU Execution**: Unlike traditional console emulators (e.g. RPCS3), no CPU translation layer is required. Both the PS4 and modern PCs share the AMD64 (x86-64) architecture.
2. **Reverse-Engineered System Contracts (`src/runtime_offline.c`)**: Capcom's MT Framework engine for Basara 4 relies on ~130 PlayStation 4 OS functions not found in base SpartanPort. These were reverse-engineered and implemented natively, including:
   - Kernel event flags and multi-thread sync primitives.
   - Offline AppContent / PlayGo container virtualisation.
   - Offline NetCtl / NP / HTTP stubs to satisfy network checks.
   - Message dialog and screenshot service bridges.
3. **Software Ngs2 Sound Graph (`src/runtime_audio.c`)**: Basara 4 routes all background music, dialogue, and combat SFX through Sony's proprietary Ngs2 library. This project implements a software Ngs2 graph featuring voice samplers, loop point handling, pitch scaling, ring buffer streaming, and multi-channel master buses.
4. **Hardened LibAtrac9 Decoder**: Incorporates security and boundary hardening within `third_party/LibAtrac9` (`huffCodes.c`, `unpack.c`, `band_extension.c`) to gracefully handle edge-case stream corruptions without crashing.
5. **On-the-Fly Memory Patching (`launcher/basara4_patches.py`)**: Locates the game's internal frame throttle instructions via byte pattern matching and removes the cap before execution begins.

---

## Building from Source

### Prerequisites

1. **MSYS2** with the **CLANG64** environment installed:
   ```bash
   pacman -S --needed base-devel mingw-w64-clang-x86_64-toolchain \
       mingw-w64-clang-x86_64-cmake mingw-w64-clang-x86_64-ninja \
       mingw-w64-clang-x86_64-vulkan-headers mingw-w64-clang-x86_64-vulkan-loader \
       mingw-w64-clang-x86_64-sdl3 mingw-w64-clang-x86_64-ffmpeg \
       mingw-w64-clang-x86_64-glslang mingw-w64-clang-x86_64-pkg-config
   ```
2. **Python 3.10+** (Windows host installation) with `PyInstaller` and `Pillow`:
   ```powershell
   python -m pip install pyinstaller pillow
   ```

### Compilation Steps

1. Clone the repository recursively:
   ```bash
   git clone --recursive https://github.com/razzconnect/basara4-native-pc.git
   cd basara4-pc
   ```

2. Open the **MSYS2 CLANG64** terminal and build the native runtime:
   ```bash
   bash build.sh
   ```
   *This compiles `out/gpu/basara4-core.exe`.*

3. In PowerShell, assemble the distribution package:
   ```powershell
   python packaging/basara4/make_package.py --out dist/basara4-windows
   ```

To update an existing release folder without disturbing your `game\`, `user\`, or `out\` cache:
```powershell
python packaging/basara4/make_package.py --out G:/basara4-windows --update
```

---

## Troubleshooting & Logs

When reporting issues or bugs, please attach the relevant logs from the `user\` folder:
- **`user\last_run.log`**: Core runtime diagnostic log (initialization, GPU shader compiling, crash traceback).
- **`user\prepare.log`**: Output from the image preparation and relocation stage.
- **`user\audio_debug.log`**: Detailed Ngs2 voice allocations, streaming feeds, and buffer status (enable "Audio debug log" in launcher).

To summarize audio logs from command line:
```bash
python tools/audio_log_summary.py user/audio_debug.log
```

---

## Credits & Acknowledgments

This project is built upon open-source foundations and prior reverse-engineering efforts:

| Project / Component | Author(s) | Role & Contribution | License |
|---|---|---|---|
| **Basara 4 PC Port** | **Razz** | Port author, reverse-engineering of Basara 4 system contracts (`runtime_offline.c`), software Ngs2 implementation, LibAtrac9 hardening, custom launcher GUI, frame-limiter patch, pad mapper, packaging | GPL-2.0+ |
| **SpartanPort** (`gow3_pc`) | **Felipe Prado** | Upstream base native runtime architecture, probe loader, and core harness | GPL-2.0+ |
| **bloodborne_pc** | **Supermedo** & **deadinside28** | Foundational PS4 native PC porting methodology and runtime libraries | GPL-2.0+ |
| **shadPS4** | **shadPS4 Team** | Translated GPU core, shader translation to SPIR-V/Vulkan, NID table (`aerolib`) | GPL-2.0+ |
| **LibAtrac9** | **Alexander Barney** | ATRAC9 decompression library | MIT |
| **GPU Rendering Fixes** | **cuesta4** | Blend and depth/stencil emulation fixes in video core | GPL-2.0+ |
| **FSR-Vulkan** | **FireBurn** / AMD | FidelityFX Super Resolution Vulkan integration | MIT |
| **Libraries** | Various Authors | SDL3, Dear ImGui, Vulkan Memory Allocator, sirit, xbyak, miniz | Respective licenses |

Full upstream notices, legal attributions, and copyright declarations are documented in [NOTICE.md](NOTICE.md).

---

## Community & Socials

Join our community for discussions, troubleshooting, updates, and more:

- **Discord Community**: [https://discord.gg/UBZc57bBMa](https://discord.gg/UBZc57bBMa) (general community & gaming)
- **TikTok**: [@razzconnect](https://www.tiktok.com/@razzconnect) ([https://www.tiktok.com/@razzconnect](https://www.tiktok.com/@razzconnect)) — development progress & updates

---

## License

This project is licensed under the **GNU General Public License v2.0 or later** ([LICENSE](LICENSE)).

- All modifications, reverse-engineered engine contracts, custom audio graph code, and launcher source code created by Razz are Copyright (C) 2026 Razz under GPL-2.0-or-later.
- Third-party components and submodules retain their respective authors' licenses as described in `NOTICE.md` and `third_party/`.
