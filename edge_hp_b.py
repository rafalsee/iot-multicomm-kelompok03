import json
import math
import subprocess
import time
import paho.mqtt.client as mqtt

BROKER = "broker.hivemq.com"
PORT = 1883
TOPIC_TELEMETRY_B = "helmguard/kelompok_03/edge_b/telemetry"
TOPIC_COMMAND_B = "helmguard/kelompok_03/edge_b/command"

def get_sensor_names():
    out = subprocess.run(["termux-sensor", "-l"], capture_output=True, text=True).stdout
    sensors = json.loads(out).get("sensors", [])
    accel = next((s for s in sensors if "accelerometer" in s.lower() and "uncalibrated" not in s.lower()), "accelerometer")
    gyro = next((s for s in sensors if "gyroscope" in s.lower() and "uncalibrated" not in s.lower()), "gyroscope")
    return accel, gyro

def read_and_process(accel_name, gyro_name):
    out = subprocess.run(["termux-sensor", "-s", f"{accel_name},{gyro_name}", "-n", "1"], capture_output=True, text=True, timeout=8).stdout
    data = json.loads(out)
    ax, ay, az = data.get(accel_name, {}).get("values", [0.0, 0.0, 9.8])[:3]
    gx, gy, gz = data.get(gyro_name, {}).get("values", [0.0, 0.0, 0.0])[:3]

    # Data Processing: Impact & Shake Gesture Detector
    svm = math.sqrt(ax**2 + ay**2 + az**2)
    impact_score = round(abs(svm - 9.81), 2)
    gyro_rate = round(math.sqrt(gx**2 + gy**2 + gz**2), 2)

    return {
        "device": "HP_B",
        "impact": impact_score,
        "gyro_rate": gyro_rate,
        "shake_ack": impact_score > 3.5 or gyro_rate > 2.0, # True jika HP B digoyangkan untuk konfirmasi
        "timestamp": int(time.time())
    }

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    alert_type = payload.get("alert")
    message = payload.get("message", "")
    print(f"\n[ALERT DARI CLOUD] {alert_type}: {message}")

    if alert_type in ["SOS_NIGHT_CRASH", "SOS_DAY_CRASH"]:
        subprocess.run(["termux-vibrate", "-d", "2000"])
        subprocess.run(["termux-notification", "--title", "🚨 DARURAT SOS UNIT A!", "--content", message])
        subprocess.run(["termux-tts-speak", f"Peringatan darurat! {message}. Goyangkan H P ini untuk mengirim konfirmasi bantuan."])
    elif alert_type == "ACK_SENT":
        subprocess.run(["termux-vibrate", "-d", "300"])
        subprocess.run(["termux-tts-speak", "Sinyal bantuan berhasil dikirim ke Unit A."])

def main():
    accel_name, gyro_name = get_sensor_names()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.subscribe(TOPIC_COMMAND_B)
    client.loop_start()

    print("[HP B AKTIF] Memantau keselamatan Unit A...")
    try:
        while True:
            telemetry = read_and_process(accel_name, gyro_name)
            client.publish(TOPIC_TELEMETRY_B, json.dumps(telemetry))
            status_shake = "MENGGuncang/ACK!" if telemetry["shake_ack"] else "Stabil"
            print(f"[KIRIM] Impact B: {telemetry['impact']} m/s2 | Status: {status_shake}")
            time.sleep(1.5)
    except KeyboardInterrupt:
        client.loop_stop()

if __name__ == "__main__":
    main()