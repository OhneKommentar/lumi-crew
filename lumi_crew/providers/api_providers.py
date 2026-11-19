"""Providers that talk to an HTTP API with native function calling."""
from __future__ import annotations

import json
import time
import uuid

from .. import usage

from .base import LimitError, Message, Provider, ProviderError, Reply, ToolCall, ToolSpec, is_limit_message


# --------------------------------------------------------------------------- OpenAI-compatible
class OpenAICompatProvider(Provider):
    """OpenAI, LM Studio, Ollama, OpenRouter … anything with /v1/chat/completions."""
    kind = "openai_compat"

    def __init__(self, name, model=None, base_url=None, api_key=None, **options):
        super().__init__(name, model, **options)
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")

    async def _model(self) -> str:
        if not self.model:  # LM Studio / Ollama: take the first loaded model
            models = await self.client.models.list()
            if not models.data:
                raise ProviderError(f"{self.name}: no model loaded and none configured")
            self.model = models.data[0].id
        return self.model

    @staticmethod
    def _convert(system: str, messages: list[Message]) -> list[dict]:
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                msg = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                          "function": {"name": tc.name, "arguments": json.dumps(tc.args)}}
                                         for tc in m.tool_calls]
                out.append(msg)
            elif m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        return out

    async def complete(self, system, messages, tools):
        kwargs = {"model": await self._model(), "messages": self._convert(system, messages)}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
        start = time.time()
        try:
            r = await self.client.chat.completions.create(**kwargs)
        except Exception as e:  # openai raises many typed errors; surface them uniformly
            usage.record(self.name, self.model, seconds=time.time() - start, error=True)
            raise (LimitError if is_limit_message(str(e)) else ProviderError)(f"{self.name}: {e}") from e
        u = r.usage
        usage.record(self.name, self.model, input_tokens=getattr(u, "prompt_tokens", 0) or 0,
                     output_tokens=getattr(u, "completion_tokens", 0) or 0, seconds=time.time() - start)
        msg = r.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(tc.id or uuid.uuid4().hex[:8], tc.function.name, args))
        return Reply(text=msg.content or "", tool_calls=calls)


# --------------------------------------------------------------------------- Anthropic
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2"}


class AnthropicProvider(Provider):
    """Claude via the Anthropic API (ANTHROPIC_API_KEY or an `ant auth login` profile)."""
    kind = "anthropic"

    def __init__(self, name, model=None, api_key=None, effort=None, **options):
        super().__init__(name, model or "claude-opus-5-5", **options)
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        self.effort = effort

    @staticmethod
    def _convert(messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                if m.raw is not None:          # replay native blocks unchanged (thinking, fallback …)
                    out.append({"role": "assistant", "content": m.raw})
                else:
                    blocks = [{"type": "text", "text": m.content}] if m.content else []
                    blocks += [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args}
                               for tc in m.tool_calls]
                    out.append({"role": "assistant", "content": blocks})
            elif m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                # all tool results of one turn go into a single user message
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    async def complete(self, system, messages, tools):
        kwargs = {
            "model": self.model, "max_tokens": 16000, "system": system,
            "messages": self._convert(messages),
        }
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                               for t in tools]
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        a = self._anthropic
        start = time.time()
        try:
            if self.model in FALLBACK_MODELS:  # server-side refusal fallback
                resp = await self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = await self.client.messages.create(**kwargs)
        except a.AuthenticationError as e:
            raise ProviderError("anthropic: invalid or missing API key (set ANTHROPIC_API_KEY)") from e
        except a.NotFoundError as e:
            raise ProviderError(f"anthropic: unknown model `{self.model}`") from e
        except a.RateLimitError as e:
            raise LimitError("anthropic: rate limit reached") from e
        except a.APIStatusError as e:
            raise ProviderError(f"anthropic: API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise ProviderError("anthropic: network error") from e

        usage.record(self.name, self.model, input_tokens=resp.usage.input_tokens,
                     output_tokens=resp.usage.output_tokens, seconds=time.time() - start)
        if resp.stop_reason == "refusal":
            return Reply(text="(The model declined this request.)")
        text, calls = [], []
        for b in resp.content:
            if b.type == "text":
                text.append(b.text)
            elif b.type == "tool_use":
                calls.append(ToolCall(b.id, b.name, dict(b.input)))
        return Reply(text="\n".join(text), tool_calls=calls, raw=[b.to_dict() for b in resp.content])


# --------------------------------------------------------------------------- Gemini
class GeminiProvider(Provider):
    """Gemini via the Google AI API (GEMINI_API_KEY)."""
    kind = "gemini"

    def __init__(self, name, model=None, api_key=None, **options):
        super().__init__(name, model or "gemini-flash-latest", **options)
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def _convert(self, messages: list[Message]):
        T = self.types
        out = []
        prev_was_tool = False
        for m in messages:
            is_tool = m.role == "tool"
            if is_tool:
                # all function responses of one turn go into a single user Content
                part = T.Part.from_function_response(name=m.name, response={"result": m.content})
                if prev_was_tool:
                    out[-1].parts.append(part)
                else:
                    out.append(T.Content(role="user", parts=[part]))
            prev_was_tool = is_tool
            if m.role == "user":
                out.append(T.Content(role="user", parts=[T.Part.from_text(text=m.content)]))
            elif m.role == "assistant":
                if m.raw is not None:          # keeps thought signatures
                    out.append(m.raw)
                else:
                    parts = [T.Part.from_text(text=m.content)] if m.content else []
                    parts += [T.Part(function_call=T.FunctionCall(name=tc.name, args=tc.args)) for tc in m.tool_calls]
                    out.append(T.Content(role="model", parts=parts))
        return out

    async def complete(self, system, messages, tools):
        T = self.types
        config = T.GenerateContentConfig(
            system_instruction=system,
            automatic_function_calling=T.AutomaticFunctionCallingConfig(disable=True),
        )
        if tools:
            config.tools = [T.Tool(function_declarations=[
                T.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in tools])]
        start = time.time()
        try:
            resp = await self.client.aio.models.generate_content(
                model=self.model, contents=self._convert(messages), config=config)
        except Exception as e:
            usage.record(self.name, self.model, seconds=time.time() - start, error=True)
            raise (LimitError if is_limit_message(str(e)) else ProviderError)(f"gemini: {e}") from e
        meta = resp.usage_metadata
        usage.record(self.name, self.model, input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
                     output_tokens=getattr(meta, "candidates_token_count", 0) or 0, seconds=time.time() - start)
        if not resp.candidates or not resp.candidates[0].content:
            return Reply(text="(empty response from Gemini)")
        content = resp.candidates[0].content
        text, calls = [], []
        for p in content.parts or []:
            if p.function_call:
                calls.append(ToolCall(p.function_call.id or uuid.uuid4().hex[:8],
                                      p.function_call.name, dict(p.function_call.args or {})))
            elif p.text and not p.thought:
                text.append(p.text)
        return Reply(text="\n".join(text), tool_calls=calls, raw=content)
