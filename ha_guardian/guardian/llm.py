from __future__ import annotations

from typing import Callable


def run_agent(
    client,
    model: str,
    system: str,
    tools: list[dict],
    handler: Callable[[str, dict], str],
    messages: list[dict],
    max_steps: int = 8,
    max_tokens: int = 1024,
) -> str:
    """Generic tool-use loop. Mutates `messages`. Returns the final text."""
    for _ in range(max_steps):
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system, tools=tools, messages=messages
        )
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            try:
                out = handler(block.name, dict(block.input))
            except Exception as e:  # tool errors go back to the model, not up the stack
                out = f"ERROR: {e}"
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(out)})
        messages.append({"role": "user", "content": results})
    return "(stopped: step limit reached)"
