OPENAI_COMPATIBLE_PROVIDERS = {
    "alibaba",
    "alibaba-coding-plan",
    "arcee",
    "azure-foundry",
    "custom",
    "deepseek",
    "gmi",
    "huggingface",
    "kilocode",
    "kimi-coding",
    "kimi-coding-cn",
    "nous",
    "novita",
    "nvidia",
    "ollama-cloud",
    "opencode-zen",
    "opencode-go",
    "openrouter",
    "qwen-oauth",
    "stepfun",
    "xai",
    "xiaomi",
    "zai",
}

ANTHROPIC_COMPATIBLE_PROVIDERS = {
    "anthropic",
    "minimax",
    "minimax-cn",
    "minimax-oauth",
}


class UnsupportedGatewayProvider(ValueError):
    pass


def gateway_provider_kind(provider_slug: str) -> str:
    if provider_slug in ANTHROPIC_COMPATIBLE_PROVIDERS:
        return "anthropic"
    if provider_slug in OPENAI_COMPATIBLE_PROVIDERS:
        return "openai_compatible"
    raise UnsupportedGatewayProvider(
        f"provider {provider_slug} has no Model Gateway adapter"
    )
