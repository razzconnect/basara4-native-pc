Basara 4 PC - by Razz  (Bahasa Indonesia)
=========================================
Sengoku Basara 4 Sumeragi (PS4, CUSA01159) berjalan native di Windows.
Software bebas, GNU GPL v2 atau lebih baru. Kredit: NOTICE.md. File game tidak disertakan.

Cara main
  1. Taruh dump game milik sendiri (folder berisi eboot.bin) di "game\", atau tekan "Browse"
     di launcher dan pilih folder lain.
  2. Buka "Sengoku Basara 4.exe", tekan PLAY. Start pertama menyiapkan image game dari eboot.bin
     (sekitar satu menit) dan diulang otomatis kalau eboot.bin berubah (patch, update).
  "Play Sengoku Basara 4.cmd" langsung menjalankan game dengan pengaturan terakhir.

Kebutuhan
  Windows 10 (1903+) atau 11, 64-bit. CPU dengan AVX. Kartu grafis dengan Vulkan 1.3 dan driver terbaru.

Isi folder
  Sengoku Basara 4.exe   launcher: tampilan, resolusi, upscaling, frame rate, controller
  bin\        runtime (basara4-core.exe), mapper controller, dan DLL-nya
  game\       dump game
  out\        image game yang sudah disiapkan (dibuat otomatis)
  user\       save, shader cache, basara4.ini, last_run.log, prepare.log
  scripts\    skrip persiapan image
  launcher\   sumber launcher

Opsi launcher
  Display mode      Windowed, Borderless, Exclusive
  Window size       Native, 720p, 900p, 1440p, 1800p, 4K (game sendiri merender di 1080p)
  Upscaling         Off, atau FSR 1 ke ukuran jendela
  Frame limit       60 (default), 90, 120, 144, 165, 240, Unlimited
  VSync, sharpening (RCAS), anisotropic filtering 2x-16x
  Controller        Controller Mapping (Map all, Map sticks only, Reset), Keyboard & Mouse Controls
  Game              Cheats..., Manage DLC... (di kanan judul Game)
  Tracer, Report    untuk melaporkan masalah gambar atau suara (lihat di bawah)
  Credits           menampilkan NOTICE.md

FPS di atas 60
  Limiter frame milik game dimatikan lewat patch memori (file game tidak diubah). Ini eksperimental:
  game menskalakan gerakannya dengan waktu frame nyata, tetapi kalau ada yang terasa lebih cepat atau
  glitch, kembali ke 60. Matikan VSync, kalau tidak refresh rate monitor menjadi batasnya.

DLC hasil ekstrak
  Gunakan "Manage DLC..." untuk memilih folder induk yang berisi satu folder per add-on.
  Tiap folder add-on harus bernama entitlement label 16 karakter dan berisi sce_sys\param.sfo.
  Folder yang didukung tersedia saat game dimulai. File PKG mentah tidak dibuka atau didekripsi.

Menu in-game
  Insert, atau R3 + L2 di controller. F12 mengambil screenshot. L1/R1 atau Q/E berpindah tab.

Controller
  XInput dan pad yang didukung SDL langsung bisa dipakai. "Controller Mapping" membuka jendela Controller:
  tekan Map all, tekan tiap tombol saat diminta, lalu Done menyimpan; Map sticks only hanya memetakan
  stick; Reset menghapus mapping tersimpan (bertanya dulu). Bisa untuk pad apa saja, memilih salah satu
  bila terhubung lebih dari satu, dan menyimpan mapping pad lain. Untuk menjadikan Xbox A sebagai Circle,
  tekan Xbox B pada prompt Cross dan Xbox A pada prompt Circle.
  Untuk stick, jendela menunjukkan arah dorongnya. Sesudahnya ia menunjukkan apa yang akan diterima game
  dari tiap stick: kalau arahnya lain dari jempolmu, centang Flip, atau petakan ulang stick itu.

Masalah
  Setelah crash, tekan Save report di jendela yang muncul. Satu zip dibuat di user\reports dan disorot di
  jendela folder; seret zip itu ke postinganmu di Discord. Zip berisi spek PC, pengaturan, dan log sesi itu
  dengan nama akun dan nama folder disamarkan, dan tanpa dump crash.
  Kirim hanya zip itu. Berkas mentah di user\ (last_run.log, prepare.log, audio_debug.log, render_traces,
  captures, crash\*.dmp) tidak disamarkan: jangan dikirim. Jangan pernah kirim file game.

Masalah gambar, shader, dan suara (glitch, musik atau suara hilang)
  Centang Tracer di launcher dan main sampai masalahnya terjadi. Tekan F11 saat terlihat (menyimpan
  screenshot dan keadaan frame itu), tutup game, lalu tekan Report di sebelah Tracer. Jendela kecil
  bertanya "What did you see, and what did you press?": beberapa kata (sampai 1000 huruf, opsional) paling
  membantu. Nama akun dan nama PC disamarkan di dalamnya seperti di tempat lain. Zip lalu juga berisi seluruh
  render trace, log audio (user\audio_debug.log, satu baris per kejadian suara), semua capture F11 sesi itu,
  dan catatan apa saja yang tidak ikut. Hanya berkas sesi itu yang masuk, dan zip tetap di bawah 9 MB: teks
  dulu, screenshot terakhir, dengan daftar yang tidak muat. Screenshot F11 dikompres di dalam zip supaya
  beberapa muat (berkas di user\captures tidak diubah): tanpa kehilangan bila muat, kalau tidak setiap nilai
  warna dibulatkan paling banyak beberapa dari 255; report.txt mencatat tiap gambar dan cara yang dipakai.
  Audio mentah (WAV) tidak pernah masuk zip. "+ WAV capture" untuk developer.
  Untuk membaca log audio sendiri: python tools/audio_log_summary.py user/audio_debug.log (dari repositori sumber).

Pembersihan otomatis
  Saat launcher dibuka, ia menyimpan berkas Tracer (gambar F11 dan folder render trace) dari 20 sesi terakhir
  dan menghapus yang lebih lama. Sesi biasa tanpa Tracer ikut dihitung, jadi salin dulu gambar F11 yang
  ingin kamu simpan. Zip di user\reports juga disimpan 20 terakhir. user\audio_wav (WAV capture developer)
  tidak dihapus otomatis: launcher bertanya dulu sebelum menghapusnya (di atas 50 MB, jawaban awal No).
