# Basara 4 PC - by Razz

A native PC runtime and launcher for **Sengoku Basara 4 Sumeragi** (PlayStation 4, CUSA01159).
The game's own x86-64 code runs directly on the PC's processor; the PS4 system libraries are
re-implemented per function in C; the GPU commands are translated to Vulkan.

No game files are included. You need a dump from your own console.

## What is new in this project (by Razz, 2026)

Copyright (C) 2026 Razz, released under the GNU GPL v2 or later.

- **Basara 4 support in the runtime** (`src/runtime_offline.c`): the ~130 system functions the game needs
  that the base runtime lacked, among them kernel event flags, AppContent / PlayGo, an offline
  NetCtl / NP / Http model, and the dialog and screenshot services.
- **A software Ngs2 sound graph**: sampler voices (PCM16, float, ATRAC9 through LibAtrac9, repeats,
  skip samples, pitch, streaming rings), submixers that call the game's own effect callbacks and
  masterers that feed AudioOut. Reverb, EQ and filters are not applied yet.
- **Fixes in shared code**: the MsgDialog result layout, `sceKernelGetdents`, the unmapped-joystick
  warning in `runtime_pad.c`.
- **The frame-limiter patch** (`launcher/basara4_patches.py`): finds the game's own frame limiter by code
  signature and removes it, for frame rates above 60.
- **Gamepad mapper** (`tools/gamepad_mapper.c`) for generic DirectInput pads.
- **The Basara 4 PC launcher** (`launcher/basara4_launcher.py`): display mode, window size, FSR 1
  upscaling, sharpening, frame limit, anisotropic filtering, controller mapping, and automatic
  preparation of the game image from `eboot.bin`.
- **The in-game menu for this game** (`gpu/shim/spartanport_overlay.cpp`): the cheat, orb and enemy-bar
  sections of the original were removed; added FSR 1 upscaling and a controller section.
- **`--no-game-hooks`** in the loader: leaves out the signature-based hooks written for another game.
- **LibAtrac9 hardening** (`third_party/LibAtrac9/C/src/huffCodes.c`, `unpack.c`): a damaged ATRAC9 stream no longer
  crashes the decoder.
- **Packaging** (`packaging/basara4/`): the script that assembles the Windows folder, its readmes and the
  DLL list.
- **Audio debug log** (`SPARTAN_AUDIO_DEBUG`, `tools/audio_log_summary.py`, the launcher's "Audio debug log" option).
- **Diagnostics**: the `SPARTAN_NGS2_TRACE`, `SPARTAN_AVPLAYER_TRACE`, `SPARTAN_TIME_TRACE` and
  `SPARTAN_FILE_TRACE` environment switches (off by default).

## Community Credits & Contributors

A special thank you to our passionate community members on Discord for deep research, testing, reporting, and code contributions:

- **@iqzir.adhi (supalii)**: Code contribution (Pull Request #4) for extracted DLC mounting (`/addcont`), dual-user pad arbitration, and XInput mapper integration.
- **@tonysafa4705**: Discovered and verified the AMD Vulkan driver layer collision workaround (`DISABLE_LAYER_AMD_SWITCHABLE_GRAPHICS_1`).
- **@jambumengkel**: Pioneered extracted PS4 DLC packaging, costume test archives, and savedata mod verification.
- **Community Bug Testers & Log Analysts**: @warvon, @afan1241, @vhierm, @alansyahcohitta, @giyoliyoliyo, @zu1343, @jagen1010, @luthfitokisaki, @nobarpov, @noan2736, @ilhamamongus, @loghoriz0n, @kazam4839, @luckser.

## Community & Official Links

- **Discord Community**: https://discord.gg/UBZc57bBMa (Setup help, controller configs & gaming community)
- **TikTok**: https://www.tiktok.com/@razzconnect (@razzconnect - Devlogs, gameplay showcases & updates)
- **Support Solo Dev**: https://sociabuzz.com/razzgame/tribe

## What this project is built on

This is a fork. The architecture, the loader, most of the runtime and the whole GPU side come from
other people's work and keep their licenses and copyright notices:

| Work | Author | License |
|---|---|---|
| **SpartanPort** (formerly `gow3_pc`), the PC runtime this project forks | Felipe Prado | GPL-2.0-or-later |
| **bloodborne_pc for Windows**, the fork SpartanPort comes from | Supermedo (Mohammed Albarghouthi) | GPL-2.0-or-later |
| **bloodborne_pc**, the original native port | deadinside28 | GPL-2.0-or-later |
| **shadPS4** video core: PS4 GPU commands and shader translation to Vulkan; NID name table (`aerolib`) and header layouts used as a reference | shadPS4 team | GPL-2.0-or-later |
| GoW III rendering fixes in the video core (blend, depth/stencil, image aliasing) | cuesta4 | GPL-2.0-or-later |
| **LibAtrac9** | Alexander Barney | MIT |
| FFmpeg, SDL3, Dear ImGui, sirit, magic_enum, miniz, xbyak, Vulkan Memory Allocator, FSR-Vulkan, AMD FidelityFX SDK, MSYS2 / LLVM | their authors | their own licenses (see `third_party/`) |

The rest of the repository (the upstream launcher `launcher/spartanport_*.py`, the God of War III patches,
cheat and multiplier hooks, `docs/`) is SpartanPort's and is kept unchanged so the history and credit
stay intact; the Basara 4 build does not use it (`--no-game-hooks`).

Files that come from these projects keep their original headers. Where this project changed them, the
change is described in the file or in the commit history.

## License

GNU General Public License, version 2 or (at your option) any later version. See `LICENSE`.
Anyone who receives a build of this project is entitled to its source code, including these changes.
"Sengoku Basara" is a trademark of Capcom. This project is not affiliated with Capcom or Sony Interactive Entertainment.
