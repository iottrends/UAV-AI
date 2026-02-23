import time
import threading
from pymavlink import mavutil

class MavlinkDroneSim:
    def __init__(self, port=14550):
        self.port = port
        self.running = False
        self.thread = None
        self.conn = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)

    def _run(self):
        # Create a UDP server socket (drone side)
        self.conn = mavutil.mavlink_connection(f'udpout:127.0.0.1:{self.port}', source_system=1)
        
        while self.running:
            # Send Heartbeat
            self.conn.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_QUADROTOR,
                mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED,
                0,
                mavutil.mavlink.MAV_STATE_ACTIVE
            )
            
            # Send SYS_STATUS
            self.conn.mav.sys_status_send(
                0, 0, 0, 500, 12000, 500, 85, 0, 0, 0, 0, 0, 0
            )
            
            # Send Attitude
            self.conn.mav.attitude_send(
                int(time.time()*1000), 0.1, 0.2, 0.3, 0.01, 0.02, 0.03
            )
            
            # Send VFR_HUD
            self.conn.mav.vfr_hud_send(10.5, 12.0, 180, 50, 15.0, 0.5)
            
            time.sleep(1) # 1Hz heartbeat

if __name__ == "__main__":
    sim = MavlinkDroneSim()
    sim.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sim.stop()
