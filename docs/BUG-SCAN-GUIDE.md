# Panduan melaporkan bug

Panduan ini untuk pemain yang menemukan masalah (suara, gambar, hang, crash, kontrol) dan ingin
melaporkannya. Isinya: apa yang dicatat, cara membuat zip laporan (satu-satunya yang dikirim), dan cara
menyalakan Tracer bila masalahnya gambar atau suara.

## 1. Catat saat masalah terjadi

Satu baris per kejadian sudah cukup:

```
Jam   Di mana (menu / stage / cutscene / dialog)   Apa yang terlihat atau terdengar   Berapa lama   Pulih sendiri?
21:15 stage ke-3, cutscene pembuka                 musik mati                          sampai akhir  tidak
```

- Tulis **gejalanya**, bukan tebakan penyebab.
- Jam dinding membantu mencocokkan gejala dengan catatan di zip.
- Setelah selesai, tutup game dengan normal: menu keluar, tombol close di jendela, atau **Stop game** di
  launcher. Jangan lewat Task Manager. Game baru menulis akhir catatannya saat ditutup normal.
- Kalau game **tidak merespons** (hang): catat jam dan lokasinya dulu, baru tutup.

## 2. Catat pengaturan dan perangkat

Zip laporan sudah memuat spek PC dan pengaturan launcher. Tulis di postingan hanya yang tidak tertangkap:

- Versi launcher (tertulis di launcher, misalnya `v1.0.9`).
- Apakah masalahnya terjadi terus, sesekali, atau selalu di tempat yang sama.
- Perangkat audio yang dipakai (speaker, headset, HDMI) untuk masalah suara.

## 3. Yang dikirim: HANYA zip

Ada dua cara membuat zip. Keduanya menyimpannya di `user\reports\` dan menyorotnya di Explorer, lalu
tombol **Open Discord** membuka kanal laporan bug; seret zip itu ke postinganmu. Tidak ada yang diunggah
otomatis.

- **Game crash:** di jendela yang muncul tekan **Save report**.
- **Game tidak crash tetapi bermasalah (gambar, suara, hang, kontrol):** tutup game, lalu tekan **Report** di
  launcher. Jendela kecil menanyakan "What did you see, and what did you press?"; beberapa kata (maksimal 1000
  huruf, boleh dikosongkan) sangat membantu. Untuk masalah gambar atau suara nyalakan **Tracer** lebih dulu
  (bagian 4).

Isi zip: spek PC, pengaturan, catatan (log) sesi itu dengan nama akun dan nama folder disamarkan, ringkasan
render trace; dengan Tracer juga seluruh render trace, log audio, dan gambar F11. Zip tidak pernah berisi dump
crash (`.dmp`) atau rekaman WAV, hanya berkas sesi itu, dan tidak lebih dari 9 MB (bagian yang tidak muat
dicatat di `report.txt` dalam zip).

**Penyamaran ada batasnya:** nama akun atau nama PC yang hanya 1-2 huruf di teks bebas, nama yang menempel
pada huruf atau angka, nama profil Windows yang berbeda dari nama akun, path yang berspasi, dan metadata
gambar PNG tidak diperiksa. Kalau ragu, buka zipnya dan cari namamu sebelum mengirim.

**Berkas mentah di `user\` TIDAK disamarkan. Jangan dikirim**, termasuk lewat postingan atau DM; semua isi yang
dibutuhkan sudah ada di zip:

| Berkas | Isi | Dikirim? |
|---|---|---|
| `user\last_run.log` | catatan game (tombol **Open log** membukanya, untuk kamu baca sendiri) | tidak, sudah ada di zip (disamarkan) |
| `user\prepare.log` | catatan persiapan image pertama | tidak, sudah ada di zip (disamarkan) |
| `user\audio_debug.log` | catatan suara Tracer | tidak, sudah ada di zip dengan Tracer |
| `user\render_traces\` dan `user\captures\` | render trace dan gambar F11 Tracer | tidak, sudah ada di zip dengan Tracer |
| `user\crash\*.dmp` | isi sebagian memori game | tidak, jangan kirim sama sekali |
| `user\screenshots\` (F12) | tangkapan layar | boleh ditempel di postingan untuk memperjelas |

Jangan kirim file game (dump, `.pkg`, `eboot.bin`, atau isi folder `game\`) dan save milik orang lain.

## 4. Tracer (untuk masalah gambar atau suara)

Dinyalakan di launcher sebelum menekan Play, dan hanya berlaku untuk sesi berikutnya.

1. Centang **Tracer** (satu kotak untuk render trace, tangkapan F11, dan log audio).
2. Tekan **PLAY** dan mainkan sampai masalahnya terjadi.
3. Tekan **F11** saat masalahnya terlihat di layar: tangkapan frame dan keadaannya tersimpan, dan jamnya
   dicatat di log audio. Boleh beberapa kali (misalnya 3-5 kali di momen berbeda).
4. Tutup game dengan normal (**Stop game**), lalu tekan **Report** dan tulis apa yang kamu lihat dan tekan.

Catatan:

- Gambar F11 di dalam zip dikompres supaya beberapa muat dalam 9 MB: satu atau dua gambar tetap tanpa
  kehilangan; tiga atau lebih dikurangi presisi warnanya (7, 6, atau 5 bit per kanal; gradasi gelap bisa
  kehilangan detail). Berkas aslinya di `user\captures\` tidak diubah. `report.txt` menyebut cara yang dipakai
  untuk tiap gambar.
- **Pembersihan otomatis:** saat launcher dibuka, berkas Tracer (gambar F11 dan folder trace) dari sesi yang
  lebih lama daripada 20 sesi terakhir dihapus. Yang dihitung 20 sesi, termasuk sesi biasa tanpa Tracer.
  Salin dulu gambar F11 yang ingin kamu simpan. Zip di `user\reports\` juga disimpan 20 terakhir.
- Tombol **Open audio log**, **Open render log**, dan **Open captures** untuk melihat hasilnya sendiri.
- Matikan Tracer lagi setelah selesai: perekaman memakai ruang disk dan sedikit performa.
- **+ WAV capture (developers, up to 3 GB)** hanya bila pengembang memintanya (butuh Tracer). Rekaman audio
  game dibuat tanpa kompresi ke `user\audio_wav\` (sekitar 70 MB per menit, maksimal 3 GB), berlaku untuk
  satu sesi, dan **tidak pernah masuk zip**. Pembersihan otomatis tidak menyentuh `user\audio_wav\`: launcher
  bertanya dulu sebelum menghapusnya (di atas 50 MB, jawaban awal No), dan memulai sesi WAV baru
  menggantikan rekaman lama. Hapus sendiri foldernya bila sudah tidak perlu.

## 5. Templat laporan

```
Versi:                (versi launcher)
Gejala (satu kalimat):
Jam:                  (± 5 detik)
Di mana:              (stage / cutscene / menu / dialog)
Terjadi:              sekali / berulang / selalu di tempat yang sama
Pulih sendiri:        ya / tidak
Terakhir normal:      (versi atau sesi sebelumnya, bila ingat)

Zip laporan:          (Save report atau Report; hanya zip, tanpa berkas mentah dari user\)
```
