# Audio di Basara 4 PC

Suara game dibuat oleh grafik suara Ngs2 versi perangkat lunak di runtime port ini: voice sampler
(PCM16, float, ATRAC9 lewat LibAtrac9), submixer yang memanggil callback efek milik game, dan
masterer yang mengirim hasilnya ke AudioOut. Reverb, EQ, dan filter belum diterapkan
(lihat `NOTICE.md`).

## Opsi audio di launcher

| Opsi | Efek |
|---|---|
| **Audio debug log** | Mencatat kejadian suara ke `user\audio_debug.log` (maksimal 64 MB). Log sesi sebelumnya disimpan sebagai `audio_debug.previous.log`. Tombol **Open audio log** membukanya. |
| **+ WAV capture (up to 3 GB)** | Bersama Audio debug log, merekam mix akhir dan setiap suara panjang tanpa kompresi ke `user\audio_wav\` (sekitar 70 MB per menit, maksimal 3 GB). Berlaku untuk satu kali start, lalu mati sendiri. Saat dibuka, launcher menawarkan menghapus rekaman lama yang besar. |

Kedua opsi hanya perlu dinyalakan bila diminta saat melaporkan masalah suara. Matikan lagi setelah
selesai, dan hapus `user\audio_wav\` setelah rekamannya dikirim.

## Melaporkan masalah suara

Ikuti [panduan melaporkan bug](BUG-SCAN-GUIDE.md). Untuk masalah suara, sebutkan juga:

- apa yang terdengar: musik hilang, dialog terulang atau terpotong, suara putus-putus, terlalu keras
  atau pecah, hanya di satu sisi, atau tertinggal dari gambar;
- jam kejadian dan di mana (stage, cutscene, menu);
- perangkat audio yang dipakai (speaker, headset, HDMI) dan jumlah kanalnya bila tahu (stereo atau 5.1/7.1).
