# Metode memperbaiki: layar hitam, gambar merah dan dobel, crash

Dokumen ini menjelaskan **cara perbaikan di sisi tampilan (GPU) dan crash dikerjakan dari awal sampai akhir**: apa yang dilihat, alat apa yang dipakai, bagaimana penyebabnya
dipersempit, apa yang diubah, dan **bagaimana Anda memeriksa sendiri** bahwa perbaikannya bekerja. Untuk sisi suara, lihat [AUDIO-VERIFICATION.md](AUDIO-VERIFICATION.md);
metode umumnya sama.

Semua angka dan cuplikan log di sini nyata (Windows, NVIDIA RTX 4070, 24 sampai 25 Oktober 2026). Bagian yang **belum terbukti** ditulis jujur sebagai belum terbukti.

## Isi

1. [Ringkasan: apa yang diperbaiki dan seberapa kuat buktinya](#1-ringkasan-apa-yang-diperbaiki-dan-seberapa-kuat-buktinya)
2. [Prinsip kerja](#2-prinsip-kerja)
3. [Alat bukti](#3-alat-bukti)
4. [Kasus 1: layar hitam di cutscene event (perangkat GPU hilang)](#4-kasus-1-layar-hitam-di-cutscene-event-perangkat-gpu-hilang)
5. [Kasus 2: latar merah dan gambar dobel](#5-kasus-2-latar-merah-dan-gambar-dobel)
6. [Kasus 3: assertion GetBarriers](#6-kasus-3-assertion-getbarriers)
7. [Kasus 4: crash di pemilihan stage](#7-kasus-4-crash-di-pemilihan-stage)
8. [Kasus 5: crash dump dan laporan otomatis](#8-kasus-5-crash-dump-dan-laporan-otomatis)
9. [Prosedur uji grafis lengkap (satu sesi)](#9-prosedur-uji-grafis-lengkap-satu-sesi)
10. [Uji pembanding (kontrol)](#10-uji-pembanding-kontrol)
11. [Keterbatasan dan yang belum terbukti](#11-keterbatasan-dan-yang-belum-terbukti)
12. [Kesalahan yang pernah terjadi dan pelajarannya](#12-kesalahan-yang-pernah-terjadi-dan-pelajarannya)

---

## 1. Ringkasan: apa yang diperbaiki dan seberapa kuat buktinya

| Gejala | Penyebab yang terbukti | Perbaikan | Commit | Bukti |
|---|---|---|---|---|
| Layar hitam di cutscene event, game tampak hang | driver NVIDIA kehilangan perangkat GPU (event Windows `nvlddmkm` 153) saat sebuah quad membaca depth image sebagai tekstur warna | pengikatan tekstur yang gambarnya tidak cocok dicari ulang dengan format yang tepat; view image dibatasi dan diberi aspek yang benar | `6ef526b`, `0868a14`, `1fb11ac` | **kuat**: 5 event 153 sampai 21:41 tanggal 24 Okt, nol sesudahnya, di tiga sesi penuh |
| Game berhenti total tanpa pesan apa pun | `Semaphore::Wait` mengulang selamanya setelah perangkat hilang | menunggu berhenti pada kesalahan nyata dan melaporkan draw penyebabnya | `b260192`, `a74d38f` | **kuat** (alat diagnosis; kasus terjadi dan teridentifikasi) |
| Latar merah di cutscene | tekstur warna RGBA16F berakhir di depth image: kedalaman tampil di kanal merah | sama dengan di atas | `1fb11ac` | **sedang**: pengaman aktif di tiga sesi; tampilan diamati pengguna sebagai lancar; tidak ada pembanding tangkapan layar otomatis |
| Gambar dobel separuh ukuran di pojok kiri atas | view dibatasi ke level 0 sehingga pass downsample menggambar salinan kecil ke pojok | sama dengan di atas | `1fb11ac` | **sedang** (sama) |
| Assertion `GetBarriers` | tabel subresource berukuran lain atau rentang melewati image | tabel dibangun ulang sesuai image, rentang dibatasi | `2acbd18` | **sedang**: tidak pernah muncul lagi, tetapi pengaman ini sendiri tidak terpicu di sesi akhir |
| Crash di pemilihan stage (issue #1) | pemecah markup teks UI MT Framework menarik node dari daftar bebas yang rusak | pengecualian di dua alamat tertentu diselamatkan dengan melompat ke epilog fungsi | `313eef4` | **lemah**: ini penjinak gejala, bukan perbaikan akar; akar kerusakan daftar bebas belum diketahui |
| Laporan crash hanya beberapa baris teks | tidak ada dump | minidump otomatis ke `user\crash\` | `c7778ca` | diuji dengan `SPARTAN_TEST_MINIDUMP=1` |

**Temuan baru dari log sesi 25 Oktober (belum ada di commit mana pun):** 16 dari 16 kejadian yang dicatat memperlihatkan bahwa `FindImage` **mengembalikan image yang benar**
(RGBA16F 960x540), lalu kode "tautan stencil/depth" (`GetAssociatedDepth`) **mengalihkannya ke depth image** (D32SfloatS8Uint 1920x1080). Jadi akar masalahnya ada pada pengalihan itu,
bukan pada pencarian image. Lihat [5.3](#53-temuan-akar-masalah-dari-log-terakhir).

---

## 2. Prinsip kerja

1. **Cari bukti dulu, ubah kode kemudian.** Setiap perubahan besar didahului alat yang membuat masalah terlihat (checkpoint draw, log image, tangkapan frame).
2. **Hubungkan dengan catatan sistem operasi.** Gejala dilaporkan dengan jam dinding; layar hitam dicocokkan dengan event driver di Windows (bukan hanya log game).
3. **Satu sesi, semua bukti.** Sesi bermain 15 sampai 20 menit mahal; semua perekam dinyalakan sebelum mulai.
4. **Pengaman tidak membuang bukti.** Setiap pengaman menulis log yang menyebut apa yang ditemukan dan apa yang dilakukan, dan jumlah barisnya dibatasi (16) supaya log tidak banjir.
5. **Jujur soal yang tidak terbukti.** Pesan commit menulis "not tried in game" sampai ada sesi yang membuktikannya.
6. **Jangan menyimpulkan dari satu sesi atau satu gejala.** Beberapa teori awal ternyata hanya sebagian benar ([12](#12-kesalahan-yang-pernah-terjadi-dan-pelajarannya)).

---

## 3. Alat bukti

### 3.1 Log game: `user\last_run.log`

Berisi inisialisasi, peringatan GPU, dan laporan crash. Baris GPU berbentuk `GPU [Render.Vulkan] <Warning> berkas.cpp:baris fungsi: [JJ:MM:DD] ...`.
Peringatan berstempel **jam dinding** `[JJ:MM:DD]` supaya cocok dengan jam pelaporan pengguna dan dengan `timeline.log`.

Peringatan yang dipakai di dokumen ini (jumlah yang dicatat dibatasi: 8 untuk view, 16 untuk tekstur):

| Teks di log | Artinya | Dipicu oleh |
|---|---|---|
| `image view range (levels A+B, layers C+D) exceeds its image ... clamped to ...` | deskripsi tekstur game (T#) meminta lebih banyak level/layer daripada yang dimiliki image; rentang dipotong | `6ef526b` |
| `view of depth/stencil image ... asked for format F aspect X: using the image's format, one aspect` | view pada image depth/stencil diminta dalam format warna atau dengan dua aspek | `0868a14` |
| `Barrier range (levels ..., layers ...) exceeds the image ...; limited to the image` | rentang barrier melebihi image; dipotong | `2acbd18` |
| `texture F WxH levels N at 0x... (stage 0x...) resolved to the depth image ...; FindImage returned ...; <redirected through the stencil/depth link / no redirect>` | tekstur **warna** berakhir di depth image; menyebut jalurnya | `0633cf5` |
| `texture ...: image ... cannot serve it; looked up again, now ...` | pengaman pengikatan: image tidak cocok dengan permintaan, dicari ulang | `1fb11ac` |
| `GPU: DEVICE LOST in <tempat>` + daftar draw dan image | perangkat GPU hilang; draw terakhir yang dicapai GPU | `b260192`, `a74d38f` |

### 3.2 Catatan sistem Windows (bukti dari luar game)

Layar hitam karena driver NVIDIA mereset GPU tercatat sebagai event **153** dari `nvlddmkm` di log System. Perintah (hanya membaca):

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; Id=153} -MaxEvents 20 |
    Select-Object TimeCreated, Id, ProviderName
```

Hasil nyata di mesin ini: lima event, **17:59:51, 19:17:57, 19:57:04, 20:33:33, dan 21:41:48 (24 Okt)**. Empat yang pertama cocok dengan empat layar hitam yang dilaporkan
(17:59, 19:17, 19:57, 20:33); yang kelima adalah yang pertama kali ditangkap oleh checkpoint ([4.3](#43-checkpoint-draw-menunjuk-draw-penyebab)). Tidak ada event 153 sesudahnya.

Untuk menghitung dalam rentang waktu tertentu (misalnya sesi uji):

```powershell
(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; Id=153; StartTime=[datetime]'2026-10-25 01:00'} -ErrorAction SilentlyContinue | Measure-Object).Count
```
Hasil 0 berarti tidak ada reset GPU sejak jam itu. Event ini hanya ada di kartu NVIDIA; di AMD/Intel lihat catatan driver masing-masing.

### 3.3 Alat dari luar proses (game hang)

| Alat | Fungsi | Perintah |
|---|---|---|
| `tools/hang_sample.py` | membaca RIP dan stack thread tersibuk dari luar tanpa mengubah game; karena kode PS4 berjalan native, RIP = alamat tamu | `python tools/hang_sample.py [pid] [--seconds 3]` |
| `tools/hang_all.py` | menamai **setiap thread** game yang sedang berjalan dengan nama fungsi host (`llvm-symbolizer`) | `python tools/hang_all.py [pid]` (butuh build dengan simbol: `out\gpu\basara4-core.exe`, bukan yang di-strip) |

Hasil kunci yang ditemukan dengan alat ini saat layar hitam: thread `DrawRec` berputar 100% CPU di dalam driver, dengan tumpukan
`DrawRec → Presenter::PrepareFrame → Scheduler::Wait → Semaphore::Wait`, sementara thread perintah GPU menunggunya di `DrainDrawPipe` dan thread lain terparkir.
Layar hitam tanpa satu baris pun di log.

### 3.4 Perekam sesi (alat pengembang)

`tools/debug_watch.py` (dijalankan `Debug run.bat`) menulis:

| Keluaran | Isi |
|---|---|
| `user\timeline.log` | baris penting game, GPU, dan audio dengan jam dinding |
| `user\captures\` | **frame analyzer** milik game: semua render pass, target, shader, dan tekstur satu frame; otomatis 1 per detik saat cutscene event, dan 5 saat **F9** |
| `user\framedump\*.raw` → PNG | frame mentah yang ditampilkan dan buffer display game; otomatis tiap 2 detik saat cutscene event (maks 8), 3 saat F9 |
| `user\recordings\` | rekaman **seluruh layar utama** (NVENC, per menit, 30 terakhir). Set `DEBUG_NO_VIDEO=1` untuk mematikan |
| `user\report.txt` | dibuat saat game ditutup: temuan audio, peringatan GPU, daftar cutscene dan tangkapannya, crash dump, daftar berkas |

Cutscene event dikenali dari nama berkasnya di log (`evp002`, `evp010`, `evp034_00`, `evm012_00` sampai `evm012_02`, ...).
`tools/framedump_png.py` mengubah `.raw` menjadi PNG (dua urutan kanal, `_bgr` dan `_rgb`, bila tidak yakin).

> **Privasi.** Rekaman menangkap semua yang tampak di layar utama, dan tidak ikut paket pemain. Tutup jendela pribadi atau set `DEBUG_NO_VIDEO=1`.

### 3.5 Crash dump

`user\crash\core-YYYYMMDD-HHMMSS.dmp` ditulis bila proses mati karena fault atau assertion GPU (`SPARTAN_TEST_MINIDUMP=1` menulis satu saat start untuk memeriksa jalurnya). Isinya
thread, stack, modul, dan memori yang ditunjuk stack. Berikan hanya kepada orang yang Anda percaya.

---

## 4. Kasus 1: layar hitam di cutscene event (perangkat GPU hilang)

### 4.1 Gejala dan cara mempersempit

1. **Gejala:** empat layar hitam dalam satu sore (17:59, 19:17, 19:57, 20:33), selalu di sekitar cutscene event; game seperti membeku, tanpa pesan.
2. **Dugaan pertama (audio) ternyata hanya gejala:** pada log audio, voice musik rack 23 macet pada saat yang sama. Itu akibat, bukan sebab: game berhenti maju sehingga tidak lagi menyuplai data.
   Dugaan lain (event 1 yang menjadikan voice tetap "playing") diperbaiki ([AUDIO 4.8](AUDIO-VERIFICATION.md#48-event-1-menghentikan-voice-697bb08)) tetapi **bukan satu-satunya sebab**.
3. **Alat dari luar:** `hang_sample.py` lalu `hang_all.py` menunjukkan `DrawRec` berputar di `Semaphore::Wait` di dalam driver NVIDIA ([3.3](#33-alat-dari-luar-proses-game-hang)).
4. **Pembanding dari luar game:** event 153 `nvlddmkm` di log Windows pada **tepat** empat jam itu ([3.2](#32-catatan-sistem-windows-bukti-dari-luar-game)). Ini mengubah masalah dari "game hang" menjadi
   "driver mereset GPU karena game mengirim perintah yang tidak valid".
5. **Ciri awal di log game:** sebelum layar hitam muncul satu kali peringatan `GetBarriers` bahwa view meminta level 0+3 dari image yang hanya memiliki 1 level.

### 4.2 Perbaikan pertama: view tidak boleh melewati image (`6ef526b`)

`image_view.cpp` menyerahkan rentang level/layer dari T# apa adanya ke `createImageView`. Itu view yang tidak valid (VUID-VkImageViewCreateInfo-subresourceRange-01718), dan game lalu berputar menunggu label GPU.
Sekarang rentang dipotong sesuai image dan satu baris log mencatat image-nya (alamat, ukuran, format, level).

### 4.3 Checkpoint draw menunjuk draw penyebab (`b260192`, `a74d38f`)

Karena penyebab belum jelas, game diberi kemampuan menunjuk draw yang menjatuhkan GPU:

- Memakai ekstensi `VK_NV_device_diagnostic_checkpoints` (khusus NVIDIA, otomatis dilewati di GPU lain).
- Setiap draw, draw tak langsung, dan dispatch menaruh penanda; id penanda menunjuk satu cincin deskripsi (jenis, hash shader, jumlah).
- Saat menunggu atau submit gagal, penanda yang dicapai GPU dibaca dan dicetak (`GPU: DEVICE LOST in <tempat>`), disusul daftar image yang diikat draw-draw terakhir (alamat, format, ukuran, level, bendera depth/target).
- `Semaphore::Wait` berhenti pada kesalahan nyata (UNREACHABLE) alih-alih berputar selamanya.

**Hasil pertama (event 21:41:48):** GPU menyelesaikan draw dengan shader `vs 0xf842a908 / ps 0x17e60af9` dan sedang berada di draw berikutnya, `vs 0xd5df186b / ps 0x3c869134`,
sebuah quad bertekstur 4-indeks yang satu-satunya teksturnya adalah gambar float 2D. Tepat sebelum itu log menunjukkan T# meminta **3 level** dari image
**D32SfloatS8Uint 1920x1080 di 0x105d358000**: *depth image dipakai sebagai tekstur warna*.

### 4.4 Perbaikan kedua: view depth/stencil (`0868a14`)

View pada image depth/stencil dibuat dengan format T# (warna) dan dua aspek (depth dan stencil), yang dilarang Vulkan untuk image depth/stencil. Sekarang view memakai format
image itu sendiri dan **satu aspek**. Peringatannya: `view of depth/stencil image ... using the image's format, one aspect`.

### 4.5 Pengaman pengikatan (`1fb11ac`): perbaikan yang benar-benar mengatasi masalah

Di `BindTextures` (`vk_rasterizer.cpp`): setelah gambar tekstur ditentukan, `image_serves_request` memeriksa apakah image itu **bisa melayani** permintaan T#:

- jumlah level dan layer image tidak kurang dari yang diminta view, **dan**
- bukan image depth bila formatnya format warna.

Jika tidak, `FindImage(desc, true)` dipanggil lagi dengan format yang persis (mekanisme itu memaksa cache membuat atau memperluas image yang sesuai, menyalin isi dari yang lama bila format memungkinkan), dan
satu baris `cannot serve it; looked up again, now ...` dicatat. Hasil pencarian ulang **tidak** disimpan di memo.

### 4.6 Cara memeriksa sekarang

Lihat prosedur lengkap di [bagian 9](#9-prosedur-uji-grafis-lengkap-satu-sesi). Intinya:

```powershell
# 1. tidak ada reset GPU sejak jam mulai sesi
(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; Id=153; StartTime=[datetime]'2026-10-25 01:00'} -ErrorAction SilentlyContinue | Measure-Object).Count
# 2. tidak ada laporan device lost di log game
Select-String user\last_run.log -Pattern 'DEVICE LOST' -CaseSensitive
```
**Lulus:** keduanya 0. **Hasil acuan:** event 153 terakhir 24 Okt 21:41:48; sesi 25 Okt (01:03 sampai 01:42, 17 menit penuh) dan dua sesi arsip lainnya: 0 `DEVICE LOST`.

---

## 5. Kasus 2: latar merah dan gambar dobel

### 5.1 Gejala

- **Merah:** latar cutscene atau pemilihan stage tampil merah.
- **Dobel:** salinan setengah ukuran dari gambar muncul di pojok kiri atas layar (terlihat di tangkapan layar).

### 5.2 Penyebabnya sama dengan layar hitam: satu tekstur, tiga akibat

Rantai bloom/DOF cutscene: T# meminta **RGBA16F dengan 3 level mip**, tetapi cache mengembalikan **image depth 1 level** pada alamat itu. Tergantung bagaimana view dibuat, ada tiga akibat
(dari pesan commit `1fb11ac`, setiap akibat diamati langsung):

| View dibuat dengan | Akibat |
|---|---|
| tanpa pembatasan (rentang 3 level pada image 1 level) | view tidak valid → GPU hilang → **layar hitam** |
| dibatasi ke level 0 | pass downsample menggambar **salinan setengah ukuran** ke pojok kiri atas → **gambar dobel** |
| dipaksa memakai aspek depth | kedalaman tampil di kanal merah → **latar merah** |

Karena itu satu pengaman pengikatan ([4.5](#45-pengaman-pengikatan-1fb11ac-perbaikan-yang-benar-benar-mengatasi-masalah)) menyelesaikan ketiganya sekaligus.

### 5.3 Temuan akar masalah dari log terakhir

Log diagnosis yang ditambahkan lebih dulu (`0633cf5`, `2bb8f4c`, `03a0db2`) mencatat bagaimana tekstur warna berakhir di depth image. Isi log sesi 25 Okt 01:30:26:

```
texture R16G16B16A16Sfloat 960x540 levels 3 at 0x105e080000 (stage 0x17e60af9) resolved to the depth image 0x105d378000 D32SfloatS8Uint 1920x1080 levels 1;
FindImage returned 0x105e080000 R16G16B16A16Sfloat 960x540; redirected through the stencil/depth link
```

Dari 16 baris yang dicatat, **16 punya akhiran yang sama (`redirected through the stencil/depth link`)** dan `FindImage` selalu mengembalikan **image RGBA16F yang benar**. Jadi:

- pencarian image (`FindImage`) **tidak salah**;
- yang salah adalah blok di `BindTextures` yang memanggil `texture_cache.GetAssociatedDepth(*image)`: *"jika image ini punya depth terkait, ini adalah lampiran stencil, alihkan ke buffer depth-stencil yang sebenarnya"*.
  Image RGBA16F di 0x105e080000 ternyata masih punya **tautan depth** ke D32SfloatS8Uint di 0x105d378000, padahal permintaannya adalah tekstur warna. Kemungkinan alamat itu dulunya
  tempat bidang stencil sebuah buffer depth lalu dipakai ulang untuk target warna, dan tautan lamanya tidak dilepas. **Itu dugaan; belum diverifikasi di kode cache.**

**Implikasi:**

- Pengaman `1fb11ac` mengoreksi hasil pengalihan itu **setelah terjadi**, dengan mencari ulang. Itu bekerja (tiga sesi penuh tanpa gejala), tetapi sebenarnya menambal gejalanya.
- Perbaikan yang lebih bersih (**belum dibuat**): hanya mengalihkan lewat tautan depth bila format yang diminta memang format depth/stencil (kondisi yang sama dengan yang dipakai `note_depth_substitution`),
  atau melepas tautan itu saat alamatnya dipakai ulang sebagai target warna. Ini perubahan di jalur panas render dan perlu diuji dengan satu sesi lagi.
- Batas catat 16 berarti jumlah kejadian sebenarnya **tidak diketahui**; di sesi 25 Okt, 16 baris itu semuanya terjadi dalam satu detik (01:30:26).

### 5.4 Cara memeriksa

```powershell
Select-String user\last_run.log -Pattern 'resolved to the depth image'   # kejadian pengalihan (dicatat maks. 16)
Select-String user\last_run.log -Pattern 'cannot serve it'               # pengaman bekerja
Select-String user\last_run.log -Pattern 'still does not fit'            # HARUS KOSONG: pencarian ulang pun gagal
```

- **Lulus:** `still does not fit` kosong, tidak ada `DEVICE LOST`, dan tampilan cutscene (tangkapan F9 atau PNG di `user\framedump`) tidak merah dan tidak ada kotak di pojok kiri atas.
- Baris `cannot serve it` **boleh ada**: itu bukti pengaman bekerja. Jumlahnya tak bermakna karena batas 16.
- Pengecekan visual masih **manual**: buka PNG cutscene (`evm012_02` dan sebagainya) dan lihat warna latar serta pojok kiri atas. Tidak ada perbandingan otomatis.

---

## 6. Kasus 3: assertion GetBarriers

- **Gejala.** Assertion di `GetBarriers` (`image.cpp`); proses mati.
- **Penyebab.** Tabel per-subresource sisa dengan ukuran lain, atau rentang barrier melewati image, membuat indeks `mip*layers+layer` melewati ujung tabel.
- **Perbaikan (`2acbd18`).** Tabel dibangun ulang sesuai image dan rentang dibatasi pada image, dengan satu peringatan (`Barrier range ... limited to the image`). `ASSERT` tetap ada.
- **Pemeriksaan.** `Select-String user\last_run.log -Pattern 'Barrier range'` harus kosong atau satu baris saja, dan tidak ada `Assertion`.
  Di tiga sesi arsip: 0 baris (pengaman ini tidak terpicu setelah pengikatan tekstur diperbaiki).
- **Catatan.** Commit yang sama mencabut pengaman audio `9fd4c98` yang keliru ([12](#12-kesalahan-yang-pernah-terjadi-dan-pelajarannya)).

---

## 7. Kasus 4: crash di pemilihan stage

- **Gejala.** Crash atau freeze pas buka pemilihan stage atau teks pengarahan (issue #1).
- **Crash lama.** Log sebelumnya nunjukin pemecah markup teks UI MT Framework baca daftar bebas yang rusak di offset `0xc33d55`. *Kenapa* daftar itu bisa rusak masih belum ketahuan.
- **Perbaikan (`313eef4`, `src/probe.c`).** Di penanganan pengecualian: kalau RIP ada di offset `0xc33d55` atau `0xc33cf0` dari image game, RIP dipindahin ke epilog fungsi di `0xc33d9f`
  (`pop rbx; pop r14; pop rbp; ret`) terus eksekusi dilanjutin. Fungsi pemecah teks jadi balik lebih awal.
- **Sifat perbaikan.** Ini cuma nutupin gejala doang, scope-nya sempit: cuma dua alamat, cuma dump yang sama. Teks yang lagi dipecah bisa kepotong; status internal daftar bebas tetap rusak.
- **Temuan crash terbaru.** Log stack-protector berhenti di caller `0xc32f66`. Di dump-nya, salinan runtime satu baris teks Inggris kepotong di tengah tag warna, padahal entry sumbernya lengkap di arsip. Pas dicek statis, ketemu loop tag di `0xc32cc0` yang nyalin byte sampai `>` tanpa ngecek NUL atau batas buffer lokal 256 byte. Ini nguatin dugaan overflow pas input runtime-nya kepotong, tapi belum jelasin kenapa salinan runtime itu bisa kepotong.
- **Guard parser tambahan (`src/probe.c`).** Buat signature fungsi yang cocok, trampoline ngecek batas input, NUL, dan batas buffer 256 byte dulu sebelum nyalin. Kalau input/tag-nya nggak valid, parser langsung diakhirin lewat epilognya; teksnya mungkin jadi nggak lengkap. Signature yang beda nggak kena patch.
- **Batasan.** Guard ini nyegah loop tag nulis kelewat buffer, tapi nggak ngebenerin korupsi yang mungkin udah kejadian lebih awal, nggak jelasin kerusakan data runtime, dan bukan jaminan semua crash stage udah kelar.
- **Cara ngecek.**
  1. Buka pemilihan stage dan teks pengarahan beberapa kali.
  2. Pastiin `user\last_run.log` ada baris `Text parser guard: ready`; kalau isinya `skipped`, kirim baris itu plus log-nya.
  3. `Select-String user\last_run.log -Pattern 'Exception|Crash dump|Fault' -CaseSensitive` hasilnya kosong dan nggak ada file baru di `user\crash\`.
  4. Hal yang **nggak** bisa dicek lewat log: apakah teks pengarahannya muncul lengkap atau nggak. Cek manual pake mata sendiri.
  5. Buat ngenalin dump yang beda: kalau RIP crash-nya ada di offset lain, guard ini mungkin nggak berlaku; berarti itu masalah baru.

---

## 8. Kasus 5: crash dump dan laporan otomatis

`c7778ca` menambah dua hal supaya bug berikutnya tidak lagi berupa "layar hitam tanpa informasi":

- `spartanport_write_minidump` (`src/probe.c`) dipanggil dari penanganan fault dan dari `assert_fail_impl` (assertion GPU): dump di `user\crash\core-*.dmp`.
  Uji jalurnya tanpa crash: set `SPARTAN_TEST_MINIDUMP=1` lalu jalankan; satu dump ditulis saat start (hapus sesudahnya).
- `debug_watch.py` menulis `user\report.txt` saat game keluar: temuan audio, peringatan GPU, daftar cutscene dan tangkapannya, dump, dan daftar berkas.

Cara memeriksa: setelah satu sesi, `report.txt` ada dan `Report written` bertanggal sesi itu; bagian "cutscenes and debug captures" mencantumkan cutscene yang diputar.

---

## 9. Prosedur uji grafis lengkap (satu sesi)

### 9.1 Persiapan

1. Jalankan lewat `Debug run.bat` (alat pengembang; rekaman layar menangkap seluruh layar utama, tutup jendela pribadi atau `set DEBUG_NO_VIDEO=1`). Tanpa itu, cukup launcher biasa dan analisis `last_run.log` serta event Windows.
2. Catat jam mulai dan pastikan tidak ada game lain yang memakai GPU.
3. Sebelum mulai, catat jumlah event 153 saat ini ([3.2](#32-catatan-sistem-windows-bukti-dari-luar-game)).

### 9.2 Skenario

| Urutan | Tujuan |
|---|---|
| Menu → pemilihan stage → teks pengarahan, beberapa kali | crash UI ([7](#7-kasus-4-crash-di-pemilihan-stage)) |
| Masuk stage (misalnya `m012`), bertempur beberapa menit | render battle normal |
| **Cutscene event**, minimal 3 yang berbeda sampai selesai (`evp002`, `evp010`, `evm012_0x`, dan sejenisnya) | jalur tekstur bloom/DOF; layar hitam; merah; dobel |
| Tekan **F9** pada saat apa pun yang tampak aneh | tangkapan analyzer dan frame mentah |
| Tutup game dengan normal | `report.txt` dibuat |

### 9.3 Analisis

```powershell
$U = "C:\Games\basara4-windows\user"       # folder user\ game Anda
# a. reset GPU Windows sejak jam mulai sesi
(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; Id=153; StartTime=[datetime]'2026-10-25 01:00'} -ErrorAction SilentlyContinue | Measure-Object).Count
# b. kegagalan di log game (semua harus kosong)
Select-String "$U\last_run.log" -Pattern 'DEVICE LOST|Assertion|Crash dump|still does not fit|Unreachable' -CaseSensitive
# c. pengaman yang terpicu (boleh ada; catat)
Select-String "$U\last_run.log" -Pattern 'image view range|view of depth/stencil image|Barrier range|resolved to the depth image|cannot serve it' |
    ForEach-Object { ($_.Line -replace '^.*?(image view range|view of depth/stencil image|Barrier range|resolved to the depth image|cannot serve it).*$', '$1') } | Group-Object | Select-Object Name, Count
# d. dump crash
Get-ChildItem "$U\crash" -ErrorAction SilentlyContinue
```

Lalu buka PNG tangkapan cutscene (`$U\framedump`) dan laporan `$U\report.txt` bagian "cutscenes and debug captures".

### 9.4 Kriteria lulus

| # | Pemeriksaan | Lulus bila |
|---|---|---|
| 1 | event `nvlddmkm` 153 selama sesi | 0 |
| 2 | `DEVICE LOST`, `Assertion`, `Crash dump`, `Unreachable` | 0 |
| 3 | `still does not fit` | 0 |
| 4 | `user\crash\` | kosong |
| 5 | peringatan pengaman (`cannot serve it`, `resolved to the depth image`, ...) | boleh ada; catat jenis dan jumlahnya |
| 6 | PNG cutscene | tidak merah, tidak ada kotak di pojok kiri atas |
| 7 | pemilihan stage dan teks pengarahan | tidak crash, teks lengkap (manual) |
| 8 | `report.txt` | ada, tanggal sesi ini |

Hasil acuan (sesi 25 Okt, 01:03 sampai 01:42): 1 = 0, 2 = 0, 3 = 0, 4 = kosong, 5 = 16 baris `resolved ... depth image` dan 16 `cannot serve it`
(semua `01:30:26`), 0 untuk `image view range`, `view of depth/stencil image`, `Barrier range`.

### 9.5 Mencatat hasil

```
Tanggal/jam:         Build (commit):        Mesin (GPU/driver/OS):
Cutscene yang diputar:
#1 ... #8   lulus / gagal / tidak diuji
Jenis dan jumlah peringatan pengaman:
Gejala yang terlihat (jam dinding):
```

---

## 10. Uji pembanding (kontrol)

Untuk membuktikan perbaikan memang berperan, jalankan versi **sebelum** perbaikan pada skenario yang sama dan pastikan gejalanya muncul lagi.
**Tidak ada saklar lingkungan untuk mematikan pengaman grafis**, dan tidak ada binary pra-perbaikan yang disimpan, jadi kontrol berarti membangun versi lama (tidak diuji; langkahnya):

```powershell
cd <folder repositori>
git worktree add ..\b4-prefix 03a0db2          # commit tepat sebelum 1fb11ac (pengaman pengikatan)
# bangun di MSYS2 CLANG64 dari folder b4-prefix: bash build.sh
# pakai out/gpu/basara4-core.exe hasilnya untuk satu sesi, lalu bandingkan dengan 9.4
git worktree remove ..\b4-prefix
```

Yang diharapkan pada versi lama: event 153 muncul lagi di cutscene bloom/DOF, atau layar merah/dobel, atau `DEVICE LOST` dengan `ps 0x3c869134` di laporan.
Peringatan: ini membangun 140+ MB dan perlu waktu; bila tidak perlu, cukup andalkan bukti event Windows sebelum dan sesudah (5 event hingga 21:41, 0 sesudahnya).

---

## 11. Keterbatasan dan yang belum terbukti

- **Hanya NVIDIA RTX 4070.** `VK_NV_device_diagnostic_checkpoints` dan event 153 khusus NVIDIA. Di AMD/Intel perbaikan mengikuti aturan Vulkan dan seharusnya tidak merugikan, tetapi belum dicoba.
- **Akar masalah tautan stencil/depth belum diperbaiki di sumbernya** ([5.3](#53-temuan-akar-masalah-dari-log-terakhir)); pengaman mengoreksi setelah terjadi.
- **Jumlah kejadian pengaman tidak diketahui** karena batas 16 baris.
- **Tidak ada perbandingan tangkapan layar otomatis** dengan gambar acuan. Pemeriksaan "tidak merah, tidak dobel" adalah visual.
- **Perbaikan crash pemilihan stage hanya menjinakkan gejala** di dua alamat.
- **Sebab layar hitam tunggal tidak dibuktikan dengan kontrol:** tidak ada binary pra-perbaikan; buktinya adalah pola event Windows sebelum dan sesudah serta draw yang tertangkap.
- Pengaman view (`image view range`, `view of depth/stencil image`) dan barrier **tidak terpicu** di tiga sesi arsip; keandalannya sebagai jaring pengaman belum teramati.

---

## 12. Kesalahan yang pernah terjadi dan pelajarannya

| Kejadian | Pelajaran |
|---|---|
| Dua pengaman audio (`9fd4c98`) disusun dari baris log yang tidak bisa membedakan tiga rack sampler (semuanya `0x1000`); keduanya membuang dialog. Dicabut di `2acbd18`. | Log harus memuat nomor seri rack; jangan membuat aturan dari identitas yang ambigu. |
| Teori "event 1 = pause menyebabkan layar hitam" muncul dulu. | Kebetulan waktu bukan sebab. Layar hitam sebenarnya juga reset GPU; hanya event Windows yang membuktikannya. Event 1 tetap diperbaiki, tetapi sebagai masalah tersendiri. |
| Beberapa perbaikan ditulis "not tried in game" di pesan commit. | Tidak ada perbaikan yang dianggap bekerja sebelum satu sesi nyata membuktikannya. |
| Perbaikan musik pertama hanya menutup satu dari tiga urutan kejadian; dua sesi berturut-turut menemukan pola baru. | Satu sesi bersih bukan bukti umum; uji beberapa trek dan beberapa urutan. |
| Diagnosis awal tentang "separuh superframe (384 byte)" ternyata tidak tepat (superframe BGM stereo 384 byte; yang benar: *sisa* superframe lama). | Periksa ulang angka yang disebut dalam penjelasan sebelum ditulis ke dokumen. |
| Pengaman yang baik harus dicatat: bila tidak ada log, tidak ada yang tahu ia bekerja (dan bisa jadi menyembunyikan masalah). | Setiap pengaman mencetak apa yang ia temukan dan lakukan, dengan batas jumlah baris. |
