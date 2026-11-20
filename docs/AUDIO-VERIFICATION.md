# Audio di Basara 4 PC

Suara game dibuat oleh grafik suara Ngs2 versi perangkat lunak di runtime port ini: voice sampler
(PCM16, float, ATRAC9 lewat LibAtrac9), submixer yang memanggil callback efek milik game, dan
masterer yang mengirim hasilnya ke AudioOut. Reverb, EQ, dan filter belum diterapkan
(lihat `NOTICE.md`).

## Opsi audio di launcher

| Opsi | Efek |
|---|---|
| **Tracer** | Satu kotak untuk render trace, tangkapan F11, dan log audio. Log audio mencatat kejadian suara ke `user\audio_debug.log` (maksimal 64 MB; log sesi sebelumnya disimpan sebagai `audio_debug.previous.log`) dan tiap penekanan **F11** sebagai penanda dengan jam yang sama dengan render trace. Ringkasan level suara dicatat per detik untuk seluruh sesi, dan lebih rinci di sekitar tiap F11. Tombol **Open audio log** membukanya (untuk kamu baca sendiri). |
| **+ WAV capture (developers, up to 3 GB)** | Hanya bila pengembang memintanya; butuh Tracer. Merekam mix akhir dan setiap suara panjang tanpa kompresi ke `user\audio_wav\` (sekitar 70 MB per menit, maksimal 3 GB). Berlaku untuk satu kali start, lalu mati sendiri. Rekaman WAV tidak pernah masuk zip laporan. |

Nyalakan Tracer bila masalahnya suara, dan matikan lagi setelah selesai. `user\audio_wav\` tidak dihapus oleh
pembersihan otomatis: saat launcher dibuka ia menawarkan menghapus rekaman lama yang besar (di atas 50 MB, jawaban
awal No), dan memulai sesi WAV baru menggantikan rekaman lama. Hapus sendiri foldernya bila sudah tidak perlu.

## Melaporkan masalah suara

Ikuti [panduan melaporkan bug](BUG-SCAN-GUIDE.md): nyalakan **Tracer**, mainkan sampai suaranya bermasalah, tekan
**F11** saat itu, tutup game dengan **Stop game**, lalu tekan **Report** dan kirim **hanya zip-nya**. Berkas mentah
di `user\` (termasuk `audio_debug.log`) tidak disamarkan, jadi jangan dikirim. Untuk masalah suara, sebutkan juga:

- apa yang terdengar: musik hilang, dialog terulang atau terpotong, suara putus-putus, terlalu keras
  atau pecah, hanya di satu sisi, atau tertinggal dari gambar;
- jam kejadian dan di mana (stage, cutscene, menu);
- perangkat audio yang dipakai (speaker, headset, HDMI) dan jumlah kanalnya bila tahu (stereo atau 5.1/7.1).
