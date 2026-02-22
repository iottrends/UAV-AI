import time
import threading
import random
from Mavlink_rx_handler import MavlinkHandler

def test_telemetry_loop_stress():
    handler = MavlinkHandler()
    handler.is_connected = True
    
    # 1. Start a "flooder" thread that hammers the rx queue
    # This simulates a high-rate serial stream (e.g. 200Hz)
    stop_event = threading.Event()
    
    def flooder():
        msg_types = ["ATTITUDE", "VFR_HUD", "SYS_STATUS", "RC_CHANNELS", "GPS_RAW_INT", "STATUSTEXT"]
        while not stop_event.is_set():
            msg = {
                "mavpackettype": random.choice(msg_types),
                "roll": random.random(),
                "pitch": random.random(),
                "yaw": random.random(),
                "alt": random.random() * 100,
                "voltage_battery": 12000,
                "text": "Stress test message"
            }
            with handler._rx_mav_lock:
                handler.rx_mav_msg.append(msg)
            time.sleep(0.005) # 200Hz
            
    flood_thread = threading.Thread(target=flooder, daemon=True)
    flood_thread.start()
    
    # 2. Run snapshots at a high rate (simulating the fast telemetry loop)
    # We want to check for race conditions during list(self.rx_mav_msg)
    start_time = time.time()
    iterations = 0
    errors = []
    
    try:
        while time.time() - start_time < 5: # Run for 5 seconds
            try:
                handler.snapshot_rx_queue()
                # Verify we got data
                ctx = handler.ai_mavlink_ctx
                assert len(ctx) > 0
                
                # Periodically simulate the slow loop update
                if iterations % 10 == 0:
                    _ = ctx.copy()
                    
                iterations += 1
                time.sleep(0.01) # 100Hz snapshot (faster than real fast loop)
            except Exception as e:
                errors.append(str(e))
                break
    finally:
        stop_event.set()
        flood_thread.join()
        
    assert len(errors) == 0, f"Concurrency errors detected: {errors}"
    print(f"Stress test passed with {iterations} successful snapshots under 200Hz load")

if __name__ == "__main__":
    test_telemetry_loop_stress()
