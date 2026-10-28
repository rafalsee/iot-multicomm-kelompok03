import json
import math
import subprocess
import time
import paho.mqtt.client as mqtt

BROKER = "broker.hivemq.com"
PORT = 1883
TOPIC_TELEMETRY_A = "helmguard/kelompok_03/edge_a/telemetry"
TOPIC_COMMAND_A = "helmguard/kelompok_03/edge_a/command"

# Variabel Global
lux_history = []
rescue_mode = False

def get_sensor_names():
    """Mencari nama sensor otomatis di HP Android"""
    out = subprocess.run(["termux-sensor", "-l"], capture_output=True, text=True).stdout
    sensors = json.loads(out).get("sensors", [])
    accel = next((s for s in sensors if "accelerometer" in s.lower() and "uncalibrated" not in s.lower()), "accelerometer")
    gyro = next((s for s in sensors if "gyroscope" in s.lower() and "uncalibrated" not in s.lower()), "gyroscope")
    light = next((s for s in sensors if "light" in s.lower()), "light")
    return accel, gyro, light

def read_and_process(accel_name, gyro_name, light_name):
    """Membaca sensor & melakukan 3 Data Processing di Edge A"""
    sensor_query = f"{accel_name},{gyro_name},{light_name}"
    out = subprocess.run(["termux-sensor", "-s", sensor_query, "-n", "1"], capture_output=True, text=True, timeout=8).stdout
    data = json.loads(out)

    ax, ay, az = data.get(accel_name, {}).get("values", [0.0, 0.0, 9.8])[:3]
    gx, gy, gz = data.get(gyro_name, {}).get("values", [0.0, 0.0, 0.0])[:3]
    raw_lux = data.get(light_name, {}).get("values", [100.0])[0]

    # DATA PROCESSING 1: Dynamic Impact G-Force (dikurangi gravitasi 9.81 m/s^2)
    svm = math.sqrt(ax**2 + ay**2 + az**2)
    impact_score = round(abs(svm - 9.81), 2)

    # DATA PROCESSING 2: Kemiringan Ekstrem (Tilt Angle dalam Derajat) & Kecepatan Putar
    tilt_angle = round(math.degrees(math.acos(max(-1.0, min(1.0, az / max(svm, 0.01))))), 1)
    gyro_rate = round(math.sqrt(gx**2 + gy**2 + gz**2), 2)

    # DATA PROCESSING 3: Moving Average Filter untuk Sensor Cahaya (Lux)
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

def play_sos_morse_and_voice(is_dark):
    """AKSI KOMUNIKASI ARAH 2: HP A menyalakan Senter Morse SOS & Bicara saat ditolong"""
    subprocess.run(["termux-vibrate", "-d", "1000"])
    subprocess.run(["termux-notification", "--title", "🚑 BANTUAN DATANG!", "--content", "Unit B sedang menuju lokasimu!"])
    subprocess.run(["termux-tts-speak", "Konfirmasi diterima. Bantuan dari Unit B sedang menuju lokasi Anda."])
    
    # Jika kondisi gelap (malam/terowongan), nyalakan Senter pola SOS (... --- ...)
    if is_dark:
        print("[AKTUATOR] Kondisi Gelap! Menyalakan Senter Sandi Morse SOS...")
        morse_sos = [0.2, 0.2, 0.2, 0.6, 0.6, 0.6, 0.2, 0.2, 0.2]
        for durasi in morse_sos:
            subprocess.run(["termux-torch", "on"])
            time.sleep(durasi)
            subprocess.run(["termux-torch", "off"])
            time.sleep(0.2)

def on_message(client, userdata, msg):
    """Menerima pesan balikan dari Server / HP B (Komunikasi Arah ke-2)"""
    payload = json.loads(msg.payload.decode())
    action = payload.get("action")
    print(f"\n[MASUK DARI CLOUD] Perintah diterima: {action}")
    
    if action == "RESCUE_ON_THE_WAY":
        is_dark = payload.get("is_dark", True)
        play_sos_morse_and_voice(is_dark)
    elif action == "WARNING_CRASH_DETECTED":
        subprocess.run(["termux-vibrate", "-d", "500"])
        subprocess.run(["termux-tts-speak", "Terdeteksi benturan. Sinyal S O S telah dikirim ke Unit B."])

def main():
    print("Mendeteksi sensor HP A...")
    accel_name, gyro_name, light_name = get_sensor_names()
    print(f"Sensor Aktif: {accel_name} | {gyro_name} | {light_name}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.subscribe(TOPIC_COMMAND_A)
    client.loop_start()

    print("[HP A AKTIF] Mengirim telemetri & siap menerima sinyal penyelamatan...")
    try:
        while True:
            telemetry = read_and_process(accel_name, gyro_name, light_name)
            client.publish(TOPIC_TELEMETRY_A, json.dumps(telemetry))
            print(f"[KIRIM] Impact: {telemetry['impact']} m/s2 | Tilt: {telemetry['tilt_deg']}° | Cahaya: {telemetry['lux']} lux")
            time.sleep(1.5)
    except KeyboardInterrupt:
        print("Berhenti.")
        client.loop_stop()

if __name__ == "__main__":
    main()