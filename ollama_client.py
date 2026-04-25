# -*- coding: utf-8 -*-
"""
ollama_client.py — Local model adapter for Phase 4 & 5
Wraps Ollama so it can be swapped in place of the Anthropic client
with minimal changes to the rest of the codebase.

Usage:
    from ollama_client import generate, embed

    # Generation (replaces claude client.messages.create)
    response = generate(prompt="Analyze Apple's Q3 results...",
                        system="You are a senior equity analyst...")

    # Embeddings (replaces Voyage AI)
    vectors = embed(["Apple reported revenue of $94.9B...",
                     "NVIDIA's data center segment grew 154%..."])
"""

import ollama

# ── Model configuration ────────────────────────────────────────────────────
GENERATION_MODEL = "llama3.1:8b"   # swap to llama3.1:70b if you have the VRAM
EMBEDDING_MODEL  = "nomic-embed-text"


def generate(
    prompt: str,
    system: str = "",
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """
    Generate a text response using a local Ollama model.
    Returns the response string directly (mirrors the pattern used
    in briefing_generator.py for easy swapping).
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = ollama.chat(
        model=GENERATION_MODEL,
        messages=messages,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    )
    return response["message"]["content"]


def embed(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a list of text strings using a local Ollama model.
    Returns a list of float vectors, one per input text.
    """
    vectors = []
    for text in texts:
        response = ollama.embeddings(
            model=EMBEDDING_MODEL,
            prompt=text,
        )
        vectors.append(response["embedding"])
    return vectors


def test_connection() -> bool:
    """Quick smoke test — returns True if Ollama is reachable and models are loaded."""
    try:
        result = generate("Say 'OK' and nothing else.", max_tokens=10)
        return bool(result.strip())
    except Exception as e:
        print(f"Ollama connection failed: {e}")
        print("Make sure Ollama is running: open a separate terminal and run 'ollama serve'")
        return False


if __name__ == "__main__":
    print("Testing Ollama connection...")
    if test_connection():
        print("Ollama is working correctly.")
        # Quick embedding test
        vecs = embed(["Apple reported strong quarterly earnings."])
        print(f"Embedding dimension: {len(vecs[0])}")
    else:
        print("Check that Ollama is installed and running.")