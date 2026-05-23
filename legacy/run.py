#!/usr/bin/env python3
"""
One-file voice wrapper - add this to your project root and run:
python run_voice.py
"""

import sys
import os

# Add the current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from src.graph import AgentGraph
from src.voice.input import VoiceInput
from src.voice.output import VoiceOutput

def quick_voice_session():
    """Quick voice session without any configuration"""
    print("Initializing voice assistant...")
    
    agent = AgentGraph()
    voice_in = VoiceInput(wake_word=None)  # No wake word needed
    voice_out = VoiceOutput(engine_type="pyttsx3")
    
    voice_out.speak("Voice assistant ready. Speak your command.")
    
    while True:
        command = voice_in.listen_once(timeout=5)
        
        if command:
            print(f"You said: {command}")
            
            if command.lower() in ['exit', 'quit', 'goodbye']:
                voice_out.speak("Goodbye!")
                break
            
            print("Thinking...")
            response = agent.run(command)
            
            if response:
                print(f"Assistant: {response}")
                voice_out.speak(response)
        else:
            print("No command detected. Try again or press Ctrl+C to exit.")

if __name__ == "__main__":
    try:
        quick_voice_session()
    except KeyboardInterrupt:
        print("\nExiting...")