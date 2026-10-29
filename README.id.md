# Sengoku Basara 4 Sumeragi PC Port (Basara 4 PC)

<div align="center">

![C++](https://img.shields.io/badge/Language-C%2B%2B20%20%2F%20C17-00599C?logo=c%2B%2B)
![Python](https://img.shields.io/badge/Launcher-Python%203.11-3776AB?logo=python)
![Vulkan](https://img.shields.io/badge/Graphics-Vulkan%201.3-E10019?logo=vulkan)
![License](https://img.shields.io/badge/License-GPL%20v2.0%2B-blue.svg)
[![Discord](https://img.shields.io/badge/Discord-Gabung%20Komunitas-5865F2?logo=discord&logoColor=white)](https://discord.gg/UBZc57bBMa)
[![TikTok](https://img.shields.io/badge/TikTok-@razzconnect-000000?logo=tiktok&logoColor=white)](https://www.tiktok.com/@razzconnect)

</div>

[English](README.md) | **Bahasa Indonesia**

Port PC native open-source dan launcher untuk **Sengoku Basara 4 Sumeragi** (PlayStation 4, CUSA01159) untuk Windows 10/11.

Kode x86-64 asli game berjalan langsung di prosesor PC Anda tanpa emulator (tanpa interpreter maupun JIT). Library sistem PlayStation 4 diimplementasikan ulang fungsi demi fungsi ke dalam bahasa C/C++, dan instruksi GPU diterjemahkan langsung ke Vulkan 1.3 secara real-time.

> 💬 **Komunitas Discord**: [https://discord.gg/UBZc57bBMa](https://discord.gg/UBZc57bBMa) — Panduan instalasi, konfigurasi controller & diskusi komunitas  
> 🎬 **TikTok**: [@razzconnect](https://www.tiktok.com/@razzconnect) ([https://www.tiktok.com/@razzconnect](https://www.tiktok.com/@razzconnect)) — Update progres porting, gameplay & devlog  
> ☕ **Dukungan Solo Dev**: [https://sociabuzz.com/razzgame/tribe](https://sociabuzz.com/razzgame/tribe)

> **Tidak menyertakan file game berhak cipta.** Anda wajib menyediakan dump game hasil ekstrak legal dari konsol PlayStation 4 milik sendiri (teruji: CUSA01159).
>
> Proyek ini adalah riset open-source independen yang dikembangkan dan di-reverse-engineer oleh **Razz**.
> Tidak berafiliasi dengan, didukung oleh, atau terkait dengan Capcom maupun Sony Interactive Entertainment.
> "Sengoku Basara" adalah merek dagang milik Capcom Co., Ltd. "PlayStation" adalah merek dagang milik Sony Interactive Entertainment.

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Spesifikasi Sistem](#spesifikasi-sistem)
- [Cara Download & Menjalankan](#cara-download--menjalankan)
- [Pengaturan Launcher](#pengaturan-launcher)
- [Menu In-Game & Hotkey](#menu-in-game--hotkey)
- [Dukungan Controller](#dukungan-controller)
- [Arsitektur Teknis (Cara Kerja)](#arsitektur-teknis-cara-kerja)
- [Build dari Source Code](#build-dari-source-code)
- [Troubleshooting & Log](#troubleshooting--log)
- [Kredit & Kontributor](#kredit--kontributor)
- [Komunitas & Media Sosial](#komunitas--media-sosial)
- [Lisensi](#lisensi)

---

## Fitur Utama

- **Eksekusi CPU 100% Native**: Tanpa emulasi CPU. Instruksi x86-64 binary game berjalan langsung di core prosesor PC dengan latensi dan overhead minimal.
- **Audio Engine Software Ngs2 Kustom**: Implementasi penuh sound graph Ngs2 untuk menangani streaming multi-channel ATRAC9, decoding LibAtrac9, sintesis suara, pemutaran PCM, dan submixing.
- **Translasi Grafis Vulkan 1.3**: Menerjemahkan shader AMD GCN PS4 ke SPIR-V / Vulkan secara langsung dengan performa tinggi.
- **Launcher Resmi Basara 4 PC**: Dibuat khusus oleh Razz dengan tema visual Sengoku, pilihan resolusi, mode layar, anisotropic filtering, FSR 1 upscaling, dan kalibrasi controller.
- **Patch FPS Unlimited**: Scanner signature memory otomatis (`basara4_patches.py`) yang mematikan pembatas FPS bawaan game (bisa main di 90, 120, 144, 165, 240, hingga unlimited FPS).
- **AMD FidelityFX Super Resolution (FSR 1)** & **RCAS Sharpening**: Upscaling bersih ke 1440p atau 4K.
- **Anisotropic Filtering (hingga 16x)**: Mempertajam tekstur lantai, tanah, dan arena pertempuran dari sudut kamera miring.
- **Pipeline Relokasi & Binding Otomatis**: Skrip otomatis (`prepare.py`, `link_libc.py`, `link_modules.py`, `content_profile.py`) yang menyiapkan executable dari dump tanpa merusak file asli.
- **Universal Gamepad Mapper**: Tool pemetaan berbasis SDL3 (`basara4-pad-mapper.exe`) untuk stik DirectInput, USB generik, maupun layout Xbox/PlayStation.

---

## Spesifikasi Sistem

| Komponen | Minimum | Rekomendasi |
|---|---|---|
| **Sistem Operasi** | Windows 10 64-bit (1903 ke atas) | Windows 10/11 64-bit |
| **Prosesor (CPU)** | 64-bit dengan AVX (Intel Sandy Bridge 2011+, AMD FX) | 6 Core / 12 Thread (Intel Gen 10+ / AMD Ryzen 3000+) |
| **Kartu Grafis (GPU)** | GPU mendukung Vulkan 1.3 (GTX 1060 / RX 580) | RTX 2060 / RX 6600 atau lebih baru |
| **RAM** | 8 GB RAM | 16 GB RAM |
| **Penyimpanan** | ~20 GB ruang kosong (SSD disarankan) | NVMe SSD |
| **File Game** | Dump game Sengoku Basara 4 Sumeragi (`CUSA01159`) | Dump dengan update terpasang |

---

## Cara Download & Menjalankan

### 1. Unduh Paket
Unduh arsip terbaru `basara4-windows.zip` dari halaman [Releases](https://github.com/razzconnect/basara4-native-pc/releases) lalu ekstrak ke folder pilihan Anda (misal `C:\Games\basara4-windows`).

### 2. Masukkan Dump Game
Salin folder dump game PS4 Anda (yang berisi `eboot.bin`, `sce_module\`, dan `sce_sys\`) ke dalam folder `game\`, atau klik tombol **Browse** di launcher untuk memilih lokasinya.

### 3. Jalankan Game
1. Buka **`Sengoku Basara 4.exe`**.
2. Atur resolusi, mode layar, dan batas FPS sesuai monitor Anda.
3. Klik **PLAY**.
   - *Catatan:* Boot pertama akan menyiapkan image binary ke folder `out\` (memakan waktu sekitar 30–60 detik). Boot berikutnya langsung jalan instan.
4. Jika ingin langsung membuka game dengan konfigurasi terakhir tanpa membuka GUI launcher, gunakan **`Play Sengoku Basara 4.cmd`**.

### DLC hasil ekstrak

Launcher mendeteksi DLC hasil ekstrak di folder `DLC` di samping folder instalasi, atau `user\dlc` jika folder tersebut belum ada. Pilih folder induk DLC melalui **Manage extracted DLC…**. Setiap add-on harus berada di subfolder bernama entitlement label 16 karakter dan berisi `sce_sys\param.sfo`. File PKG mentah tidak dibuka atau didekripsi.

---

## Pengaturan Launcher

| Opsi | Pilihan | Penjelasan |
|---|---|---|
| **Display Mode** | Windowed, Borderless Fullscreen, Exclusive Fullscreen | Mode tampilan jendela game. |
| **Window Size** | Native (1080p), 720p, 900p, 1440p, 1800p, 4K | Resolusi jendela output (game merender internal di 1080p). |
| **Upscaling** | Off, FSR 1 | Upscaling AMD FidelityFX Super Resolution ke resolusi target. |
| **Sharpening (RCAS)** | Aktif / Nonaktif | Filter penajam gambar Contrast Adaptive Sharpening. |
| **Anisotropic Filtering**| Off, 2x, 4x, 8x, 16x | Kualitas filter tekstur untuk sudut pandang miring. |
| **Frame Limit** | 60 (Default), 90, 120, 144, 165, 240, Unlimited | Membuka cap FPS bawaan game menggunakan signature patch. |
| **VSync** | Aktif / Nonaktif | Sinkronisasi frame rate dengan refresh rate monitor. |
| **Audio Debug Log** | Aktif / Nonaktif | Mencatat detail alokasi suara Ngs2 ke `user\audio_debug.log`. |

---

## Menu In-Game & Hotkey

- **Insert** (Keyboard) atau **R3 + L2** (Gamepad): Membuka overlay menu dalam game (pengaturan FSR, Anisotropic filtering, status stik, dan About).
- **F12**: Mengambil screenshot (disimpan ke folder `user\`).
- **L1 / R1** (atau **Q / E**): Berpindah antar tab menu.

---

## Dukungan Controller

- **XInput & SDL3**: Stik Xbox One/Series, DualShock 4, DualSense, dan Nintendo Switch Pro langsung terbaca otomatis.
- **Stik Generik / DirectInput**: Klik **Map controller** di launcher untuk merekam tombol dan analog stik lewat program `basara4-pad-mapper.exe`. Hasil tersimpan di `user\gamepad_mapping.txt`.

---

## Arsitektur Teknis (Cara Kerja)

1. **Eksekusi Native CPU**: Berbeda dengan emulator (seperti RPCS3), port ini tidak membutuhkan penerjemah CPU karena arsitektur prosesor PS4 sama persis dengan PC (x86-64).
2. **Reverse Engineering Kontrak Sistem (`src/runtime_offline.c`)**: Engine MT Framework Capcom untuk Basara 4 membutuhkan sekitar 130 syscall OS PS4 yang sebelumnya belum ada di SpartanPort. Semuanya diimplementasikan ulang secara native oleh Razz (kernel event flags, PlayGo, AppContent, dialog box, dan offline network stubs).
3. **Sound Graph Ngs2 Kustom (`src/runtime_audio.c`)**: Seluruh audio game diproses melalui sistem Ngs2 buatan sendiri yang mendukung dekompresi ATRAC9 multi-channel, pengaturan loop point, pitch, dan streaming ring buffer.
4. **Hardening LibAtrac9**: Perbaikan keamanan memori pada decoder LibAtrac9 (`huffCodes.c`, `unpack.c`, `band_extension.c`) agar tidak crash jika membaca audio stream yang tidak sempurna.
5. **Memory Signature Patching**: Engine limiter di-bypass langsung pada memori sebelum proses boot berlangsung tanpa mengubah file asli game.

---

## Build dari Source Code

### Kebutuhan

1. **MSYS2** dengan environment **CLANG64**:
   ```bash
   pacman -S --needed base-devel mingw-w64-clang-x86_64-toolchain \
       mingw-w64-clang-x86_64-cmake mingw-w64-clang-x86_64-ninja \
       mingw-w64-clang-x86_64-vulkan-headers mingw-w64-clang-x86_64-vulkan-loader \
       mingw-w64-clang-x86_64-sdl3 mingw-w64-clang-x86_64-ffmpeg \
       mingw-w64-clang-x86_64-glslang mingw-w64-clang-x86_64-pkg-config
   ```
2. **Python 3.10+** (Windows) dengan `PyInstaller` dan `Pillow`:
   ```powershell
   python -m pip install pyinstaller pillow
   ```

### Langkah Kompilasi

1. Clone repositori:
   ```bash
   git clone --recursive https://github.com/razzconnect/basara4-native-pc.git
   cd basara4-pc
   ```

2. Jalankan build di terminal **MSYS2 CLANG64**:
   ```bash
   bash build.sh
   ```
   *Perintah ini menghasilkan `out/gpu/basara4-core.exe`.*

3. Rakit folder rilis menggunakan PowerShell:
   ```powershell
   python packaging/basara4/make_package.py --out dist/basara4-windows
   ```

---

## Troubleshooting & Log

Jika menemukan bug atau crash, sertakan berkas log dari folder `user\`:
- **`user\last_run.log`**: Log runtime utama (inisialisasi, compile shader Vulkan, crash dump).
- **`user\prepare.log`**: Log proses ekstraksi dan penautan modul.
- **`user\audio_debug.log`**: Detail pembacaan suara dan musik Ngs2.

Menganalisis log audio lewat terminal:
```bash
python tools/audio_log_summary.py user/audio_debug.log
```

---

## Kredit & Kontributor

| Komponen | Penulis / Kontributor | Kontribusi | Lisensi |
|---|---|---|---|
| **Basara 4 PC Port** | **Razz** | Pembuat port, reverse-engineering fungsi sistem Basara 4 (`runtime_offline.c`), implementasi Ngs2 audio, LibAtrac9 hardening, launcher GUI, patch unlock FPS, pad mapper, packaging | GPL-2.0+ |
| **SpartanPort** (`gow3_pc`) | **Felipe Prado** | Fondasi arsitektur runtime native PC dan loader probe | GPL-2.0+ |
| **bloodborne_pc** | **Supermedo** & **deadinside28** | Perintis teknik dan metodologi native port PS4 PC | GPL-2.0+ |
| **shadPS4** | **shadPS4 Team** | Penerjemah shader GPU ke Vulkan / SPIR-V, tabel NID (`aerolib`) | GPL-2.0+ |
| **LibAtrac9** | **Alexander Barney** | Library decoder format ATRAC9 | MIT |
| **GPU Core Fixes** | **cuesta4** | Perbaikan rendering video core (blend, depth/stencil) | GPL-2.0+ |
| **FSR-Vulkan** | **FireBurn** / AMD | Integrasi AMD FidelityFX Super Resolution untuk Vulkan | MIT |
| **Third-Party Libraries** | Berbagai Pengembang | SDL3, Dear ImGui, Vulkan Memory Allocator, sirit, xbyak, miniz | Lisensi masing-masing |

Daftar pemberitahuan hak cipta lengkap tersedia di file [NOTICE.md](NOTICE.md).

---

## Komunitas & Media Sosial

Bergabung dengan komunitas kami untuk diskusi, tanya jawab, pembaruan rilis, dan lainnya:

- **Komunitas Discord**: [https://discord.gg/UBZc57bBMa](https://discord.gg/UBZc57bBMa) (komunitas umum & gaming)
- **TikTok**: [@razzconnect](https://www.tiktok.com/@razzconnect) ([https://www.tiktok.com/@razzconnect](https://www.tiktok.com/@razzconnect)) — update progres porting & devlog

---

## Lisensi

Proyek ini dirilis di bawah lisensi **GNU General Public License v2.0 atau lebih baru** ([LICENSE](LICENSE)).

- Seluruh kode hasil modifikasi, reverse-engineering kontrak sistem, sound graph Ngs2, dan launcher karya Razz dilindungi Hak Cipta (C) 2026 Razz di bawah lisensi GPL-2.0-or-later.
- Komponen pihak ketiga mempertahankan lisensi hak cipta dari masing-masing pembuat aslinya seperti tertera pada `NOTICE.md` dan folder `third_party/`.
