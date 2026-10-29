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
TOPIC_TELEMETRY_B = "helmguard/kelompok03/edge_b/telemetry"
TOPIC_COMMAND_B = "helmguard/kelompok03/edge_b/command"
ALT_COMMAND_B = "helmguard/kelompok_03/edge_b/command"
TOPIC_COMMAND_A = "helmguard/kelompok03/edge_a/command"

sim_mode = False
sos_active_received = False
last_shake_ack_time = 0

def run_hardware_diagnostics():
    """Uji mandiri perangkat keras HP B: Speaker dan Getar"""
    print("\n🔍 [DIAGNOSTIK PERANGKAT HP B]")
    try:
        subprocess.run(["termux-volume", "music", "15"], capture_output=True, timeout=3)
        subprocess.run(["termux-tts-speak", "-l", "id-ID", "Unit Penolong siap aktif"], capture_output=True, timeout=5)
        print("   🔊 Audio TTS: Berhasil dipanggil (Volume disetel ke 15)")
    except Exception as e:
        print(f"   ⚠️ Audio TTS Catatan: {e}")

    try:
        subprocess.run(["termux-vibrate", "-d", "300"], capture_output=True, timeout=3)
        print("   📳 Getaran Motor: OK")
    except Exception as e:
        print(f"   ⚠️ Getaran Catatan: {e}")
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
        return accel, gyro
    except Exception as e:
        print(f"⚠️ [MODE SIMULASI AKTIF] termux-sensor tidak tersedia ({e}).")
        print("   Menjalankan simulasi sensor otomatis (bisa dijalankan di laptop/PC).")
        sim_mode = True
        return "sim_accel", "sim_gyro"

def read_and_process(accel_name, gyro_name):
    if sim_mode:
        import random
        impact_score = round(random.uniform(0.05, 0.4), 2)
        gyro_rate = round(random.uniform(0.01, 0.2), 2)
        return {
            "device": "HP_B",
            "impact": impact_score,
            "gyro_rate": gyro_rate,
            "shake_ack": False,
            "timestamp": int(time.time())
        }

    active_list = [s for s in (accel_name, gyro_name) if s is not None]
    if not active_list:
        return {"device": "HP_B", "impact": 0.0, "gyro_rate": 0.0, "shake_ack": False, "timestamp": int(time.time())}

    try:
        res = subprocess.run(["termux-sensor", "-s", ",".join(active_list), "-n", "3"], capture_output=True, text=True, timeout=6)
        out = res.stdout
    except Exception:
        out = ""

    max_impact = 0.0
    max_gyro = 0.0

    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(out):
        out_strip = out[idx:].lstrip()
        if not out_strip:
            break
        try:
            data, offset = decoder.raw_decode(out_strip)
            idx += (len(out[idx:]) - len(out_strip)) + offset

            if accel_name and accel_name in data:
                vals = data[accel_name].get("values", [0.0, 0.0, 9.81])
                while len(vals) < 3:
                    vals.append(0.0)
                ax, ay, az = vals[:3]
                svm = math.sqrt(ax**2 + ay**2 + az**2)
                imp = abs(svm - 9.81)
                if imp > max_impact:
                    max_impact = imp

            if gyro_name and gyro_name in data:
                gvals = data[gyro_name].get("values", [0.0, 0.0, 0.0])
                while len(gvals) < 3:
                    gvals.append(0.0)
                gx, gy, gz = gvals[:3]
                gr = math.sqrt(gx**2 + gy**2 + gz**2)
                if gr > max_gyro:
                    max_gyro = gr
        except Exception:
            break

    impact_score = round(max_impact, 2)
    gyro_rate = round(max_gyro if gyro_name else (impact_score / 1.5), 2)

    # Deteksi Gesture Shake: Guncangan ayunan tangan disengaja (impact >= 3.5 m/s² atau rotasi cepat >= 2.5 rad/s)
    is_shaken = (impact_score >= 3.5) or (gyro_rate >= 2.5)

    return {
        "device": "HP_B",
        "impact": impact_score,
        "gyro_rate": gyro_rate,
        "shake_ack": is_shaken,
        "timestamp": int(time.time())
    }

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"✅ [HP B TERHUBUNG KE CLOUD] Terhubung ke {BROKER}!")
        client.subscribe([(TOPIC_COMMAND_B, 1), (ALT_COMMAND_B, 1)])
        print(f"   Mendengarkan peringatan darurat pada {TOPIC_COMMAND_B}")
    else:
        print(f"❌ [GAGAL KONEK] Kode error: {rc}")

def _run_sos_alert(message):
    """Peringatan audio & visual di HP B saat rekan HP A terjatuh"""
    print(f"\n🚨 [SINYAL BAHAYA DITERIMA DI HP B] {message}")
    try:
        # Maksimalkan volume suara
        subprocess.run(["termux-volume", "music", "15"], capture_output=True)
        # Notifikasi bawaan Android bersuara keras
        subprocess.run([
            "termux-notification", "--id", "201",
            "--title", "🚨 SINYAL DARURAT HP A!",
            "--content", f"{message} Goyangkan HP ini untuk merespons!",
            "--sound", "--vibrate", "2000"
        ], capture_output=True)
        # Getaran paksa 2 detik
        subprocess.run(["termux-vibrate", "-f", "-d", "2000"], capture_output=True)
        # Suara TTS panduan
        try:
            subprocess.run(["termux-tts-speak", "-l", "id-ID", f"Peringatan! {message}. Goyangkan HP ini untuk mengirim bantuan."], timeout=6)
        except Exception:
            subprocess.run(["termux-tts-speak", f"Emergency! Unit A has crashed. Shake this phone to dispatch rescue."], timeout=6)
    except Exception as e:
        print(f"⚠️ Alert feedback error: {e}")

def _run_ack_confirm():
    """Umpan balik saat balasan HP B berhasil diterima cloud"""
    try:
        subprocess.run(["termux-vibrate", "-d", "400"], capture_output=True)
        try:
            subprocess.run(["termux-tts-speak", "-l", "id-ID", "Sinyal balasan berhasil terkirim ke HP A."], timeout=4)
        except Exception:
            subprocess.run(["termux-tts-speak", "Rescue signal dispatched to Unit A."], timeout=4)
    except Exception as e:
        print(f"⚠️ Ack feedback error: {e}")

def on_message(client, userdata, msg):
    global sos_active_received
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return

    alert_type = payload.get("alert")
    message = payload.get("message", "")
    print(f"\n🚨 [SINYAL MASUK DARI CLOUD] {alert_type}: {message}")

    if alert_type in ["SOS_NIGHT_CRASH", "SOS_DAY_CRASH", "SHAKE_SOS_A"]:
        sos_active_received = True
        threading.Thread(target=_run_sos_alert, args=(message,), daemon=True).start()
    elif alert_type == "ACK_SENT":
        sos_active_received = False
        threading.Thread(target=_run_ack_confirm, daemon=True).start()

def connect_resilient():
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

            if connected_evt.wait(timeout=4.0):
                print(f"✅ Terhubung & Terkonfirmasi ke {host}:{port}!")
                global BROKER
                BROKER = f"{host}:{port}"
                return c
            else:
                print(f"⚠️ Jalur {host}:{port} timeout (tidak ada CONNACK), ganti jalur...")
                c.loop_stop()
                c.disconnect()
        except Exception as e:
            print(f"⚠️ Jalur {host}:{port} gagal ({e}), coba jalur berikutnya...")
    raise RuntimeError("Gagal konek ke broker. Periksa koneksi kuota / WiFi HP!")

def main():
    global sos_active_received, last_shake_ack_time
    run_hardware_diagnostics()
    accel_name, gyro_name = get_sensor_names()
    print(f"Sensor HP B: {accel_name} | {gyro_name or 'Estimasi Accel'}")
    client = connect_resilient()

    print("🚀 [HP B SIAP] Standby mendengarkan alarm & membaca sensor...")
    try:
        while True:
            telemetry = read_and_process(accel_name, gyro_name)
            
            # 1. Kirim telemetri HP B ke Cloud (Server AI)
            info = client.publish(TOPIC_TELEMETRY_B, json.dumps(telemetry), qos=1)
            client.publish("helmguard/kelompok_03/edge_b/telemetry", json.dumps(telemetry), qos=1)
            info.wait_for_publish(timeout=1.0)

            now = time.time()
            # 2. Jika gestur guncang terdeteksi
            if telemetry["shake_ack"]:
                if now - last_shake_ack_time > 2.0:
                    last_shake_ack_time = now
                    print(f"\n👋 [GESTURE SHAKE TERDETEKSI: {telemetry['impact']} m/s²] -> Sinyal konfirmasi bantuan dikirim ke Cloud!")
                    
                    # Berikan respon suara lokal di HP B
                    try:
                        subprocess.run(["termux-vibrate", "-d", "300"], capture_output=True)
                        subprocess.run(["termux-tts-speak", "-l", "id-ID", "Bantuan dikirim"], timeout=3)
                    except Exception:
                        pass

                    # Direct peer-to-peer backup jika darurat HP A sedang aktif
                    if sos_active_received:
                        print("   Mengirim instruksi RESCUE langsung ke HP A (Peer-to-Peer Fallback)...")
                        client.publish(TOPIC_COMMAND_A, json.dumps({
                            "action": "RESCUE_ON_THE_WAY",
                            "is_dark": True
                        }), qos=1)
                        client.publish("helmguard/kelompok_03/edge_a/command", json.dumps({
                            "action": "RESCUE_ON_THE_WAY",
                            "is_dark": True
                        }), qos=1)
                        sos_active_received = False
            else:
                mode_str = "🚨 MENUNGGU GUNCANGAN RESCUE" if sos_active_received else "STANDBY NORMAL"
                status_kirim = "TERKIRIM ✅" if info.is_published() else "PENDING ⚠️"
                print(f"[{status_kirim}][{mode_str}] Impact B: {telemetry['impact']:<4} m/s² | Gyro: {telemetry['gyro_rate']:<4} rad/s")

            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nHP B dihentikan oleh pengguna.")
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
