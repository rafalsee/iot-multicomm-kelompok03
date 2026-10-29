import json
import math
import subprocess
import sys
import threading
import time
import paho.mqtt.client as mqtt

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ==========================================
# KONFIGURASI MQTT BROKER & TOPIC
# ==========================================
BROKER = "broker.emqx.io"
PORT = 1883
TOPIC_TELEMETRY_A = "helmguard/kelompok03/edge_a/telemetry"
TOPIC_COMMAND_A = "helmguard/kelompok03/edge_a/command"
ALT_COMMAND_A = "helmguard/kelompok_03/edge_a/command"

lux_history = []
sim_mode = False
torch_lock = threading.Lock()

def run_hardware_diagnostics():
    """Uji mandiri perangkat keras: Speaker, Getar, dan Senter Kamera"""
    print("\n🔍 [DIAGNOSTIK PERANGKAT HP A]")
    
    # 1. Test Volume & Suara
    try:
        subprocess.run(["termux-volume", "music", "15"], capture_output=True, timeout=3)
        subprocess.run(["termux-tts-speak", "-l", "id-ID", "Helm Guard siap aktif"], capture_output=True, timeout=5)
        print("   🔊 Audio TTS: Berhasil dipanggil (Volume disetel ke 15)")
    except Exception as e:
        print(f"   ⚠️ Audio TTS Catatan: {e}")

    # 2. Test Getar
    try:
        subprocess.run(["termux-vibrate", "-d", "300"], capture_output=True, timeout=3)
        print("   📳 Getaran Motor: OK")
    except Exception as e:
        print(f"   ⚠️ Getaran Catatan: {e}")

    # 3. Test Senter Kamera (Torch)
    try:
        t_on = subprocess.run(["termux-torch", "on"], capture_output=True, text=True, timeout=3)
        if t_on.returncode == 0:
            time.sleep(0.3)
            subprocess.run(["termux-torch", "off"], capture_output=True, timeout=3)
            print("   💡 Senter Kamera (Torch): OK (Izin Kamera Aktif)")
        else:
            err = t_on.stderr.strip() or "Izin kamera belum diberikan"
            print(f"   ❌ Senter Kamera Gagal: {err}")
            print("      👉 SOLUSI: Buka Pengaturan HP > Aplikasi > Termux:API > Izin > Berikan izin KAMERA!")
    except Exception as e:
        print(f"   ⚠️ Senter Kamera Catatan: {e}")
    print("--------------------------------------------------\n")

def get_sensor_names():
    global sim_mode
    try:
        res = subprocess.run(["termux-sensor", "-l"], capture_output=True, text=True, timeout=5)
        if res.returncode != 0 or not res.stdout.strip():
            raise RuntimeError("termux-sensor mengembalikan output kosong")
        sensors = json.loads(res.stdout).get("sensors", [])
        accel = next((s for s in sensors if any(k in s.lower() for k in ["accelerometer", "accel", "gravity"]) and "uncalibrated" not in s.lower()), None)
        gyro = next((s for s in sensors if any(k in s.lower() for k in ["gyroscope", "gyro"]) and "uncalibrated" not in s.lower()), None)
        light = next((s for s in sensors if "light" in s.lower()), None)
        return accel, gyro, light
    except Exception as e:
        print(f"⚠️ [MODE SIMULASI AKTIF] termux-sensor tidak tersedia ({e}).")
        print("   Menjalankan simulasi sensor otomatis (bisa dijalankan di laptop/PC).")
        sim_mode = True
        return "sim_accel", "sim_gyro", "sim_light"

def read_and_process(accel_name, gyro_name, light_name):
    if sim_mode:
        import random
        ax = random.uniform(-0.3, 0.3)
        ay = random.uniform(-0.3, 0.3)
        az = 9.81 + random.uniform(-0.2, 0.2)
        raw_lux = 120.0 + random.uniform(-5, 5)
        gx, gy, gz = 0.0, 0.0, 0.0
    else:
        active_list = [s for s in (accel_name, gyro_name, light_name) if s is not None]
        sensor_query = ",".join(active_list)
        try:
            res = subprocess.run(
                ["termux-sensor", "-s", sensor_query, "-n", "1"],
                capture_output=True, text=True, timeout=6
            )
            out = res.stdout
            decoder = json.JSONDecoder()
            idx = 0
            data = {}
            while idx < len(out):
                out_strip = out[idx:].lstrip()
                if not out_strip:
                    break
                try:
                    obj, offset = decoder.raw_decode(out_strip)
                    data.update(obj)
                    idx += (len(out[idx:]) - len(out_strip)) + offset
                except Exception:
                    break
        except Exception:
            data = {}

        accel_vals = data.get(accel_name, {}).get("values", [0.0, 0.0, 9.81]) if accel_name else [0.0, 0.0, 9.81]
        while len(accel_vals) < 3:
            accel_vals.append(0.0)
        ax, ay, az = accel_vals[:3]

        light_vals = data.get(light_name, {}).get("values", [100.0]) if light_name else [100.0]
        raw_lux = light_vals[0] if light_vals else 100.0

        if gyro_name and gyro_name in data:
            gyro_vals = data[gyro_name].get("values", [0.0, 0.0, 0.0])
            while len(gyro_vals) < 3:
                gyro_vals.append(0.0)
            gx, gy, gz = gyro_vals[:3]
        else:
            gx, gy, gz = (ax / 5.0), 0.0, 0.0

    # 1. Olah Data A: Signal Vector Magnitude (SVM) & Ambang Benturan
    svm = math.sqrt(ax**2 + ay**2 + az**2)
    impact_score = round(abs(svm - 9.81), 2)

    # 2. Olah Data B: Sudut Kemiringan (Tilt Angle dalam Derajat)
    tilt_cos = max(-1.0, min(1.0, az / max(svm, 0.01)))
    tilt_angle = round(math.degrees(math.acos(tilt_cos)), 1)

    # 3. Olah Data C: Kecepatan Rotasi Gyroscope
    gyro_rate = round(math.sqrt(gx**2 + gy**2 + gz**2), 2)

    # 4. Olah Data D: Moving Average Intensitas Cahaya (EMA Lux)
    lux_history.append(raw_lux)
    if len(lux_history) > 3:
        lux_history.pop(0)
    smooth_lux = round(sum(lux_history) / len(lux_history), 1)

    return {
        "device": "HP_A",
        "impact": impact_score,
        "tilt_deg": tilt_angle,
        "gyro_rate": gyro_rate,
        "lux": smooth_lux,
        "timestamp": int(time.time())
    }

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"✅ [HP A TERHUBUNG] Sukses konek ke broker {BROKER}!")
        client.subscribe([(TOPIC_COMMAND_A, 1), (ALT_COMMAND_A, 1)])
        print(f"   Mendengarkan instruksi pada {TOPIC_COMMAND_A}")
    else:
        print(f"❌ [GAGAL KONEK] Kode error: {rc}")

def _blink_torch_sos(repeat=2):
    """Kedipkan senter kamera dengan kode SOS Morse (... --- ...)"""
    with torch_lock:
        print("💡 [SENTER SOS] Memancarkan strobo darurat senter kamera...")
        # Pola Morse SOS: 3 pendek (0.2s), 3 panjang (0.5s), 3 pendek (0.2s)
        morse = [0.2, 0.2, 0.2, 0.5, 0.5, 0.5, 0.2, 0.2, 0.2]
        try:
            for _ in range(repeat):
                for durasi in morse:
                    t_on = subprocess.run(["termux-torch", "on"], capture_output=True, text=True)
                    if t_on.returncode != 0:
                        print(f"   ⚠️ Senter kamera tidak merespon: Pastikan izin Kamera diaktifkan di Termux:API!")
                        return
                    time.sleep(durasi)
                    subprocess.run(["termux-torch", "off"], capture_output=True)
                    time.sleep(0.15)
        except Exception as e:
            print(f"   ⚠️ Error senter: {e}")
        finally:
            subprocess.run(["termux-torch", "off"], capture_output=True)

def _run_crash_warning(is_dark=True):
    """Peringatan seketika saat benturan/kecelakaan terdeteksi pada HP A"""
    print("\n🚨 [PERINGATAN DARURAT AKTIF DI HP A] Mengaktifkan audio keras & sinyal darurat...")
    try:
        # 1. Maksimalkan volume musik/media
        subprocess.run(["termux-volume", "music", "15"], capture_output=True)
        # 2. Bunyikan nada notifikasi bawaan HP
        subprocess.run([
            "termux-notification", "--id", "101",
            "--title", "🚨 PERINGATAN DARURAT!",
            "--content", "Terdeteksi benturan. Sinyal SOS dikirim ke rekan konvoi!",
            "--sound", "--vibrate", "1000"
        ], capture_output=True)
        # 3. Ucapkan suara peringatan TTS
        try:
            subprocess.run(["termux-tts-speak", "-l", "id-ID", "Peringatan! Terdeteksi benturan. Sinyal S O S dikirim."], timeout=5)
        except Exception:
            subprocess.run(["termux-tts-speak", "Warning! Crash detected. Sending SOS signal."], timeout=5)
    except Exception as e:
        print(f"⚠️ Warning feedback error: {e}")

    # 4. Langsung nyalakan strobo senter SOS seketika agar terlihat pengendara lain
    _blink_torch_sos(repeat=2)

def _run_rescue_feedback(is_dark=True):
    """Umpan balik saat rekan HP B telah mengonfirmasi bantuan (Rescue Dispatched)"""
    print("\n🚑 [BANTUAN DATANG!] Menerima konfirmasi dari Unit B. Mengaktifkan panduan suara...")
    try:
        subprocess.run(["termux-volume", "music", "15"], capture_output=True)
        subprocess.run([
            "termux-notification", "--id", "102",
            "--title", "🚑 BANTUAN DALAM PERJALANAN!",
            "--content", "Unit B sedang menuju lokasimu!",
            "--sound", "--vibrate", "1500"
        ], capture_output=True)
        try:
            subprocess.run(["termux-tts-speak", "-l", "id-ID", "Konfirmasi diterima. Bantuan sedang menuju lokasi Anda."], timeout=5)
        except Exception:
            subprocess.run(["termux-tts-speak", "Confirmation received. Help is on the way."], timeout=5)
    except Exception as e:
        print(f"⚠️ Rescue feedback error: {e}")

    # Nyalakan strobo intensif untuk memandu penolong
    _blink_torch_sos(repeat=3)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return

    action = payload.get("action")
    print(f"\n🔔 [MASUK DARI CLOUD] Perintah: {action}")

    if action == "WARNING_CRASH_DETECTED":
        is_dark = payload.get("is_dark", True)
        threading.Thread(target=_run_crash_warning, args=(is_dark,), daemon=True).start()
    elif action == "RESCUE_ON_THE_WAY":
        is_dark = payload.get("is_dark", True)
        threading.Thread(target=_run_rescue_feedback, args=(is_dark,), daemon=True).start()

def connect_resilient():
    """Mencoba koneksi ke broker secara andal sampai terkonfirmasi sukses"""
    jalur_koneksi = [
        ("broker.emqx.io", 1883, "tcp", None),
        ("broker.emqx.io", 8083, "websockets", "/mqtt"),
        ("broker.hivemq.com", 1883, "tcp", None),
        ("test.mosquitto.org", 1883, "tcp", None),
    ]
    for host, port, transport, path in jalur_koneksi:
        try:
            print(f"Mencoba jalur {host}:{port} ({transport})...")
            try:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport=transport)
            except AttributeError:
                c = mqtt.Client(transport=transport)
            if path:
                c.ws_set_options(path=path)

            connected_evt = threading.Event()
            def make_on_connect(prev_handler):
                def _oc(client, userdata, flags, rc, props=None):
                    if rc == 0:
                        connected_evt.set()
                    if prev_handler:
                        prev_handler(client, userdata, flags, rc, props)
                return _oc

            c.on_connect = make_on_connect(on_connect)
            c.on_message = on_message
            c.connect(host, port, 60)
            c.loop_start()

            # Tunggu konfirmasi CONNACK dari broker maksimal 4 detik
            if connected_evt.wait(timeout=4.0):
                print(f"✅ Terhubung & Terkonfirmasi ke {host}:{port}!")
                global BROKER
                BROKER = f"{host}:{port}"
                return c
            else:
                print(f"⚠️ Jalur {host}:{port} timeout (tidak ada CONNACK), ganti jalur cadangan...")
                c.loop_stop()
                c.disconnect()
        except Exception as e:
            print(f"⚠️ Jalur {host}:{port} gagal ({e}), coba jalur berikutnya...")
    raise RuntimeError("Semua broker gagal dihubungi. Periksa koneksi kuota / WiFi HP!")

def main():
    run_hardware_diagnostics()
    accel_name, gyro_name, light_name = get_sensor_names()
    print(f"Sensor Aktif: {accel_name} | {gyro_name or 'Tanpa-Gyro'} | {light_name}")

    client = connect_resilient()

    print("🚀 [HP A SIAP] Mengirim telemetri berkala ke cloud...")
    try:
        while True:
            telemetry = read_and_process(accel_name, gyro_name, light_name)
            
            # Publikasikan telemetri ke Cloud dengan QoS 1
            info = client.publish(TOPIC_TELEMETRY_A, json.dumps(telemetry), qos=1)
            client.publish("helmguard/kelompok_03/edge_a/telemetry", json.dumps(telemetry), qos=1)
            
            # Tunggu konfirmasi PUBACK (maksimal 1.5 detik) agar tidak selalu PENDING
            info.wait_for_publish(timeout=1.5)
            status_kirim = "TERKIRIM ✅" if info.is_published() else "PENDING ⚠️"
            
            print(f"[{status_kirim}] Impact: {telemetry['impact']:<4} | Tilt: {telemetry['tilt_deg']:<4}° | Lux: {telemetry['lux']:<5}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nHP A dihentikan oleh pengguna.")
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
