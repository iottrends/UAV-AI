print("Starting program...")
from drone_validator import DroneValidator
import time
import logging
import sys
import threading
import JARVIS
#import llm_ai_v5
import web_server
from logging_config import setup_logging  # Import the logging setup

# Initialize logging
loggers = setup_logging()
mavlink_logger = loggers['mavlink']
agent_logger = loggers['agent']
web_logger = loggers['web_server']

validator = None  # Global variable


def main():
    # Log startup
    web_logger.info("Starting UAV-AI Assistant")

    # Create validator instance
    print("Started UAV-AI Assistant: Web interface will be available at http://localhost:5000")
    validator = DroneValidator()

    # Start the web server in a separate thread
    web_thread = web_server.start_server(
        validator_instance=validator,
        jarvis=JARVIS,
        #llm_ai=llm_ai_v5,
        host='127.0.0.1',  # Listen on all interfaces
        port=5000,  # Set to True for debugging
        debug=False,
        loggers=loggers  # Pass loggers to web_server
    )

    # Initialize LLM agents
    #llm_ai_v5.initialize_agent_params(validator)

    # Connect to the drone using fixed default values
    mavlink_logger.info("Attempting to connect to drone on COM3 at 115200 baud")
    if not validator.connect("COM4", 115200):
        mavlink_logger.error("Failed to connect to COM port")
        print("Failed to connect to COM port")
    else:
        # Start the message reception loop
        validator.start_message_loop()
        validator.request_data_stream()
        validator.request_autopilot_version()
        validator.request_parameter_list()
        mavlink_logger.info("Parameter list request started!")
        print("Parameter list request started!")
        print("Total number of threads:", threading.active_count())

        # Wait for validation to complete
        print("Waiting for hardware validation...")
        while not validator.hardware_validated:
            time.sleep(0.5)

        mavlink_logger.info("Hardware validation complete!")
        print("Hardware validation complete!")
        print(f"Total parameters: {len(validator.get_parameters())}")
        validator.request_blackbox_logs()

    web_logger.info("Terminal interface is active")
    print("Terminal interface is active. Type 'exit' to quit.")
    print("Web interface is available at http://localhost:5000")

    # Main loop for terminal input - now just for queries and exit
    while True:
        command = input("Command: ").strip().lower()

        if command == 'exit':
            break

        elif command.startswith('query:'):
            query = command[6:].strip()
            if not query:
                print("Empty query")
                continue

            # Process through both AI systems
            agent_logger.info(f"Processing query: {query}")
            response = JARVIS.ask_gemini(query)
            print("JARVIS response:")
            if "error" in response:
                print(f"Error: {response['error']}")
                agent_logger.error(f"JARVIS error: {response['error']}")
            else:
                print(f"Intent: {response['intent']}")
                print(f"Message: {response['message']}")
                agent_logger.info(f"JARVIS response - Intent: {response['intent']}")
                if "fix_command" in response:
                    print(f"Fix Command: {response['fix_command']}")
                    mavlink_logger.info(f"Fix command: {response['fix_command']}")

            # Now sending same query to AI_pipeline
            agent_logger.info("Processing through LLM pipeline...")
            print("Processing through LLM pipeline...")
            #pipeline_resp = llm_ai_v5.ask_ai5(query, validator, 4500)
            #print("LLM response:")
            #print(pipeline_resp)
            #agent_logger.info(f"LLM response: {pipeline_resp[:200]}...")  # Log first 200 chars

        else:
            print("Unknown command. Available commands: query:your_question, exit")

    # Cleanup and exit
    web_logger.info("UAV-AI Assistant shutting down")
    print("Exiting...")
    validator.disconnect()


if __name__ == "__main__":
    main()