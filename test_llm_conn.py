import logging
import sys
from core.llm_core import LLMCore

# Setup basic logging to see what's happening
logging.basicConfig(level=logging.INFO)

def test_connectivity():
    print("Initializing LLMCore...")
    try:
        llm = LLMCore()
        print("Invoking LLM with a simple test...")
        # Attempt a simple chat call
        response = llm.chat("You are a helpful assistant.", "Hello, say 'Ready to plan 5G network' if you can hear me.")
        print(f"\nLLM Response: {response}")
        
        if "Ready to plan 5G network" in response:
            print("\nSUCCESS: LLM is connected and responsive.")
        else:
            print("\nWARNING: LLM responded but the content was unexpected.")
            
    except Exception as e:
        print(f"\nFAILURE: Could not connect to LLM. Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_connectivity()
