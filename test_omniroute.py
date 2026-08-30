#!/usr/bin/env python3
"""Test script for OmniRoute connection."""

import os
import sys
from pathlib import Path

# Add src to path so we can import k8s_agent modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

def test_omniroute_connection():
    """Test OmniRoute connection with provided credentials."""
    # Set environment variables for OmniRoute
    os.environ["OMNIROUTE_API_KEY"] = "sk-4dda364fd0661d65-0cb146-39e8ed28"
    os.environ["OMNIROUTE_BASE_URL"] = "http://localhost:20128/v1"
    os.environ["LLM__MODEL"] = "my-claude-copy"

    try:
        # Import after setting environment variables
        from k8s_agent.config import load_settings
        from k8s_agent.llm_client import NVIDIAClient

        # Load settings (will read from environment variables)
        settings = load_settings()

        # Create LLM client
        client = NVIDIAClient(settings)

        # Test with a simple completion request
        messages = [{"role": "user", "content": "Hello"}]
        response = client.chat_completion(messages=messages, max_tokens=10)

        # Check if we got a valid response
        if response and "choices" in response and len(response["choices"]) > 0:
            content = response["choices"][0].get("message", {}).get("content", "")
            if content:
                return {"status": "success", "message": "LLM connection test passed", "response_received": True}
            else:
                return {"status": "success", "message": "LLM connection test passed but empty response", "response_received": False}
        else:
            return {"status": "failure", "message": "Invalid response format from LLM", "response_received": False}

    except Exception as e:
        # Return failure without exposing sensitive info in error message
        return {"status": "failure", "message": f"LLM connection test failed: {type(e).__name__}"}

if __name__ == "__main__":
    result = test_omniroute_connection()
    # Print result as JSON for easy parsing
    import json
    print(json.dumps(result))