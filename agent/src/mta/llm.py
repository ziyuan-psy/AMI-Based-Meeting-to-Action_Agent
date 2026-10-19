"""LLM access. The pipeline speaks one Anthropic-style interface (system, messages, tools, tool_choice);
`OpenAIAdapter` translates it to OpenAI-compatible chat completions (used with the Portkey gateway).

Tests inject a fake client with the same `messages.create` signature.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace as NS

from . import config


class OpenAIAdapter:
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=config.OPENAI_BASE_URL, max_retries=4)
        self.messages = NS(create=self.create)

    @staticmethod
    def _messages(system: str, messages: list) -> list:
        out = [{"role": "system", "content": system}]
        for m in messages:
            c = m["content"]
            if isinstance(c, str):
                out.append({"role": m["role"], "content": c})
            elif m["role"] == "assistant":
                text = "".join(b["text"] for b in c if b["type"] == "text")
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                         for b in c if b["type"] == "tool_use"]
                msg = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:  # user turn made of tool results
                for b in c:
                    if b["type"] == "tool_result":
                        body = ("ERROR: " if b.get("is_error") else "") + str(b["content"])
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": body})
        return out

    def create(self, model, system, messages, max_tokens=4096, tools=None, tool_choice=None):
        kw = dict(model=model, messages=self._messages(system, messages),
                  max_completion_tokens=max(max_tokens, config.OPENAI_MIN_COMPLETION_TOKENS))
        if tools:
            kw["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
            if tool_choice and tool_choice.get("type") == "tool":
                kw["tool_choice"] = {"type": "function", "function": {"name": tool_choice["name"]}}
            elif tool_choice:
                kw["tool_choice"] = "required"
        resp = self.client.chat.completions.create(**kw)
        msg = resp.choices[0].message
        content = []
        if msg.content:
            content.append(NS(type="text", text=msg.content))
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            content.append(NS(type="tool_use", id=tc.id, name=tc.function.name, input=args))
        u = resp.usage
        return NS(content=content, usage=NS(input_tokens=getattr(u, "prompt_tokens", 0),
                                            output_tokens=getattr(u, "completion_tokens", 0)))


class LLM:
    def __init__(self, client=None, model: str | None = None):
        if client is None:
            if os.environ.get("OPENAI_API_KEY"):
                client = OpenAIAdapter()
                model = model or config.OPENAI_MODEL
            elif os.environ.get("ANTHROPIC_API_KEY"):
                import anthropic
                client = anthropic.Anthropic(max_retries=4)
            else:
                raise RuntimeError("Set OPENAI_API_KEY (or ANTHROPIC_API_KEY) in .env")
        self.client = client
        self.model = model or config.MODEL
        self.in_tokens = 0
        self.out_tokens = 0

    def create(self, system: str, messages: list, tools: list | None = None,
               tool_choice: dict | None = None, max_tokens: int = 4096):
        kw = dict(model=self.model, system=system, messages=messages, max_tokens=max_tokens)
        if tools:
            kw["tools"] = tools
            if tool_choice:
                kw["tool_choice"] = tool_choice
        resp = self.client.messages.create(**kw)
        u = getattr(resp, "usage", None)
        if u:
            self.in_tokens += getattr(u, "input_tokens", 0)
            self.out_tokens += getattr(u, "output_tokens", 0)
        return resp

    def forced_tool(self, system: str, user: str, name: str, schema: dict, max_tokens: int = 8192) -> dict:
        """One call that must return the input of tool `name` (structured output). Retries with a bigger budget."""
        tool = {"name": name, "description": "Submit the result.", "input_schema": schema}
        for attempt in range(3):
            resp = self.create(system, [{"role": "user", "content": user}], [tool],
                               {"type": "tool", "name": name}, max_tokens * (2 ** attempt))
            for block in resp.content:
                if block.type == "tool_use" and block.name == name and block.input:
                    return block.input
        raise RuntimeError(f"model did not call {name} after 3 attempts")
