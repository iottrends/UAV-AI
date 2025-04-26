# Implementation Plan: Latency and Parameter Download Progress

## Overview

This document outlines the implementation plan for adding latency measurement and parameter download progress indicators to the UAV-AI Assistant interface header.

## 1. Modify Mavlink_rx_handler.py

Add parameter download progress tracking:

```python
# In Mavlink_rx_handler.py
class MavlinkHandler:
    def __init__(self):
        # Existing code...
        self.param_progress = 0  # Add this line to track parameter download progress
        
    def _process_parameter(self, msg):
        """Process parameter messages."""
        param_id = msg.param_id
        param_value = msg.param_value
        self.params_dict[param_id] = param_value

        # Track parameter download progress
        self.param_count = msg.param_count
        param_index = msg.param_index

        # Show progress periodically and update progress variable
        if param_index % 50 == 0 or param_index == self.param_count - 1:
            self.param_progress = (param_index + 1) / self.param_count * 100
            mavlink_logger.info(f"⏳ Parameter download: {self.param_progress:.1f}% ({param_index + 1}/{self.param_count})")
```

## 2. Update web_server.py

Add WebSocket ping/pong for latency measurement and include parameter progress in system status:

```python
# In web_server.py

# Add to global variables
last_system_health = {
    # Existing fields...
    "params": {
        "percentage": 0,
        "downloaded": 0,
        "total": 0
    },
    "latency": 0
}

# Add WebSocket event handler for ping
@socketio.on('ping')
def handle_ping():
    """Handle ping from client for latency measurement"""
    client_id = request.sid
    # Send pong back immediately
    emit('pong', {}, room=client_id)

# Update system health function to include parameter progress
def update_system_health():
    """Update system health information from validator data"""
    global last_system_health
    
    # Existing code...
    
    try:
        # Existing code...
        
        # Add parameter progress to system health
        if validator and hasattr(validator, 'param_progress'):
            param_percentage = validator.param_progress
            param_count = validator.param_count
            param_downloaded = int(param_count * (param_percentage / 100)) if param_count > 0 else 0
            
            last_system_health["params"] = {
                "percentage": param_percentage,
                "downloaded": param_downloaded,
                "total": param_count
            }
        
        # Broadcast system health to all connected clients
        if connected_clients:
            socketio.emit('system_status', last_system_health)
            
    except Exception as e:
        logger.error(f"Error updating system health: {str(e)}")
```

## 3. Update index.html

Add latency and parameter progress display to the header and implement latency measurement:

```html
<!-- In index.html header section -->
<header>
    <div class="logo">UAV-AI Assistant</div>
    <div class="connection-status">
        <span id="connectionText">Disconnected</span>
        <div class="status-indicator" id="connectionIndicator"></div>
        <span id="headerParamsText" style="margin-left: 15px; display: none;">Params: <span id="headerParamsValue">0%</span></span>
        <span id="headerLatencyText" style="margin-left: 15px; display: none;">Latency: <span id="headerLatencyValue">0ms</span></span>
    </div>
</header>
```

```javascript
// Add to the existing JavaScript
let pingInterval = null;
let lastPingTime = 0;

// Function to measure latency
function startLatencyMeasurement() {
    if (pingInterval) {
        clearInterval(pingInterval);
    }
    
    pingInterval = setInterval(() => {
        if (socket && isConnected) {
            lastPingTime = Date.now();
            socket.emit('ping');
        }
    }, 2000); // Ping every 2 seconds
}

// Handle pong response
socket.on('pong', function() {
    const latency = Date.now() - lastPingTime;
    if (headerLatencyValue) {
        headerLatencyValue.textContent = `${latency}ms`;
    }
    
    // Also update the system status data
    if (socket) {
        socket.emit('update_latency', { latency: latency });
    }
});

// Update connection status function to start/stop latency measurement
function updateConnectionStatus(connected) {
    isConnected = connected;
    
    // Existing code...
    
    // Show/hide header params and latency indicators
    const headerParamsText = document.getElementById('headerParamsText');
    const headerLatencyText = document.getElementById('headerLatencyText');
    
    if (headerParamsText) {
        headerParamsText.style.display = connected ? 'inline' : 'none';
    }
    
    if (headerLatencyText) {
        headerLatencyText.style.display = connected ? 'inline' : 'none';
    }
    
    // Start/stop latency measurement
    if (connected) {
        startLatencyMeasurement();
    } else if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
    }
}

// Update system status handler to update parameter progress
socket.on('system_status', function(data) {
    // Existing code...
    
    // Update parameter progress in header
    if (data.params && headerParamsValue) {
        headerParamsValue.textContent = `${data.params.percentage.toFixed(1)}%`;
    }
});
```

## 4. Add WebSocket Event Handler for Latency Updates

Add a handler in web_server.py to receive latency updates from the client:

```python
# In web_server.py

@socketio.on('update_latency')
def handle_latency_update(data):
    """Handle latency updates from client"""
    global last_system_health
    
    if 'latency' in data:
        last_system_health['latency'] = data['latency']
```

## Implementation Steps

1. First, modify Mavlink_rx_handler.py to track parameter download progress.
2. Then, update web_server.py to implement the ping/pong mechanism and include parameter progress in system status.
3. Finally, update index.html to display these values in the header and implement latency measurement.

## Note

This implementation requires switching to Code mode to edit the Python and HTML files. After reviewing this plan, we should switch to Code mode to implement these changes.