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

BROKER = "broker.emqx.io"
PORT = 1883
TOPIC_TELEMETRY_B = "helmguard/kelompok03/edge_b/telemetry"
TOPIC_COMMAND_B = "helmguard/kelompok03/edge_b/command"
ALT_COMMAND_B = "helmguard/kelompok_03/edge_b/command"
TOPIC_COMMAND_A = "helmguard/kelompok03/edge_a/command"

sim_mode = False
sos_active_received = False
last_shake_ack_time = 0

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

    # Ambil 3 sampel berturut-turut (-n 3) agar guncangan tangan (shake) tertangkap akurat
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
    try:
        subprocess.run(["termux-vibrate", "-d", "2000"])
        subprocess.run(["termux-notification", "--title", "🚨 SINYAL DARURAT HP A!", "--content", message])
        subprocess.run(["termux-tts-speak", f"Peringatan! {message}. Goyangkan HP ini untuk mengirim bantuan."])
    except Exception:
        pass

def _run_ack_confirm():
    try:
        subprocess.run(["termux-vibrate", "-d", "400"])
        subprocess.run(["termux-tts-speak", "Sinyal balasan berhasil terkirim ke HP A."])
    except Exception:
        pass

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
            c.on_connect = on_connect
            c.on_message = on_message
            c.connect(host, port, 60)
            global BROKER
            BROKER = f"{host}:{port}"
            return c
        except Exception as e:
            print(f"⚠️ Jalur {host}:{port} gagal ({e}), coba jalur berikutnya...")
    raise RuntimeError("Gagal konek ke broker. Periksa koneksi internet HP!")

def main():
    global sos_active_received, last_shake_ack_time
    accel_name, gyro_name = get_sensor_names()
    print(f"Sensor HP B: {accel_name} | {gyro_name or 'Estimasi Accel'}")
    client = connect_resilient()
    client.loop_start()

    print("🚀 [HP B SIAP] Standby mendengarkan alarm & membaca sensor...")
    try:
        while True:
            telemetry = read_and_process(accel_name, gyro_name)
            
            # 1. Kirim telemetri HP B ke Cloud (Server AI)
            client.publish(TOPIC_TELEMETRY_B, json.dumps(telemetry), qos=1)
            client.publish("helmguard/kelompok_03/edge_b/telemetry", json.dumps(telemetry), qos=1)

            now = time.time()
            # 2. Jika gestur guncang terdeteksi
            if telemetry["shake_ack"]:
                if now - last_shake_ack_time > 2.0:
                    last_shake_ack_time = now
                    print(f"👋 [GESTURE SHAKE TERDETEKSI: {telemetry['impact']} m/s²] -> Sinyal konfirmasi dikirim ke Cloud!")
                    
                    # Direct peer-to-peer backup jika darurat HP A sedang aktif
                    if sos_active_received:
                        print("   Mengirim konfirmasi langsung ke HP A (Peer-to-Peer Fallback)...")
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
                print(f"[{mode_str}] Impact B: {telemetry['impact']:<4} m/s² | Gyro: {telemetry['gyro_rate']:<4} rad/s")

            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nHP B dihentikan oleh pengguna.")
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
