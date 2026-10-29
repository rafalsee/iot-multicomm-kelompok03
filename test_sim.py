import json
import random
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
BASE = "helmguard/kelompok03"
ALT_BASE = "helmguard/kelompok_03"

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def on_connect(c, userdata, flags, rc, props=None):
    if rc == 0:
        print(f"✅ [SIMULATOR] Terhubung ke {BROKER}:{PORT}")
        # Subscribe ke perintah untuk memverifikasi respon sistem
        c.subscribe([
            (f"{BASE}/edge_a/command", 1),
            (f"{BASE}/edge_b/command", 1),
            (f"{BASE}/dashboard/state", 1),
        ])
    else:
        print(f"❌ [SIMULATOR] Gagal konek, rc: {rc}")

def on_message(c, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        topic = msg.topic
        if "edge_a/command" in topic:
            print(f"\n   📥 [DITERIMA HP A] Perintah Masuk: {data}")
        elif "edge_b/command" in topic:
            print(f"\n   📥 [DITERIMA HP B] Peringatan Masuk: {data}")
        elif "dashboard/state" in topic:
            status = data.get("system_status")
            print(f"   📊 [STATUS SERVER AI]: {status}", end="\r")
    except Exception:
        pass

client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, PORT, 60)
client.loop_start()

def pub_both(topic_suffix, payload):
    msg = json.dumps(payload)
    client.publish(f"{BASE}/{topic_suffix}", msg, qos=1)
    client.publish(f"{ALT_BASE}/{topic_suffix}", msg, qos=1)

def simulate_normal():
    """Simulasi konvoi normal (kondisi jalan aman)"""
    pub_both("edge_a/telemetry", {
        "device": "HP_A", "impact": round(random.uniform(0.1, 0.4), 2),
        "tilt_deg": round(random.uniform(1.0, 8.0), 1), "gyro_rate": 0.1,
        "lux": 110.0, "timestamp": int(time.time())
    })
    pub_both("edge_b/telemetry", {
        "device": "HP_B", "impact": round(random.uniform(0.1, 0.4), 2),
        "gyro_rate": 0.1, "shake_ack": False, "timestamp": int(time.time())
    })

def simulate_bumpy():
    """Simulasi Polisi Tidur / Jalan Rusak (Sensor Fusion: Keduanya terguncang)"""
    print("\n🛣️ [SKENARIO] Memancarkan Polisi Tidur / Bumpy Road (Kedua HP terguncang)...")
    now = int(time.time())
    pub_both("edge_a/telemetry", {
        "device": "HP_A", "impact": 3.8, "tilt_deg": 12.0, "gyro_rate": 2.2, "lux": 90.0, "timestamp": now
    })
    pub_both("edge_b/telemetry", {
        "device": "HP_B", "impact": 3.5, "gyro_rate": 2.0, "shake_ack": False, "timestamp": now
    })

def simulate_crash_night():
    """Simulasi HP A terjatuh di malam hari (Lux rendah -> Senter SOS)"""
    print("\n🚨 [SKENARIO] Memancarkan Crash Solo HP A di Malam Hari (Miring 70°, Gelap)...")
    pub_both("edge_a/telemetry", {
        "device": "HP_A", "impact": 5.2, "tilt_deg": 70.0, "gyro_rate": 3.5, "lux": 15.0, "timestamp": int(time.time())
    })

def simulate_crash_day():
    """Simulasi HP A terjatuh di siang hari (Lux terang)"""
    print("\n🚨 [SKENARIO] Memancarkan Crash Solo HP A di Siang Hari (Miring 65°, Terang)...")
    pub_both("edge_a/telemetry", {
        "device": "HP_A", "impact": 4.8, "tilt_deg": 65.0, "gyro_rate": 2.8, "lux": 250.0, "timestamp": int(time.time())
    })

def simulate_shake_rescue():
    """Simulasi HP B membalas dengan gesture goyangan (Shake ACK)"""
    print("\n🚑 [SKENARIO] Memancarkan Gesture Shake ACK dari HP B (Bantuan Menuju Lokasi)...")
    pub_both("edge_b/telemetry", {
        "device": "HP_B", "impact": 5.5, "gyro_rate": 4.0, "shake_ack": True, "timestamp": int(time.time())
    })

def simulate_reset():
    """Simulasi HP A ditegakkan kembali (Kembali normal)"""
    print("\n🔄 [SKENARIO] Memancarkan HP A Ditegakkan Kembali...")
    pub_both("edge_a/telemetry", {
        "device": "HP_A", "impact": 0.1, "tilt_deg": 5.0, "gyro_rate": 0.05, "lux": 100.0, "timestamp": int(time.time())
    })
    pub_both("edge_b/telemetry", {
        "device": "HP_B", "impact": 0.1, "gyro_rate": 0.05, "shake_ack": False, "timestamp": int(time.time())
    })
    pub_both("dashboard/control", {"command": "RESET_SYSTEM"})

def menu():
    time.sleep(1.0)
    print("\n" + "="*50)
    print("      HELMGUARD SOS - INTERACTIVE SIMULATOR      ")
    print("="*50)
    print("Pilih skenario untuk pengujian IoT & AI:")
    print(" [1] Normal Convoy (Perjalanan Lancar)")
    print(" [2] Bumpy Road / Polisi Tidur (Sensor Fusion)")
    print(" [3] Crash HP A Malam Hari (SOS Gelap + Senter)")
    print(" [4] Crash HP A Siang Hari (SOS Terang)")
    print(" [5] HP B Shake Balasan (Rescue Dispatched)")
    print(" [6] Reset Sistem ke Normal")
    print(" [0] Keluar")
    print("="*50)

    # Background thread untuk mengirim data normal berkala jika tidak ada aksi
    stop_flag = threading.Event()
    def background_heartbeat():
        while not stop_flag.is_set():
            simulate_normal()
            time.sleep(2.0)
    t = threading.Thread(target=background_heartbeat, daemon=True)
    t.start()

    try:
        while True:
            choice = input("\nMasukkan pilihan [1-6, 0=keluar]: ").strip()
            if choice == "1":
                print(">> Mengirim data Normal Convoy...")
                simulate_normal()
            elif choice == "2":
                simulate_bumpy()
            elif choice == "3":
                simulate_crash_night()
            elif choice == "4":
                simulate_crash_day()
            elif choice == "5":
                simulate_shake_rescue()
            elif choice == "6":
                simulate_reset()
            elif choice == "0":
                break
            else:
                print("Pilihan tidak valid.")
            time.sleep(1.0)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop_flag.set()
        client.loop_stop()
        client.disconnect()
        print("\nSimulator selesai.")

if __name__ == "__main__":
    menu()
