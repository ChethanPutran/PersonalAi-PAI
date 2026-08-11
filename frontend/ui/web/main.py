      
from src.agent.agent import AssistantAgent
from src.voice.voice import VoiceInput,VoiceOutput
from langchain_community.tools.tavily_search import TavilySearchResults
# import signal
import sys
# import os
import logging

TASK_PRIORITY = {
    'start_record': -2,
    'end_record': -2,
    'start_execution': -5,
    'stop_execution': -5,
    'normal': -5,
    'pause': -3,
    'exit': -5,
    'wait': -4,
    "start_processing": -2,
    "start_processing": -2,
    "get_from_camera":-2
}

if __name__ == "__main__":
    logger = logging.getLogger()
    voice_input = VoiceInput()
    voice_output = VoiceOutput(debug=True)
    assitant = AssistantAgent()

    import time

    def handle_message(msg, priority):
        logger.info(f"{priority}: {msg}")
        start_time = time.time()
        
        response, meta_data = assitant.process_query(msg, print_hist=False)
        
        processing_time = time.time() - start_time
        logger.info(f"LLM Processing Time: {processing_time:.2f} seconds Content:{response.content}")

        voice_output.say(response.content)



    def close_all(*args,**kwargs):
        voice_input.close()
        voice_output.close()
        # llm.close()

    voice_input.add_message_callback(handle_message)
    voice_input.add_on_exit_callback(close_all)

    try:
        voice_input.start()
        # llm.start()
        voice_output.process_queue()
    except KeyboardInterrupt:
        close_all()
        sys.exit(0)
