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

# Menggunakan broker.emqx.io (sesuai slide hal 8 & 12) TANPA SSL agar tidak kena ConnectionResetError di 4G!
BROKER = "broker.emqx.io"
PORT = 1883
TOPIC_TELEMETRY_A = "helmguard/kelompok03/edge_a/telemetry"
TOPIC_COMMAND_A = "helmguard/kelompok03/edge_a/command"
ALT_COMMAND_A = "helmguard/kelompok_03/edge_a/command"

lux_history = []
sim_mode = False

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
        # Simulasi nilai normal
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

    # 1. Olah Data A: SVM & Impact Magnitude
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

def _run_rescue_feedback(is_dark):
    """Jalankan getaran, audio TTS, dan strobo senter SOS secara asinkron agar tidak memblokir MQTT"""
    try:
        subprocess.run(["termux-vibrate", "-d", "1000"])
        subprocess.run(["termux-notification", "--title", "🚑 BANTUAN DATANG!", "--content", "Unit B sedang menuju lokasimu!"])
        subprocess.run(["termux-tts-speak", "Konfirmasi diterima. Bantuan sedang menuju lokasi Anda."])
    except Exception:
        pass

    if is_dark:
        print("💡 [SENTER SOS] Menyalakan pola SOS Morse (... --- ...) dengan senter kamera!")
        try:
            # SOS Morse Code: 3 pendek, 3 panjang, 3 pendek
            morse = [0.2, 0.2, 0.2, 0.6, 0.6, 0.6, 0.2, 0.2, 0.2]
            for durasi in morse:
                try:
                    subprocess.run(["termux-torch", "on"])
                    time.sleep(durasi)
                    subprocess.run(["termux-torch", "off"])
                    time.sleep(0.2)
                except Exception:
                    break
        finally:
            try:
                subprocess.run(["termux-torch", "off"])
            except Exception:
                pass

def _run_crash_warning():
    try:
        subprocess.run(["termux-vibrate", "-d", "600"])
        subprocess.run(["termux-tts-speak", "Terdeteksi benturan. Sinyal S O S dikirim."])
    except Exception:
        pass

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return

    action = payload.get("action")
    print(f"\n🔔 [MASUK DARI CLOUD] Perintah: {action}")

    if action == "RESCUE_ON_THE_WAY":
        is_dark = payload.get("is_dark", True)
        threading.Thread(target=_run_rescue_feedback, args=(is_dark,), daemon=True).start()
    elif action == "WARNING_CRASH_DETECTED":
        threading.Thread(target=_run_crash_warning, daemon=True).start()

def connect_resilient():
    """Mencoba koneksi ke broker secara andal"""
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
            c.on_connect = on_connect
            c.on_message = on_message
            c.connect(host, port, 60)
            global BROKER
            BROKER = f"{host}:{port}"
            return c
        except Exception as e:
            print(f"⚠️ Jalur {host}:{port} gagal ({e}), coba jalur berikutnya...")
    raise RuntimeError("Semua broker gagal dihubungi. Periksa koneksi internet HP!")

def main():
    accel_name, gyro_name, light_name = get_sensor_names()
    print(f"Sensor Aktif: {accel_name} | {gyro_name or 'Tanpa-Gyro'} | {light_name}")

    client = connect_resilient()
    client.loop_start()

    print("🚀 [HP A SIAP] Mengirim telemetri berkala ke cloud...")
    try:
        while True:
            telemetry = read_and_process(accel_name, gyro_name, light_name)
            info = client.publish(TOPIC_TELEMETRY_A, json.dumps(telemetry), qos=1)
            # Juga publikasikan ke topic alternatif untuk interoperabilitas
            client.publish("helmguard/kelompok_03/edge_a/telemetry", json.dumps(telemetry), qos=1)
            
            status_kirim = "TERKIRIM ✅" if info.is_published() else "PENDING ⚠️"
            print(f"[{status_kirim}] Impact: {telemetry['impact']:<4} | Tilt: {telemetry['tilt_deg']:<4}° | Lux: {telemetry['lux']:<5}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nHP A dihentikan oleh pengguna.")
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
