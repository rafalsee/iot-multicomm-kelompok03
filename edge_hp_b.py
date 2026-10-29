import json
import math
import subprocess
import time
import paho.mqtt.client as mqtt

TOPIC_TELEMETRY_B = "helmguard/kelompok03/edge_b/telemetry"
TOPIC_COMMAND_B = "helmguard/kelompok03/edge_b/command"
TOPIC_COMMAND_A = "helmguard/kelompok03/edge_a/command"

def get_sensor_names():
    out = subprocess.run(["termux-sensor", "-l"], capture_output=True, text=True).stdout
    sensors = json.loads(out).get("sensors", [])
    accel = next((s for s in sensors if any(k in s.lower() for k in ["accelerometer", "accel", "gravity"]) and "uncalibrated" not in s.lower()), None)
    gyro = next((s for s in sensors if any(k in s.lower() for k in ["gyroscope", "gyro"]) and "uncalibrated" not in s.lower()), None)
    return accel, gyro

def read_and_process(accel_name, gyro_name):
    active_list = [s for s in (accel_name, gyro_name) if s is not None]
    if not active_list:
        return {"device": "HP_B", "impact": 0.0, "gyro_rate": 0.0, "shake_ack": False, "timestamp": int(time.time())}

    out = subprocess.run(["termux-sensor", "-s", ",".join(active_list), "-n", "3"], capture_output=True, text=True, timeout=8).stdout
    
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
                ax, ay, az = data[accel_name].get("values", [0.0, 0.0, 9.8])[:3]
                svm = math.sqrt(ax**2 + ay**2 + az**2)
                imp = abs(svm - 9.81)
                if imp > max_impact:
                    max_impact = imp

            if gyro_name and gyro_name in data:
                gx, gy, gz = data[gyro_name].get("values", [0.0, 0.0, 0.0])[:3]
                gr = math.sqrt(gx**2 + gy**2 + gz**2)
                if gr > max_gyro:
                    max_gyro = gr
        except Exception:
            break

    impact_score = round(max_impact, 2)
    gyro_rate = round(max_gyro if gyro_name else (impact_score / 1.5), 2)

    is_shaken = (impact_score >= 0.8) or (gyro_rate >= 1.0)

    return {
        "device": "HP_B",
        "impact": impact_score,
        "gyro_rate": gyro_rate,
        "shake_ack": is_shaken,
        "timestamp": int(time.time())
    }

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("✅ [HP B TERHUBUNG KE CLOUD] Siap mendeteksi guncangan!")
        client.subscribe(TOPIC_COMMAND_B)

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    alert_type = payload.get("alert")
    message = payload.get("message", "")
    print(f"\n🚨 [SINYAL MASUK DARI CLOUD] {alert_type}: {message}")

    if alert_type in ["SOS_NIGHT_CRASH", "SOS_DAY_CRASH", "SHAKE_SOS_A"]:
        subprocess.run(["termux-vibrate", "-d", "2000"])
        subprocess.run(["termux-notification", "--title", "🚨 SINYAL DARURAT HP A!", "--content", message])
        subprocess.run(["termux-tts-speak", f"Peringatan! {message}. Goyangkan H P ini untuk membalas sinyal."])
    elif alert_type == "ACK_SENT":
        subprocess.run(["termux-vibrate", "-d", "400"])
        subprocess.run(["termux-tts-speak", "Sinyal balasan terkirim ke H P A."])

def connect_resilient():
    jalur_koneksi = [
        ("broker.emqx.io", 1883, "tcp", None),
        ("broker.emqx.io", 8083, "websockets", "/mqtt"),
        ("broker.hivemq.com", 8000, "websockets", "/mqtt"),
    ]
    for host, port, transport, path in jalur_koneksi:
        try:
            try:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport=transport)
            except AttributeError:
                c = mqtt.Client(transport=transport)
            if path:
                c.ws_set_options(path=path)
            c.on_connect = on_connect
            c.on_message = on_message
            c.connect(host, port, 60)
            return c
        except Exception:
            continue
    raise RuntimeError("Gagal konek ke broker.")

def main():
    accel_name, gyro_name = get_sensor_names()
    print(f"Sensor HP B: {accel_name} | {gyro_name or 'Estimasi Accel'}")
    client = connect_resilient()
    client.loop_start()

    try:
        while True:
            telemetry = read_and_process(accel_name, gyro_name)
            client.publish(TOPIC_TELEMETRY_B, json.dumps(telemetry), qos=1)
            
            if telemetry["shake_ack"]:
                print(f"👋 [GUNCANGAN TERDETEKSI: {telemetry['impact']} m/s2] -> LANGSUNG MENGIRIM SINYAL KE HP A!")
                client.publish(TOPIC_COMMAND_A, json.dumps({
                    "action": "RESCUE_ON_THE_WAY",
                    "is_dark": True
                }), qos=1)
            else:
                print(f"[STANDBY] Impact B: {telemetry['impact']} m/s2 (Guncang HP untuk kirim sinyal)")
            time.sleep(0.5)
    except KeyboardInterrupt:
        client.loop_stop()

if __name__ == "__main__":
    main()