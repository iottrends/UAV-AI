// ===== chat.js — Chat & voice handlers =====

document.addEventListener('DOMContentLoaded', function() {
   const chatInput = document.getElementById('chatInput');
   const sendButton = document.getElementById('sendButton');
   const voiceButton = document.getElementById('voiceButton');

   function sendMessage() {
      if (!chatInput) return;

      const message = chatInput.value.trim();
      if (!message) return;

      window._app.addMessage({
         text: message,
         time: window._app.getCurrentTime()
      }, true);

      chatInput.value = '';

      if (window._app.socket && (window._app.isConnected || window._app.logLoaded)) {
         window._app.socket.emit('chat_message', { message: message });
      } else {
         window._app.addMessage({
            text: "<strong>System:</strong> Please connect to a drone or upload a log file first.",
            time: window._app.getCurrentTime()
         });
      }
   }

   // Set up chat input
   if (chatInput) {
      chatInput.addEventListener('keypress', function(e) {
         if (e.key === 'Enter') {
            sendMessage();
         }
      });
   }

   if (sendButton) {
      sendButton.addEventListener('click', sendMessage);
   }

   // Wait for socket to be available, then register chat handlers
   function setupSocketHandlers() {
      var socket = window._app.socket;
      if (!socket) {
         setTimeout(setupSocketHandlers, 100);
         return;
      }

      socket.on('chat_processing', function(data) {
         console.log('Processing message:', data);
      });

      socket.on('chat_response', function(data) {
         if (data.error) {
            window._app.addMessage({
               text: 'Error: ' + data.error,
               time: window._app.getCurrentTime()
            });
            return;
         }

         var messageText = '';

         if (data.source === 'log_analysis') {
            // Log analysis response — render markdown analysis + trigger charts
            var analysis = data.analysis || 'No analysis returned.';
            messageText = '<strong>JARVIS (Log Analysis):</strong><br><br>' + renderSimpleMarkdown(analysis);

            // Trigger chart rendering in the Logs tab viz area
            if (data.charts && data.charts.length > 0 && window._app.renderLogCharts) {
               window._app.renderLogCharts(data.charts);
            }
         } else if (data.source === 'copilot') {
            messageText = '<strong>Co-Pilot:</strong> ' + data.response;
            if (data.message) {
               messageText += '<br><em>' + data.message + '</em>';
            }
            if (data.error) {
               messageText += '<br><span style="color:var(--danger-color);">' + data.error + '</span>';
            }
         } else if (data.source === 'jarvis') {
            var response = data.response;
            console.log("JARVIS response object:", response);

            var messageStr = '';

            if (response && response.message) {
               messageStr += response.message + '<br><br>';
            }
            if (response && response.intent) {
               messageStr += '<strong>Intent:</strong> ' + response.intent + '<br><br>';
            }

            messageStr += window._app.formatResponse(response);

            messageText = '<strong>JARVIS:</strong><br><br>' + messageStr;
         } else {
            console.log("AI Pipeline response:", data.response);
            messageText = '<strong>AI Pipeline:</strong><br><br>' + data.response;
         }

         window._app.addMessage({
            text: messageText,
            time: window._app.getCurrentTime()
         });
      });

      // Listen for voice command responses
      socket.on('voice_status', function(data) {
         var voiceStatusIndicator = document.getElementById('voiceStatusIndicator');
         if (voiceStatusIndicator) {
            voiceStatusIndicator.style.display = 'block';
            if (data.status === 'listening') {
               voiceStatusIndicator.textContent = 'Listening...';
               voiceButton.classList.add('active');
            } else if (data.status === 'processing') {
               voiceStatusIndicator.textContent = 'Processing...';
               voiceButton.classList.remove('active');
            } else {
               voiceStatusIndicator.style.display = 'none';
               voiceButton.classList.remove('active');
            }
         }
      });

      socket.on('voice_response', function(data) {
         var voiceStatusIndicator = document.getElementById('voiceStatusIndicator');
         if (voiceStatusIndicator) {
            voiceStatusIndicator.style.display = 'none';
            voiceButton.classList.remove('active');
         }

         if (data.error) {
            window._app.addMessage({
               text: '<strong>Voice Error:</strong> ' + data.error,
               time: window._app.getCurrentTime()
            });
            return;
         }
         if (data.message) {
            window._app.addMessage({
               text: '<strong>System:</strong> ' + data.message,
               time: window._app.getCurrentTime()
            });
         }
         if (data.response) {
            var messageStr = '';
            if (data.response.message) {
               messageStr += data.response.message + '<br><br>';
            }
            if (data.response.intent) {
               messageStr += '<strong>Intent:</strong> ' + data.response.intent + '<br><br>';
            }
            messageStr += window._app.formatResponse(data.response);

            window._app.addMessage({
               text: '<strong>JARVIS (Voice):</strong><br><br>' + messageStr,
               time: window._app.getCurrentTime()
            });
         }
      });
   }
   setupSocketHandlers();

   // Simple markdown to HTML for log analysis responses
   function renderSimpleMarkdown(text) {
      if (!text) return '';
      // Escape HTML first
      var div = document.createElement('div');
      div.textContent = text;
      var html = div.innerHTML;
      // Headers
      html = html.replace(/^### (.+)$/gm, '<h4 style="margin:0.5rem 0 0.25rem;">$1</h4>');
      html = html.replace(/^## (.+)$/gm, '<h3 style="margin:0.5rem 0 0.25rem;">$1</h3>');
      html = html.replace(/^# (.+)$/gm, '<h3 style="margin:0.5rem 0 0.25rem;">$1</h3>');
      // Bold and italic
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
      // Inline code
      html = html.replace(/`([^`]+)`/g, '<code style="background:#eee;padding:0.1rem 0.3rem;border-radius:3px;font-size:0.85em;">$1</code>');
      // Unordered lists
      html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
      html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul style="padding-left:1.2rem;margin:0.25rem 0;">$&</ul>');
      // Line breaks
      html = html.replace(/\n/g, '<br>');
      html = html.replace(/<\/li><br>/g, '</li>');
      html = html.replace(/<\/ul><br>/g, '</ul>');
      html = html.replace(/<\/h[34]><br>/g, function(m) { return m.replace('<br>', ''); });
      return html;
   }

   // Voice button PTT functionality
   if (voiceButton) {
      var voiceRecordingTimeout;
      var MAX_RECORDING_TIME = 10000;

      voiceButton.addEventListener('mousedown', function() {
         console.log("Voice button pressed - starting recording");
         if (window._app.socket && window._app.isConnected) {
            window._app.socket.emit('start_voice_input');
            voiceRecordingTimeout = setTimeout(function() {
               console.log("Max recording time reached, stopping voice input.");
               window._app.socket.emit('stop_voice_input');
            }, MAX_RECORDING_TIME);
         } else {
            window._app.addMessage({
               text: "<strong>System:</strong> Please connect to a drone first to use voice commands.",
               time: window._app.getCurrentTime()
            });
         }
      });

      voiceButton.addEventListener('mouseup', function() {
         console.log("Voice button released - stopping recording");
         if (voiceRecordingTimeout) {
            clearTimeout(voiceRecordingTimeout);
         }
         if (window._app.socket && window._app.isConnected) {
            window._app.socket.emit('stop_voice_input');
         }
      });

      // For touch devices
      voiceButton.addEventListener('touchstart', function(e) {
         e.preventDefault();
         if (window._app.socket && window._app.isConnected) {
            window._app.socket.emit('start_voice_input');
            voiceRecordingTimeout = setTimeout(function() {
               console.log("Max recording time reached, stopping voice input.");
               window._app.socket.emit('stop_voice_input');
            }, MAX_RECORDING_TIME);
         } else {
            window._app.addMessage({
               text: "<strong>System:</strong> Please connect to a drone first to use voice commands.",
               time: window._app.getCurrentTime()
            });
         }
      });

      voiceButton.addEventListener('touchend', function(e) {
         e.preventDefault();
         if (voiceRecordingTimeout) {
            clearTimeout(voiceRecordingTimeout);
         }
         if (window._app.socket && window._app.isConnected) {
            window._app.socket.emit('stop_voice_input');
         }
      });
   }
});
