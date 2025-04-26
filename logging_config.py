import logging
import os


def setup_logging():
    """
    Set up logging configuration for the entire application.
    Creates three log files:
    - mavlink_log.txt: For MAVLink communication logs
    - Agent.log: For AI agent/assistant activities
    - webserver.log: For web server activities
    """
    # Ensure log directory exists
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

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
    mavlink_handler = logging.FileHandler(os.path.join(log_dir, 'mavlink_log.txt'), encoding='utf-8')
    mavlink_handler.setLevel(logging.DEBUG)
    mavlink_handler.setFormatter(formatter)
    # Set flush behavior
    mavlink_handler.flush = lambda: mavlink_handler.stream.flush()
    mavlink_logger.addHandler(mavlink_handler)
    mavlink_logger.propagate = False  # Don't send to root logger

    # 2. Configure Agent logger
    agent_logger = logging.getLogger('agent')
    agent_logger.setLevel(logging.DEBUG)
    agent_handler = logging.FileHandler(os.path.join(log_dir, 'Agent.log'), encoding='utf-8')
    agent_handler.setLevel(logging.DEBUG)
    agent_handler.setFormatter(formatter)
    # Set flush behavior
    agent_handler.flush = lambda: agent_handler.stream.flush()
    agent_logger.addHandler(agent_handler)
    agent_logger.propagate = False  # Don't send to root logger

    # 3. Configure Web Server logger
    web_logger = logging.getLogger('web_server')
    web_logger.setLevel(logging.DEBUG)
    web_handler = logging.FileHandler(os.path.join(log_dir, 'webserver.log'), encoding='utf-8')
    web_handler.setLevel(logging.DEBUG)
    web_handler.setFormatter(formatter)
    # Set flush behavior
    web_handler.flush = lambda: web_handler.stream.flush()
    web_logger.addHandler(web_handler)
    web_logger.propagate = False  # Don't send to root logger

    # Create a dictionary of loggers for easy access
    loggers = {
        'mavlink': mavlink_logger,
        'agent': agent_logger,
        'web_server': web_logger
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