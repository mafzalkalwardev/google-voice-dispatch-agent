import os
from src.ai_groq import GroqAgent
from src.config import Config


def main():
    cfg = Config.load()
    api_key = cfg.groq_api_key or os.getenv("GROQ_API_KEY")
    endpoint = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1")
    print("Using GROQ endpoint:", endpoint)
    agent = GroqAgent(api_key=api_key, model=cfg.groq_model)
    print("Sending test prompt to Groq...\n")
    try:
        out = agent.generate_call_script(contact_name="Test Contact", objective="test connectivity", context="run quick connectivity test")
        print("--- Groq Response Start ---")
        print(out)
        print("--- Groq Response End ---")
    except Exception as e:
        print("Groq test failed:", e)


if __name__ == '__main__':
    main()
