import json
import time
import paho.mqtt.client as mqtt

BROKER = "broker.hivemq.com"
PORT = 1883
BASE_TOPIC = "helmguard/kelompok_03"

# Menyimpan state terakhir dari kedua Edge Device
state_a = {"impact": 0.0, "tilt_deg": 0.0, "gyro_rate": 0.0, "lux": 100.0, "timestamp": 0}
state_b = {"impact": 0.0, "gyro_rate": 0.0, "shake_ack": False, "timestamp": 0}

system_status = "NORMAL_CONVOY" # Pilihan: NORMAL_CONVOY, BUMPY_ROAD, SOS_CRASH_ACTIVE, RESCUE_DISPATCHED
last_alert_time = 0

def evaluate_decision_tree(client):
    """ALGORITMA AI / DECISION TREE & SENSOR FUSION ANTAR 2 EDGE DEVICE"""
    global system_status, last_alert_time
    now = time.time()

    # 1. Jika sedang status SOS_CRASH_ACTIVE, cek apakah HP B digoyangkan (Komunikasi Arah 2: Rescue ACK)
    if system_status == "SOS_CRASH_ACTIVE":
        if state_b["shake_ack"] and (now - last_alert_time > 3):
            system_status = "RESCUE_DISPATCHED"
            is_dark = state_a["lux"] < 50.0
            print(">>> [KEPUTUSAN AI] HP B merespons! Mengirim instruksi Senter SOS ke HP A...")
            
            # Kirim perintah balik ke HP A untuk nyalakan senter SOS
            client.publish(f"{BASE_TOPIC}/edge_a/command", json.dumps({
                "action": "RESCUE_ON_THE_WAY",
                "is_dark": is_dark
            }))
            # Beri tahu HP B bahwa konfirmasi terkirim
            client.publish(f"{BASE_TOPIC}/edge_b/command", json.dumps({
                "alert": "ACK_SENT",
                "message": "Sinyal penyelamatan terkirim ke HP A"
            }))
            last_alert_time = now
        return

    # Reset otomatis dari RESCUE_DISPATCHED ke NORMAL jika HP A sudah ditegakkan kembali (tilt < 35 derajat)
    if system_status == "RESCUE_DISPATCHED":
        if state_a["tilt_deg"] < 35.0 and state_a["impact"] < 1.5:
            system_status = "NORMAL_CONVOY"
            print(">>> [KEPUTUSAN AI] HP A sudah tegak & aman. Kembali ke NORMAL_CONVOY.")
        return

    # 2. Sensor Fusion: Cek apakah kedua HP terguncang bersamaan (Jalan Rusak / Polisi Tidur)
    a_disturbed = state_a["impact"] > 2.5 or state_a["gyro_rate"] > 1.5
    b_disturbed = state_b["impact"] > 2.5 or state_b["gyro_rate"] > 1.5

    if a_disturbed and b_disturbed:
        system_status = "BUMPY_ROAD"
        print(">>> [KEPUTUSAN AI] Kedua HP terguncang bersamaan -> BUMPY_ROAD (Abaikan Alarm)")

    # 3. Deteksi Kecelakaan / Darurat pada HP A (HP A miring ekstrem > 55 derajat ATAU guncangan keras sendirian)
    elif (state_a["tilt_deg"] > 55.0 or state_a["impact"] > 4.0) and not b_disturbed:
        if now - last_alert_time > 6:
            system_status = "SOS_CRASH_ACTIVE"
            last_alert_time = now
            is_dark = state_a["lux"] < 50.0
            kondisi_cahaya = "Malam/Gelap" if is_dark else "Siang/Terang"
            alert_code = "SOS_NIGHT_CRASH" if is_dark else "SOS_DAY_CRASH"

            print(f">>> [KEPUTUSAN AI] BAHAYA! {alert_code} Terdeteksi pada HP A!")

            # Kirim Peringatan ke HP B (Arah 1)
            client.publish(f"{BASE_TOPIC}/edge_b/command", json.dumps({
                "alert": alert_code,
                "message": f"Unit A terjatuh (Kemiringan {state_a['tilt_deg']} derajat, kondisi {kondisi_cahaya})!"
            }))
            # Beri tahu HP A bahwa sinyal SOS telah dipancarkan
            client.publish(f"{BASE_TOPIC}/edge_a/command", json.dumps({
                "action": "WARNING_CRASH_DETECTED"
            }))
    else:
        system_status = "NORMAL_CONVOY"

def publish_dashboard_state(client):
    """Kirim rangkuman data ke Dashboard Web secara real-time"""
    summary = {
        "system_status": system_status,
        "hp_a": state_a,
        "hp_b": state_b,
        "updated_at": time.strftime("%H:%M:%S")
    }
    client.publish(f"{BASE_TOPIC}/dashboard/state", json.dumps(summary))

def on_message(client, userdata, msg):
    global state_a, state_b, system_status
    payload = json.loads(msg.payload.decode())

    if msg.topic.endswith("edge_a/telemetry"):
        state_a = payload
    elif msg.topic.endswith("edge_b/telemetry"):
        state_b = payload
    elif msg.topic.endswith("dashboard/control"):
        # Jika tombol Rescue / Reset ditekan dari Dashboard Web
        cmd = payload.get("command")
        if cmd == "TRIGGER_RESCUE":
            state_b["shake_ack"] = True
        elif cmd == "RESET_SYSTEM":
            system_status = "NORMAL_CONVOY"
            print(">>> [DASHBOARD] Sistem di-reset ke NORMAL_CONVOY")

    evaluate_decision_tree(client)
    publish_dashboard_state(client)

def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.subscribe(f"{BASE_TOPIC}/edge_a/telemetry")
    client.subscribe(f"{BASE_TOPIC}/edge_b/telemetry")
    client.subscribe(f"{BASE_TOPIC}/dashboard/control")
    print("[SERVER AI AKTIF] Menghubungkan HP A, HP B, dan Dashboard...")
    client.loop_forever()

if __name__ == "__main__":
    main()