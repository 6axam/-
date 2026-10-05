import base64
import httpx
import pytest
from app.images import OpenAIImageProvider, OpenRouterImageProvider

async def test_openrouter_image_provider_uses_dedicated_images_endpoint():
    async def handler(request):
        assert request.url.path == "/api/v1/images"
        import json
        body=json.loads(request.content)
        assert body == {"model":"model", "prompt":"cat"}
        return httpx.Response(200, json={"data":[{"b64_json":base64.b64encode(b"png").decode()}],"usage":{"cost":.02}})
    provider=OpenRouterImageProvider("key","model","https://openrouter.ai/api/v1")
    # Injecting transport without exposing it in production keeps the actual client simple.
    import app.images as images
    original=images.httpx.AsyncClient
    class Client(original):
        def __init__(self,*args,**kwargs): kwargs["transport"]=httpx.MockTransport(handler); super().__init__(*args,**kwargs)
    images.httpx.AsyncClient=Client
    try: result=await provider.generate("cat")
    finally: images.httpx.AsyncClient=original
    assert result.data == b"png" and result.cost == .02

async def test_openrouter_reference_payload_is_typed_image_url():
    async def handler(request):
        import json
        ref=json.loads(request.content)["input_references"][0]
        assert ref["type"] == "image_url" and ref["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(b"png").decode()}]})
    provider=OpenRouterImageProvider("key","bytedance-seed/seedream-4.5","https://openrouter.ai/api/v1")
    import app.images as images
    original=images.httpx.AsyncClient
    class Client(original):
        def __init__(self,*args,**kwargs): kwargs["transport"]=httpx.MockTransport(handler); super().__init__(*args,**kwargs)
    images.httpx.AsyncClient=Client
    try: await provider.generate("short",[b"reference"])
    finally: images.httpx.AsyncClient=original


async def test_openai_compatible_provider_rejects_unsupported_references_before_http():
    provider = OpenAIImageProvider("key", "model", "https://example.invalid/v1")
    assert provider.supports_references is False
    with pytest.raises(RuntimeError, match="does not support identity references"):
        await provider.generate("selfie", [b"reference"])


def test_openrouter_declares_reference_support():
    assert OpenRouterImageProvider.supports_references is True
