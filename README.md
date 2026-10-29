# 🛡️ HelmGuard SOS — Smart Touring Convoy & Emergency Response System

Proyek IoT Mata Kuliah **Internet of Things (Semester 3)**.
Sistem perlindungan cerdas konvoi touring sepeda motor berbasis **Multisensor Fusion** dan **Komunikasi 2-Arah (Two-Way Communication)** menggunakan 2 perangkat Edge (Android via Termux), Cloud MQTT Broker, Server AI Decision Tree, dan Live Web Dashboard.

---

## 📌 1. Masalah & Latar Belakang Ide
* **Masalah:** Saat berkendara atau touring konvoi motor, kecelakaan tunggal (terjatuh ke jurang atau semak-semak) sering kali terlambat disadari oleh rekan di depan/belakang, terutama saat malam hari di jalur minim penerangan. Selain itu, alarm sering mengalami *false alarm* (salah deteksi) akibat jalan berlubang atau polisi tidur.
* **Solusi (HelmGuard SOS):**
  1. **HP A (Unit Lapangan / Helm Korban):** Membaca accelerometer, gyroscope, dan sensor cahaya untuk mendeteksi kecelakaan (benturan keras & posisi miring ekstrem).
  2. **HP B (Unit Komandan / Rekan Konvoi):** Menerima peringatan otomatis via getaran, notifikasi, dan Text-to-Speech (TTS).
  3. **Sensor Fusion (Anti False Alarm):** Jika kedua motor sama-sama terguncang secara bersamaan, sistem menyimpulkan itu adalah jalan berlubang/polisi tidur (`BUMPY_ROAD`) sehingga alarm kecelakaan **dibatalkan/ditekan**.
  4. **Komunikasi 2 Arah (Two-Way Rescue ACK):** Rekan di HP B cukup menggoyangkan HP (*shake gesture*). Cloud akan mengirim sinyal balasan ke HP A untuk menyalakan suara panduan dan **strobo senter kamera dengan kode SOS Morse (`... --- ...`)** jika kondisi sekitar gelap.

---

## 🏗️ 2. Arsitektur Sistem

```
       +---------------------------------------------+
       |             MQTT Cloud Broker               |
       |             (broker.emqx.io)                |
       +----------------------+----------------------+
                              |
     +------------------------+------------------------+
     |                        |                        |
     v                        v                        v
+------------+         +-------------+         +---------------+
|    HP A    |         |    HP B     |         |   Laptop /    |
| (Edge 1)   |         |  (Edge 2)   |         |   Server AI   |
| Korban /   |         | Penolong /  |         | Decision Tree |
| Unit Helm  |         | Rekan Motor |         | Sensor Fusion |
+------------+         +-------------+         +-------+-------+
                                                       |
                                                       v
                                            +--------------------+
                                            |   Web Dashboard    |
                                            | (Live WebSockets)  |
                                            +--------------------+
```

---

## 📊 3. Sensor & Data Processing

Sistem ini memenuhi ketentuan tugas dengan memanfaatkan **3 jenis sensor** dan **4 jenis pengolahan data numerik**:

| Sensor | Data Mentah | Pengolahan Data (*Data Processing*) | Satuan / Rumus |
| :--- | :--- | :--- | :--- |
| **Accelerometer** | $a_x, a_y, a_z$ | **1. Signal Vector Magnitude (SVM) & Impact Error** | $SVM = \sqrt{a_x^2 + a_y^2 + a_z^2}$<br>$Impact = \|SVM - 9.81\|$ (m/s²) |
| **Accelerometer** | $a_z, SVM$ | **2. Sudut Kemiringan (Tilt Angle)** | $\theta = \arccos\left(\frac{a_z}{SVM}\right) \times \frac{180}{\pi}$ (derajat) |
| **Gyroscope** | $g_x, g_y, g_z$ | **3. Resultan Kecepatan Sudut (Gyro Rate)** | $\omega = \sqrt{g_x^2 + g_y^2 + g_z^2}$ (rad/s) |
| **Light Sensor** | $Lux_{raw}$ | **4. Moving Average (Peredam Fluktuasi Cahaya)** | $Lux_{smooth} = \frac{1}{N}\sum Lux$ (lux) |

---

## 🧠 4. Algoritma AI & Decision Tree

Pengambilan keputusan darurat dilakukan melalui alur Decision Tree dan Sensor Fusion berikut:

```
                      [Data Masuk dari HP A & B]
                                  |
                                  v
                   Apakah KEDUA HP terguncang bersamaan
                 (Impact A > 2.5 & Impact B > 2.5 dlm 1.5s)?
                                  |
                   +--------------+--------------+
                  YA                             TIDAK
                   |                               |
                   v                               v
          [STATUS: BUMPY_ROAD]           Apakah HP A Miring > 55°
         (Polisi Tidur / Jalan Rusak)      ATAU Benturan Keras > 4.0?
          *Abaikan Alarm Kecelakaan*               |
                                            +------+------+
                                           YA            TIDAK
                                            |              |
                                            v              v
                                  [SOS_CRASH_ACTIVE]  [NORMAL_CONVOY]
                                            |
                         +------------------+------------------+
                         |                                     |
                  Cahaya < 50 lux?                     Cahaya >= 50 lux?
                         |                                     |
                         v                                     v
               [SOS_NIGHT_CRASH]                        [SOS_DAY_CRASH]
               Kirim ke HP B: Alarm                     Kirim ke HP B: Alarm
               HP A: Standby Senter                     HP A: Audio Peringatan
                         |
                         v
       Apakah HP B Diguncang (Shake ACK)?
                         |
                         v (YA)
               [RESCUE_DISPATCHED]
       HP A: Notifikasi, TTS, & Senter SOS Morse!
       HP B: TTS Konfirmasi Terkirim
```

---

## 📡 5. Topik MQTT & Format Payload JSON

Broker: `broker.emqx.io` (Port TCP `1883`, Port WebSocket TLS `8084`)

### A. Telemetri HP A (`helmguard/kelompok03/edge_a/telemetry`)
```json
{
  "device": "HP_A",
  "impact": 5.4,
  "tilt_deg": 68.2,
  "gyro_rate": 2.8,
  "lux": 15.0,
  "timestamp": 1793283000
}
```

### B. Telemetri HP B (`helmguard/kelompok03/edge_b/telemetry`)
```json
{
  "device": "HP_B",
  "impact": 4.2,
  "gyro_rate": 3.1,
  "shake_ack": true,
  "timestamp": 1793283000
}
```

### C. Perintah ke HP A (`helmguard/kelompok03/edge_a/command`)
```json
{
  "action": "RESCUE_ON_THE_WAY",
  "is_dark": true
}
```

### D. Peringatan ke HP B (`helmguard/kelompok03/edge_b/command`)
```json
{
  "alert": "SOS_NIGHT_CRASH",
  "message": "Unit A terjatuh (Kemiringan 68.2°, Malam/Gelap)!"
}
```

---

## 🚀 6. Panduan Menjalankan Sistem

### A. Persiapan Dependensi (Laptop / PC)
```bash
pip install -r requirements.txt
```

### B. Menjalankan Server AI (Laptop)
```bash
python server_ai.py
```

### C. Menjalankan di Android (Termux)
Pastikan aplikasi **Termux** dan **Termux:API** sudah terpasang dari F-Droid:
```bash
pkg update
pkg install python git termux-api
pip install paho-mqtt
```
1. **Di HP A (Unit Helm / Korban):**
   ```bash
   python edge_hp_a.py
   ```
2. **Di HP B (Unit Rekan Konvoi):**
   ```bash
   python edge_hp_b.py
   ```
*(Catatan: Jika script dijalankan di Laptop/PC tanpa Termux, program secara otomatis beralih ke Mode Simulasi Sensor sehingga tetap bisa diuji!)*

### D. Membuka Live Dashboard Web
Cukup klik ganda atau buka file `dashboard.html` di browser (Chrome / Edge / Firefox). Dashboard akan langsung terhubung ke Cloud MQTT via WebSockets WSS!

### E. Pengujian Interaktif Tanpa HP (Simulator)
Jika ingin mendemonstrasikan seluruh skenario sistem dalam video demo:
```bash
python test_sim.py
```
Pilih opsi:
* `1`: Normal Convoy
* `2`: Bumpy Road / Polisi Tidur (Sensor Fusion Demo)
* `3`: HP A Crash Malam Hari (Trigger SOS Strobe)
* `4`: HP A Crash Siang Hari
* `5`: HP B Shake Rescue Response
* `6`: Reset Sistem
