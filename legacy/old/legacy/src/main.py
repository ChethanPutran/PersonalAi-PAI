import logging
import os
from dotenv import load_dotenv
from pathlib import Path

# 1. Change the global level to WARNING (hides library INFO)
logging.basicConfig(level=logging.WARNING)

# 2. Set only YOUR specific app logs to INFO
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BASE_DIR = Path(__file__).parent.parent

load_dotenv(BASE_DIR / '.env')

def main():
    from src.graph import AgentGraph
    # Initialize the agent
    agent = AgentGraph()
    
    print("🤖 Agent Assistant Ready!")
    print("Type 'exit' to quit\n")

    
    while True:
        user_input = input("You: ")
        if user_input.lower() in ['exit', 'quit']:
            break
        
        try:
            response = agent.run(user_input)
            print(f"Assistant: {response}\n")
        except Exception as e:
            raise e

if __name__ == "__main__":
    main()
