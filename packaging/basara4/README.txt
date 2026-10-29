Basara 4 PC - by Razz
=====================
Sengoku Basara 4 Sumeragi (PS4, CUSA01159) running natively on Windows.
Free software, GNU GPL v2 or later. Credits: NOTICE.md. No game files are included.

How to play
  1. Put your own game dump (the folder that contains eboot.bin) in "game\", or press
     "Browse" in the launcher and choose it somewhere else.
  2. Open "Sengoku Basara 4.exe" and press PLAY. The first start prepares the game image from
     eboot.bin (about a minute). It is redone by itself if eboot.bin changes (a patch, an update).
  "Play Sengoku Basara 4.cmd" starts the game straight away with the last settings.

Requirements
  Windows 10 (1903+) or 11, 64-bit. A CPU with AVX. A graphics card with Vulkan 1.3 and a current
  driver.

Folder contents
  Sengoku Basara 4.exe   launcher: display, resolution, upscaling, frame rate, controller
  bin\        the runtime (basara4-core.exe), the controller mapper and their DLLs
  game\       the game dump
  out\        the prepared game image (made automatically)
  user\       saves, shader cache, basara4.ini, last_run.log, prepare.log
  scripts\    the image preparation scripts
  launcher\   the launcher source

Launcher options
  Display mode      Windowed, Borderless, Exclusive
  Window size       Native, 720p, 900p, 1440p, 1800p, 4K (the game itself renders at 1080p)
  Upscaling         Off, or FSR 1 to the window size
  Frame limit       60 (default), 90, 120, 144, 165, 240, Unlimited
  VSync, sharpening (RCAS), anisotropic filtering 2x-16x
  Controller        Map controller / Map sticks only / Reset mapping
  Credits           shows NOTICE.md

Frame rates above 60
  The game's own frame limiter is switched off by a memory patch (the game files are not changed).
  This is experimental: the game scales its movement by real frame time, but if anything feels
  faster or glitchy, go back to 60. Turn VSync off, or the monitor refresh rate is the limit.

Extracted DLC
  Use "Manage extracted DLC..." to choose the parent folder containing one folder per add-on.
  Each add-on folder must be named with its 16-character entitlement label and contain
  sce_sys\param.sfo. Supported folders are available when the game starts. Raw PKG files are
  not opened or decrypted.

In-game menu
  Insert, or R3 + L2 on the controller. F12 takes a screenshot. L1/R1 or Q/E switch tabs.

Controller
  XInput and SDL-supported pads work directly. Any other pad: "Map controller" in the launcher.

Problems
  Attach user\last_run.log (and user\prepare.log if the preparation failed) to your report.

Audio problems (missing music or voices)
  Tick "Audio debug log" in the launcher and play until the problem happens. user\audio_debug.log has one line
  per audio event: the sound ports, the sound-engine voices (what type, how long, whether they reach the
  output), loud/silent windows, and the sound files and stages opened. Send that file with last_run.log.
  The previous run's log is kept as audio_debug.previous.log. A log stops growing at 64 MB.
  To read it yourself: python tools/audio_log_summary.py user/audio_debug.log (from the source repository).
