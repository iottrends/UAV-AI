import time
import threading
from types import SimpleNamespace

import pytest

from video_streamer import ll_streamer
import web_server

def test_ll_streamer_multi_client():
    """
    Test that LowLatencyStreamer correctly handles multiple clients.
    Each client should receive the init segment and subsequent atoms.
    """
    # 1. Setup mock data
    init_data = b"ftyp...moov..."
    atom1 = b"moof...mdat1"
    atom2 = b"moof...mdat2"

    # 2. Start streamer in a controlled way
    ll_streamer.stop()
    ll_streamer._running = True
    ll_streamer._init_segment = init_data
    ll_streamer.status = 'streaming'

    # 3. Simulate multiple clients connecting
    results = {}
    
    def run_client(client_id):
        gen = ll_streamer.generate_fmp4()
        received = []
        try:
            # We expect exactly 3 chunks (init + 2 atoms)
            received.append(next(gen))
            received.append(next(gen))
            received.append(next(gen))
        except StopIteration:
            pass
        results[client_id] = received

    threads = []
    for i in range(3):
        t = threading.Thread(target=run_client, args=(i,))
        t.start()
        threads.append(t)

    # Give threads time to register their queues
    time.sleep(0.2)

    # 4. Broadcast atoms
    with ll_streamer._lock:
        for q in ll_streamer._clients:
            q.put(atom1)
            q.put(atom2)

    # 5. Wait for threads to finish
    for t in threads:
        t.join(timeout=2.0)

    # 6. Verify results
    for i in range(3):
        assert i in results, f"Client {i} did not receive any data"
        assert len(results[i]) == 3
        assert results[i][0] == init_data
        assert results[i][1] == atom1
        assert results[i][2] == atom2

    ll_streamer.stop()

def test_ll_streamer_max_clients():
    """Verify that ll_streamer respects _MAX_CLIENTS."""
    ll_streamer.stop()
    ll_streamer._running = True
    ll_streamer._MAX_CLIENTS = 2
    ll_streamer._init_segment = b"init"
    
    # 1. Connect first 2 clients
    gen1 = ll_streamer.generate_fmp4()
    next(gen1) # Receives init
    
    gen2 = ll_streamer.generate_fmp4()
    next(gen2) # Receives init
    
    # 2. 3rd client should be rejected (returns early)
    gen3 = ll_streamer.generate_fmp4()
    with pytest.raises(StopIteration):
        next(gen3)
        
    ll_streamer.stop()
    ll_streamer._MAX_CLIENTS = 8 # Reset

def test_video_source_auto_routing(client):
    """Test that web server routes UDP to ll_streamer and USB/RTSP to video_streamer."""
    # 1. Test UDP routing
    resp = client.post("/api/video_source", 
                      json={"source": "udp://0.0.0.0:5600"})
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "ll"
    assert ll_streamer.source == "udp://0.0.0.0:5600"
    
    # 2. Test USB routing
    resp = client.post("/api/video_source", 
                      json={"source": "0"})
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "mjpeg"
    assert ll_streamer.status == "idle" # LL should be stopped
    
    # 3. Test stop
    resp = client.post("/api/video_source", 
                      json={"source": ""})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "stopped"
    assert ll_streamer.status == "idle"


def test_video_ll_stream_headers(client, monkeypatch):
    def _gen():
        yield b"ftyp"

    monkeypatch.setattr(web_server, "ll_streamer", SimpleNamespace(generate_fmp4=_gen))
    resp = client.get("/api/video_ll_stream")
    assert resp.status_code == 200
    assert resp.mimetype == "video/mp4"
    assert resp.headers.get("Cache-Control") == "no-cache, no-store, must-revalidate"
    assert resp.headers.get("X-Accel-Buffering") == "no"
    chunks = list(resp.response)
    assert chunks and chunks[0].startswith(b"ftyp")
