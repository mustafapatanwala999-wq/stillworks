"""Gemini as the agent under test, over the plain REST API (standard library only).

The request shapes below are written from memory of Google's public REST API and
have NOT been checked against its docs, which this build environment could not
reach. They are checked against the live API instead: the first pilot run's
errors show any mismatch. Nothing about quotas or free-tier terms is assumed in
code; every API failure surfaces as an InfraError carrying Google's own message.

    export GEMINI_API_KEY=...             # a key from Google AI Studio
    python -m apbench.agents.gemini       # list models the key can call
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from ..tools import SPECS, InfraError
from .claude import SYSTEM

API = "https://generativelanguage.googleapis.com/v1beta"


def function_declarations(specs=SPECS) -> list:
    declarations = []
    for spec in specs:
        declaration = {"name": spec["name"], "description": spec["description"]}
        schema = spec["input_schema"]
        if schema["properties"]:  # a tool with no arguments declares no parameters
            parameters = {"type": "object", "properties": schema["properties"]}
            if schema["required"]:
                parameters["required"] = schema["required"]
            declaration["parameters"] = parameters
        declarations.append(declaration)
    return declarations


class RestClient:
    """Minimal HTTP client. Retries rate limits and server errors, then gives up loudly."""

    def __init__(self, api_key=None, max_retries=4, sleep=time.sleep):
        # Where a proxy injects the real key, any placeholder value works here.
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "placeholder")
        self.max_retries = max_retries
        self.sleep = sleep

    def _request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{API}/{path}", data=data, method=method,
            headers={"x-goog-api-key": self.api_key, "content-type": "application/json"})
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:600]
                if exc.code in (429, 500, 503) and attempt < self.max_retries:
                    retry_after = exc.headers.get("Retry-After", "")
                    self.sleep(float(retry_after) if retry_after.isdigit() else min(5 * 2 ** attempt, 60))
                    continue
                raise InfraError(f"Gemini API returned HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < self.max_retries:
                    self.sleep(min(5 * 2 ** attempt, 60))
                    continue
                raise InfraError(f"Gemini API unreachable: {exc}") from exc

    def generate(self, model, body):
        return self._request("POST", f"models/{model}:generateContent", body)

    def list_models(self):
        return self._request("GET", "models?pageSize=200").get("models", [])


class GeminiAgent:
    def __init__(self, model, max_turns=40, client=None):
        self.client = client or RestClient()
        self.name = model
        self.meta = {"provider": "google", "model": model}
        self.max_turns = max_turns
        self.tools = [{"functionDeclarations": function_declarations()}]
        self.system = {"parts": [{"text": SYSTEM}]}

    def run(self, task, env):
        contents = [{"role": "user", "parts": [{"text": task.instruction}]}]
        for _ in range(self.max_turns):
            reply = self.client.generate(self.name, {
                "systemInstruction": self.system, "contents": contents, "tools": self.tools})
            candidates = reply.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"no candidates returned: {reply.get('promptFeedback')}")
            content = candidates[0].get("content") or {}
            calls = [part["functionCall"] for part in content.get("parts", []) if "functionCall" in part]
            if not calls:
                reason = candidates[0].get("finishReason")
                if reason not in (None, "STOP"):
                    raise RuntimeError(f"model stopped with finishReason={reason}")
                return

            # Append the model's turn exactly as returned: thinking models attach
            # signatures to it that must be sent back unchanged.
            contents.append(content)
            responses = []
            for call in calls:
                result = env.call(call["name"], call.get("args") or {})
                responses.append({"functionResponse": {"name": call["name"], "response": result}})
            if env.finished:
                return
            contents.append({"role": "user", "parts": responses})
        raise RuntimeError(f"no finish after {self.max_turns} model turns")


def main():
    for model in RestClient().list_models():
        if "generateContent" in model.get("supportedGenerationMethods", []):
            print(model["name"].removeprefix("models/"))


if __name__ == "__main__":
    main()
