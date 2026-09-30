from app.llm.openai_provider import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    default_base_url = "https://openrouter.ai/api/v1"
