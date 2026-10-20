import time
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# Ensure GEMINI_API_KEY is set from AI_API_KEY if needed
if not os.environ.get("GEMINI_API_KEY") and os.environ.get("AI_API_KEY"):
    os.environ["GEMINI_API_KEY"] = os.environ.get("AI_API_KEY")

client = genai.Client()

MODELS_TO_TEST = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-1.5-flash",
]

TEST_PROMPT = "Explain the theory of relativity in two short paragraphs."

def benchmark_model(model_name):
    print(f"Testing {model_name}...")
    start_time = time.time()
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=TEST_PROMPT,
            config=types.GenerateContentConfig(
                max_output_tokens=150,
                temperature=0.1
            )
        )
        end_time = time.time()
        latency = end_time - start_time
        usage = response.usage_metadata
        tokens_out = usage.candidates_token_count if usage else "N/A"
        print(f"  -> Success! Latency: {latency:.4f} seconds (Output tokens: {tokens_out})")
        return latency
    except Exception as e:
        print(f"  -> Error testing {model_name}: {e}")
        return None

def run_tests():
    results = {}
    print("Performing warm-up call...")
    try:
        client.models.generate_content(model="gemini-3.1-flash-lite", contents="Hi")
    except Exception:
        pass
    print("-" * 40)

    iterations = 2
    for model in MODELS_TO_TEST:
        latencies = []
        for i in range(iterations):
            latency = benchmark_model(model)
            if latency:
                latencies.append(latency)
            time.sleep(1)
            
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            results[model] = avg_latency
        print("-" * 40)

    print("\n=== FINAL RESULTS (Average Latency) ===")
    sorted_results = sorted(results.items(), key=lambda item: item[1])
    for model, latency in sorted_results:
        print(f"{model}: {latency:.4f} seconds")

if __name__ == "__main__":
    run_tests()
