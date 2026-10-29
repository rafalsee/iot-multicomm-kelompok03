import json
import sys
import time
import paho.mqtt.client as mqtt

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ==========================================
# KONFIGURASI MQTT BROKER & TOPIC
# ==========================================
# Gunakan broker.emqx.io port 1883 (TCP publik tanpa SSL)
# Sesuai dengan edge_hp_a dan edge_hp_b agar berada di broker yang sama!
BROKER = "broker.emqx.io"
PORT = 1883

BASE_TOPIC = "helmguard/kelompok03"
ALT_BASE_TOPIC = "helmguard/kelompok_03"

# State terakhir kedua Edge Device
state_a = {
    "device": "HP_A",
    "impact": 0.0,
    "tilt_deg": 0.0,
    "gyro_rate": 0.0,
    "lux": 100.0,
    "timestamp": time.time()
}
state_b = {
    "device": "HP_B",
    "impact": 0.0,
    "gyro_rate": 0.0,
    "shake_ack": False,
    "timestamp": time.time()
}

# Status Sistem:
# NORMAL_CONVOY    : Kondisi normal perjalanan
# BUMPY_ROAD       : Jalan bergelombang/polisi tidur (Sensor Fusion: HP A & B terguncang bersama)
# SOS_CRASH_ACTIVE : HP A mengalami benturan/kemiringan ekstrem sendirian (Kecelakaan)
# RESCUE_DISPATCHED: HP B membalas dengan gesture shake (Bantuan dalam perjalanan)
system_status = "NORMAL_CONVOY"
last_alert_time = 0
last_disturbed_a = 0
last_disturbed_b = 0
bumpy_until = 0

def publish_command(client, topic_suffix, payload_dict):
    """Kirim perintah ke kedua variasi topic (kelompok03 & kelompok_03) untuk kompatibilitas penuh"""
    msg = json.dumps(payload_dict)
    client.publish(f"{BASE_TOPIC}/{topic_suffix}", msg, qos=1)
    client.publish(f"{ALT_BASE_TOPIC}/{topic_suffix}", msg, qos=1)

def evaluate_decision_tree(client):
    """
    ALGORITMA AI / DECISION TREE & SENSOR FUSION ANTAR 2 EDGE DEVICE
    1. Filter False Alarm (Jalan Berlubang / Polisi Tidur via Sensor Fusion)
    2. Deteksi Crash Solo HP A (Miring > 55 deg atau Benturan Keras)
    3. Komunikasi 2 Arah: Menunggu Shake ACK dari HP B untuk dispatching rescue
    4. Auto-Recovery jika HP A ditegakkan kembali
    """
    global system_status, last_alert_time, last_disturbed_a, last_disturbed_b, bumpy_until
    now = time.time()

    # 1. JIKA STATUS RESCUE_DISPATCHED:
    if system_status == "RESCUE_DISPATCHED":
        # Reset otomatis ke NORMAL jika HP A sudah ditegakkan kembali (tilt < 35 derajat)
        if state_a["tilt_deg"] < 35.0 and state_a["impact"] < 1.5:
            system_status = "NORMAL_CONVOY"
            print(">>> [KEPUTUSAN AI] HP A sudah ditegakkan & aman. Status kembali ke NORMAL_CONVOY.")
        return

    # 2. JIKA STATUS SOS_CRASH_ACTIVE: Cek apakah HP B merespons (Komunikasi Arah 2)
    if system_status == "SOS_CRASH_ACTIVE":
        # Auto-cancel jika HP A langsung berdiri sendiri tanpa bantuan
        if (now - last_alert_time > 8) and (state_a["tilt_deg"] < 35.0 and state_a["impact"] < 1.2):
            system_status = "NORMAL_CONVOY"
            print(">>> [KEPUTUSAN AI] HP A pulih mandiri. Status kembali ke NORMAL_CONVOY.")
            return

        if state_b["shake_ack"]:
            system_status = "RESCUE_DISPATCHED"
            is_dark = state_a["lux"] < 50.0
            kondisi = "Malam/Gelap (Senter SOS Aktif)" if is_dark else "Siang/Terang"
            print(f">>> [KEPUTUSAN AI] 🚑 HP B merespons ({kondisi})! Mengirim instruksi RESCUE ke HP A...")

            # Kirim perintah konfirmasi penyelamatan ke HP A
            publish_command(client, "edge_a/command", {
                "action": "RESCUE_ON_THE_WAY",
                "is_dark": is_dark
            })
            # Beri tahu HP B bahwa konfirmasinya berhasil diteruskan
            publish_command(client, "edge_b/command", {
                "alert": "ACK_SENT",
                "message": "Sinyal penyelamatan terkirim ke HP A"
            })
            last_alert_time = now
            state_b["shake_ack"] = False
        return

    # Periksa getaran / gangguan pada masing-masing HP
    if state_a["impact"] > 2.5 or state_a["gyro_rate"] > 1.8:
        last_disturbed_a = now
    if state_b["impact"] > 2.5 or state_b["gyro_rate"] > 1.8:
        last_disturbed_b = now

    # 3. SENSOR FUSION: Cek apakah kedua HP terguncang bersamaan dalam rentang 1.5 detik
    both_disturbed = abs(last_disturbed_a - last_disturbed_b) < 1.5 and (now - last_disturbed_a < 2.0)
    if both_disturbed:
        bumpy_until = now + 3.0
        if system_status != "BUMPY_ROAD":
            system_status = "BUMPY_ROAD"
            print(">>> [SENSOR FUSION] Kedua HP terguncang bersamaan -> BUMPY_ROAD (Polisi Tidur/Jalan Rusak - Alarm Diabaikan)")
        return

    # Jika sedang dalam durasi penahanan BUMPY_ROAD, jangan picu alarm kecelakaan
    if now < bumpy_until:
        system_status = "BUMPY_ROAD"
        return

    # 4. DETEKSI KECELAKAAN HP A (HP A miring ekstrem > 55° ATAU benturan keras sendirian)
    a_crashed = (state_a["tilt_deg"] > 55.0 or state_a["impact"] > 4.0)
    if a_crashed:
        if now - last_alert_time > 5:
            system_status = "SOS_CRASH_ACTIVE"
            last_alert_time = now
            is_dark = state_a["lux"] < 50.0
            kondisi_cahaya = "Malam/Gelap" if is_dark else "Siang/Terang"
            alert_code = "SOS_NIGHT_CRASH" if is_dark else "SOS_DAY_CRASH"

            print(f"\n🚨 [KEPUTUSAN AI] DARURAT! {alert_code} terdeteksi pada HP A!")
            print(f"    Detail: Tilt = {state_a['tilt_deg']}°, Impact = {state_a['impact']} m/s², Lux = {state_a['lux']}")

            # Kirim peringatan darurat ke HP B (Arah 1)
            publish_command(client, "edge_b/command", {
                "alert": alert_code,
                "message": f"Unit A terjatuh (Kemiringan {state_a['tilt_deg']}°, {kondisi_cahaya})!"
            })
            # Beri tahu HP A bahwa sinyal SOS sudah dikirim ke Cloud
            publish_command(client, "edge_a/command", {
                "action": "WARNING_CRASH_DETECTED"
            })
    else:
        system_status = "NORMAL_CONVOY"

def publish_dashboard_state(client):
    """Kirim rangkuman status sistem ke Dashboard Web secara real-time"""
    summary = {
        "system_status": system_status,
        "hp_a": state_a,
        "hp_b": state_b,
        "updated_at": time.strftime("%H:%M:%S")
    }
    publish_command(client, "dashboard/state", summary)

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"✅ [SERVER AI] Terhubung ke Broker {BROKER}:{PORT}")
        # Subscribe ke kedua kemungkinan nama topik
        topics = [
            ("helmguard/kelompok03/edge_a/telemetry", 1),
            ("helmguard/kelompok03/edge_b/telemetry", 1),
            ("helmguard/kelompok03/dashboard/control", 1),
            ("helmguard/kelompok_03/edge_a/telemetry", 1),
            ("helmguard/kelompok_03/edge_b/telemetry", 1),
            ("helmguard/kelompok_03/dashboard/control", 1),
        ]
        client.subscribe(topics)
        print("   Mendengarkan telemetri HP A, HP B, dan kontrol Dashboard...")
    else:
        print(f"❌ [SERVER AI] Gagal terhubung, rc: {rc}")

def on_message(client, userdata, msg):
    global state_a, state_b, system_status
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return

    topic = msg.topic
    if topic.endswith("edge_a/telemetry"):
        state_a = payload
        state_a["timestamp"] = time.time()
    elif topic.endswith("edge_b/telemetry"):
        state_b = payload
        state_b["timestamp"] = time.time()
    elif topic.endswith("dashboard/control"):
        cmd = payload.get("command")
        print(f">>> [DASHBOARD CONTROL] Menerima perintah: {cmd}")
        if cmd == "TRIGGER_RESCUE":
            state_b["shake_ack"] = True
        elif cmd == "RESET_SYSTEM":
            system_status = "NORMAL_CONVOY"
            state_b["shake_ack"] = False
            print(">>> [DASHBOARD CONTROL] Sistem di-reset ke NORMAL_CONVOY")

    evaluate_decision_tree(client)
    publish_dashboard_state(client)

def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    
    print(f"Connecting to MQTT Broker {BROKER}:{PORT}...")
    try:
        client.connect(BROKER, PORT, 60)
    except Exception as e:
        print(f"❌ Koneksi ke {BROKER} gagal: {e}")
        return

    client.loop_forever()

if __name__ == "__main__":
    main()
