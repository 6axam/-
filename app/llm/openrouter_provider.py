from app.llm.openai_provider import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    default_base_url = "https://openrouter.ai/api/v1"

    def _payload(self, messages, response_format, session_id):
        payload = super()._payload(messages, response_format, session_id)
        if session_id:
            payload["session_id"] = session_id
        return payload
