import json
import math
import subprocess
import time
import paho.mqtt.client as mqtt

BROKER = "broker.emqx.io"
PORT = 1883
TOPIC_TELEMETRY_A = "helmguard/kelompok03/edge_a/telemetry"
TOPIC_COMMAND_A = "helmguard/kelompok03/edge_a/command"

lux_history = []

def get_sensor_names():
    out = subprocess.run(["termux-sensor", "-l"], capture_output=True, text=True).stdout
    sensors = json.loads(out).get("sensors", [])
    accel = next((s for s in sensors if any(k in s.lower() for k in ["accelerometer", "accel", "gravity"]) and "uncalibrated" not in s.lower()), None)
    gyro = next((s for s in sensors if any(k in s.lower() for k in ["gyroscope", "gyro"]) and "uncalibrated" not in s.lower()), None)
    light = next((s for s in sensors if any(k in s.lower() for k in ["light", "als", "lux", "stk", "mn78", "tmd", "ltr"])), None)
    return accel, gyro, light

def read_and_process(accel_name, gyro_name, light_name):
    active_list = [s for s in (accel_name, gyro_name, light_name) if s is not None]
    sensor_query = ",".join(active_list)
    
    out = subprocess.run(["termux-sensor", "-s", sensor_query, "-n", "1"], capture_output=True, text=True, timeout=8).stdout
    data = json.loads(out)

    ax, ay, az = data.get(accel_name, {}).get("values", [0.0, 0.0, 9.8])[:3] if accel_name else [0.0, 0.0, 9.8]
    raw_lux = data.get(light_name, {}).get("values", [100.0])[0] if light_name else 100.0

    svm = math.sqrt(ax**2 + ay**2 + az**2)
    impact_score = round(abs(svm - 9.81), 2)
    tilt_angle = round(math.degrees(math.acos(max(-1.0, min(1.0, az / max(svm, 0.01))))), 1)

    if gyro_name and gyro_name in data:
        gx, gy, gz = data.get(gyro_name, {}).get("values", [0.0, 0.0, 0.0])[:3]
        gyro_rate = round(math.sqrt(gx**2 + gy**2 + gz**2), 2)
    else:
        gyro_rate = round(abs(ax) / 5.0, 2)

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
        print(f"✅ [TERHUBUNG KE CLOUD] Berhasil konek ke {BROKER}!")
        client.subscribe(TOPIC_COMMAND_A)
    else:
        print(f"❌ [GAGAL KONEK] Kode error: {rc}")

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    action = payload.get("action")
    print(f"\n🔔 [MASUK DARI CLOUD] Perintah: {action}")
    
    if action == "RESCUE_ON_THE_WAY":
        subprocess.run(["termux-vibrate", "-d", "1000"])
        subprocess.run(["termux-notification", "--title", "🚑 BANTUAN DATANG!", "--content", "Unit B sedang menuju lokasimu!"])
        subprocess.run(["termux-tts-speak", "Konfirmasi diterima. Bantuan sedang menuju lokasi Anda."])
        if payload.get("is_dark", True):
            for durasi in [0.2, 0.2, 0.2, 0.6, 0.6, 0.6, 0.2, 0.2, 0.2]:
                subprocess.run(["termux-torch", "on"])
                time.sleep(durasi)
                subprocess.run(["termux-torch", "off"])
                time.sleep(0.2)
    elif action == "WARNING_CRASH_DETECTED":
        subprocess.run(["termux-vibrate", "-d", "500"])
        subprocess.run(["termux-tts-speak", "Terdeteksi benturan. Sinyal S O S dikirim."])

def connect_resilient():
    jalur_koneksi = [
        ("broker.emqx.io", 1883, "tcp", None),
        ("broker.emqx.io", 8083, "websockets", "/mqtt"),
        ("broker.hivemq.com", 8000, "websockets", "/mqtt"),
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
            print(f"⚠️ Jalur {host}:{port} gagal ({e}), pindah jalur cadangan...")
    raise RuntimeError("Semua broker gagal dihubungi. Cek koneksi internet HP!")

def main():
    accel_name, gyro_name, light_name = get_sensor_names()
    print(f"Sensor Aktif: {accel_name} | {gyro_name or 'Tanpa-Gyro'} | {light_name}")

    client = connect_resilient()
    client.loop_start()

    try:
        while True:
            telemetry = read_and_process(accel_name, gyro_name, light_name)
            info = client.publish(TOPIC_TELEMETRY_A, json.dumps(telemetry), qos=1)
            info.wait_for_publish(timeout=3)
            status_kirim = "TERKIRIM ✅" if info.is_published() else "PENDING ⚠️"
            print(f"[{status_kirim}] Impact: {telemetry['impact']} | Tilt: {telemetry['tilt_deg']}° | Lux: {telemetry['lux']}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        client.loop_stop()

if __name__ == "__main__":
    main()