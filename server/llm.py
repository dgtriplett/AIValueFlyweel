"""Foundation Model API client (OpenAI-compatible).

CHUNK A ships the client factory only; the AI agents are wired in later chunks.
"""
from .config import get_oauth_token, get_workspace_host, SERVING_ENDPOINT


def get_llm_client():
    """Return an AsyncOpenAI client pointed at Databricks serving endpoints."""
    from openai import AsyncOpenAI

    host = get_workspace_host()
    token = get_oauth_token()
    return AsyncOpenAI(api_key=token, base_url=f"{host}/serving-endpoints")


async def chat_completion(messages: list, model: str = SERVING_ENDPOINT) -> str:
    client = get_llm_client()
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=4096,
        temperature=0.7,
    )
    return resp.choices[0].message.content
