import pytest
import time
import web_server
from drone_validator import DroneValidator
from tests.integration.mavlink_drone_sim import MavlinkDroneSim

def test_udp_connection_to_sim_drone(client, monkeypatch):
    # Use real validator logic for integration
    real_validator = DroneValidator()
    monkeypatch.setattr(web_server, "validator", real_validator)
    
    # Start the mock drone
    sim = MavlinkDroneSim(port=14551)
    sim.start()
    time.sleep(1) # wait for sim to bind
    
    try:
        # Request connection via API
        payload = {
            "type": "udp",
            "ip": "127.0.0.1",
            "port": 14551
        }
        resp = client.post("/api/connect", json=payload)
        assert resp.status_code == 200
        
        # Wait for backend to process heartbeat
        # Sim sends at 1Hz, give it 3s to receive and validate hardware
        start_wait = time.time()
        while time.time() - start_wait < 5:
            if real_validator.is_connected:
                break
            time.sleep(0.5)
            
        assert real_validator.is_connected is True
        
        # Manually trigger snapshot and health update to verify data flow
        real_validator.snapshot_rx_queue()
        web_server.mavlink_buffer = real_validator.ai_mavlink_ctx.copy()
        web_server.update_system_health()
        
        health = web_server.last_system_health
        # Our sim sends 12000mV -> 12.0V
        assert health["battery"]["voltage"] == 12.0
        
    finally:
        real_validator.disconnect()
        sim.stop()
