import logging
import sys
import os
import logging.handlers
import glob
import time


def _get_log_dir():
    """Return a writable log directory. Uses a user-local path when running
    from a PyInstaller bundle (where the CWD may be read-only)."""
    if getattr(sys, '_MEIPASS', None):
        # Bundled mode — write logs next to the executable
        base = os.path.dirname(sys.executable)
        return os.path.join(base, 'logs')
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), 'logs')


def setup_logging():
    """
    Set up logging configuration for the entire application.
    Creates three log files:
    - mavlink_log.txt: For MAVLink communication logs
    - Agent.log: For AI agent/assistant activities
    - webserver.log: For web server activities
    """
    # Ensure log directory exists
    log_dir = _get_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    
    # Cleanup old logs if total size exceeds 1GB
    def cleanup_old_logs():
        log_files = glob.glob(os.path.join(log_dir, '*.log')) + \
                   glob.glob(os.path.join(log_dir, '*.txt'))
        
        # Calculate total size
        total_size = sum(os.path.getsize(f) for f in log_files)
        
        # If over 1GB, delete oldest files
        if total_size > 1024*1024*1024:  # 1GB
            # Sort by modification time (oldest first)
            log_files.sort(key=os.path.getmtime)
            while total_size > 1024*1024*1024 and len(log_files) > 1:
                oldest = log_files.pop(0)
                total_size -= os.path.getsize(oldest)
                try:
                    os.remove(oldest)
                except Exception as e:
                    print(f"Error removing log file {oldest}: {e}")
    
    # Run cleanup before setting up new logs
    cleanup_old_logs()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Setup console handler for all logs
    #console_handler = logging.StreamHandler()
    #console_handler.setLevel(logging.INFO)
    #console_handler.setFormatter(formatter)
    #root_logger.addHandler(console_handler)

    # 1. Configure MAVLink logger
    mavlink_logger = logging.getLogger('mavlink')
    mavlink_logger.setLevel(logging.DEBUG)
    # Use immediate flush for mavlink logs (high volume)
    mavlink_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'mavlink_log.txt'),
        encoding='utf-8',
        maxBytes=100*1024*1024,  # 100MB per file
        backupCount=5  # Keep 5 backup files
    )
    mavlink_handler.setLevel(logging.DEBUG)
    mavlink_handler.setFormatter(formatter)
    # Set flush behavior
    mavlink_handler.flush = lambda: mavlink_handler.stream.flush()
    mavlink_logger.addHandler(mavlink_handler)
    mavlink_logger.propagate = False  # Don't send to root logger

    # 2. Configure Agent logger
    agent_logger = logging.getLogger('agent')
    agent_logger.setLevel(logging.DEBUG)
    agent_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'Agent.log'),
        encoding='utf-8',
        maxBytes=100*1024*1024,  # 100MB per file
        backupCount=5  # Keep 5 backup files
    )
    agent_handler.setLevel(logging.DEBUG)
    agent_handler.setFormatter(formatter)
    # Set flush behavior
    agent_handler.flush = lambda: agent_handler.stream.flush()
    agent_logger.addHandler(agent_handler)
    agent_logger.propagate = False  # Don't send to root logger

    # 3. Configure Web Server logger
    web_logger = logging.getLogger('web_server')
    web_logger.setLevel(logging.DEBUG)
    web_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'webserver.log'),
        encoding='utf-8',
        maxBytes=100*1024*1024,  # 100MB per file
        backupCount=5  # Keep 5 backup files
    )
    web_handler.setLevel(logging.DEBUG)
    web_handler.setFormatter(formatter)
    # Set flush behavior
    web_handler.flush = lambda: web_handler.stream.flush()
    web_logger.addHandler(web_handler)
    web_logger.propagate = False  # Don't send to root logger

    # 4. Configure STT Module logger
    stt_logger = logging.getLogger('stt_module')
    stt_logger.setLevel(logging.DEBUG)
    stt_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'STT.log'),
        encoding='utf-8',
        maxBytes=100*1024*1024,  # 100MB per file
        backupCount=5  # Keep 5 backup files
    )
    stt_handler.setLevel(logging.DEBUG)
    stt_handler.setFormatter(formatter)
    # Set flush behavior
    stt_handler.flush = lambda: stt_handler.stream.flush()
    stt_logger.addHandler(stt_handler)
    stt_logger.propagate = False  # Don't send to root logger

    # Create a dictionary of loggers for easy access
    loggers = {
        'mavlink': mavlink_logger,
        'agent': agent_logger,
        'web_server': web_logger,
        'stt_module': stt_logger
    }

    # Add a custom filter to force flushing after each log entry
    class FlushFilter(logging.Filter):
        def filter(self, record):
            # Always return True to accept the record
            return True
        
        def __call__(self, record):
            result = self.filter(record)
            # Force flush on all handlers
            for logger_name, logger in loggers.items():
                for handler in logger.handlers:
                    if hasattr(handler, 'flush'):
                        handler.flush()
            return result
    
    # Apply the flush filter to all loggers
    flush_filter = FlushFilter()
    for logger_name, logger in loggers.items():
        logger.addFilter(flush_filter)
    
    return loggers