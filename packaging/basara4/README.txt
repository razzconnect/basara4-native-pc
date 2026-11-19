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
  Use "Manage DLC..." to choose the parent folder containing one folder per add-on.
  Each add-on folder must be named with its 16-character entitlement label and contain
  sce_sys\param.sfo. Supported folders are available when the game starts. Raw PKG files are
  not opened or decrypted.

In-game menu
  Insert, or R3 + L2 on the controller. F12 takes a screenshot. L1/R1 or Q/E switch tabs.

Controller
  XInput and SDL-supported pads work directly. "Map controller" opens the Controller window: press
  Map all, press each button when it is asked for, then Done saves. It can customize any pad, lets you
  select one when multiple are connected, and keeps mappings for other pads. To make Xbox A act
  as Circle, press Xbox B at the Cross prompt and Xbox A at the Circle prompt.
  For the sticks the window shows which way to push. Afterwards it shows what the game will receive
  from each stick: if that goes another way than your thumb, tick Flip, or map the stick again.

Problems
  After a crash, press Save report in the window that appears. It makes one zip in user\reports and shows it
  in a folder window; drag that zip into your post on Discord. The zip holds your PC specs, settings and the
  logs of that session with your account and folder names hidden, and no crash dump.

Graphics, shader and audio problems (a glitch, missing music or voices)
  Tick Tracer in the launcher and play until the problem happens. Press F11 when you see it (it saves a
  screenshot and the state of the frame), close the game, and press Report next to Tracer. A small window asks
  "What did you see, and what did you press?": a few words (up to 1000 characters, optional) help most. Your
  account and PC name are hidden in them like everywhere else. The zip then also holds the whole render trace,
  the audio log (user\audio_debug.log, one line per audio event), every F11 capture of that session, and a note
  of anything left out. Only files of that session go in, and the zip stays below 9 MB: text first,
  screenshots last, with a list of what did not fit. The F11 screenshots are compressed inside the zip so that
  several fit (the files in user\captures are not changed): without loss when there is room, otherwise every
  colour value is rounded by at most a few of 255; report.txt lists each picture and which of the two was used.
  Raw audio (WAV) never goes in the zip. "+ WAV capture" is for developers.
  To read the audio log yourself: python tools/audio_log_summary.py user/audio_debug.log (from the source repository).
