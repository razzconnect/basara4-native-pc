# Panduan melaporkan bug

Panduan ini untuk pemain yang menemukan masalah (suara, gambar, hang, crash, kontrol) dan ingin
melaporkannya. Isinya: apa yang dicatat, log mana yang dikirim, dan cara menyalakan perekaman
tambahan bila diminta.

## 1. Catat saat masalah terjadi

Satu baris per kejadian sudah cukup:

```
Jam   Di mana (menu / stage / cutscene / dialog)   Apa yang terlihat atau terdengar   Berapa lama   Pulih sendiri?
21:15 stage ke-3, cutscene pembuka                 musik mati                          sampai akhir  tidak
```

- Tulis **gejalanya**, bukan tebakan penyebab.
- Jam dinding membantu mencocokkan gejala dengan log.
- Setelah selesai, tutup game dengan normal: menu keluar, tombol close di jendela, atau **Stop game** di
  launcher. Jangan lewat Task Manager. Game baru menulis akhir log-nya saat ditutup normal.
- Kalau game **tidak merespons** (hang): catat jam dan lokasinya dulu, baru tutup.

## 2. Catat pengaturan dan perangkat

- Versi launcher (tertulis di launcher, misalnya `v1.0.8`).
- GPU dan versi driver, Windows, CPU, dan RAM.
- Pengaturan launcher: mode layar (Windowed / Borderless / Exclusive), resolusi, upscaler (Off / FSR 1),
  RCAS, anisotropic filtering, batas FPS, VSync, dan GPU yang dipilih.
- Apakah masalahnya terjadi terus, sesekali, atau selalu di tempat yang sama.

## 3. Log yang dikirim

Semua ada di folder `user\` di samping launcher. Tombol **Open log** di launcher membuka `last_run.log`.

| Berkas | Kapan dikirim |
|---|---|
| `user\last_run.log` | selalu |
| `user\crash\core-*.dmp` | bila game crash (berisi sebagian memori game; kirim hanya ke pengembang) |
| `user\prepare.log` | bila masalahnya saat persiapan pertama (sebelum game mulai) |
| `user\audio_debug.log` | bila diminta, untuk masalah suara (lihat bagian 4) |
| folder sesi di `user\render_traces\` dan PNG di `user\captures\` | bila diminta, untuk masalah gambar (lihat bagian 4) |
| tangkapan layar (F12, tersimpan di `user\screenshots\`) atau rekaman layar | masalah gambar yang sulit dijelaskan |

Kalau game crash, launcher menampilkan dialog dengan tombol **📋 Copy Error ke Clipboard**. Tempel isinya
ke laporan.

## 4. Perekaman tambahan (bila diminta)

Semuanya dinyalakan di launcher sebelum menekan Play, dan hanya berlaku untuk sesi berikutnya.

- **Audio debug log**: centang **Audio debug log**. Setiap kejadian suara dicatat ke `user\audio_debug.log`;
  log sesi sebelumnya disimpan sebagai `audio_debug.previous.log`. Tombol **Open audio log** membukanya.
- **WAV capture**: centang **+ WAV capture (up to 3 GB)** bersama Audio debug log, hanya bila diminta.
  Mix akhir dan setiap suara panjang direkam tanpa kompresi ke `user\audio_wav\` (sekitar 70 MB per menit,
  maksimal 3 GB). Fitur ini mati sendiri setelah satu sesi. Hapus foldernya setelah dikirim.
- **Render debug trace**: centang **Render debug trace** untuk masalah gambar. Setiap sesi dicatat di
  foldernya sendiri, `user\render_traces\<tanggal-jam>\`, dan beberapa tangkapan diambil otomatis ke
  `user\captures\`. Selama trace menyala, tekan **F11** saat masalah terlihat di layar untuk menyimpan tangkapan
  frame itu. Tombol **Open render log** dan **Open captures** membuka hasilnya.

Matikan lagi perekaman setelah selesai. Log tambahan memakai ruang disk dan sedikit performa.

## 5. Templat laporan

```
Versi:                (versi launcher)
Perangkat:            (GPU + driver, Windows, CPU, RAM)
Pengaturan launcher:  (mode layar, resolusi, upscaler, RCAS, aniso, batas FPS, VSync, GPU)

Gejala (satu kalimat):
Jam:                  (± 5 detik)
Di mana:              (stage / cutscene / menu / dialog)
Terjadi:              sekali / berulang / selalu di tempat yang sama
Pulih sendiri:        ya / tidak
Terakhir normal:      (versi atau sesi sebelumnya, bila ingat)

Berkas terlampir:     (last_run.log, dump crash, log/folder lain yang diminta)
```

Jangan kirim file game, save milik orang lain, atau seluruh folder `user\audio_wav\`.
