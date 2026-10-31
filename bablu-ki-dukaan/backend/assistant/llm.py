"""All model access in one place. Every model name comes from settings (.env), so when Groq retires a
model you change one line, not the code."""
from django.conf import settings
from langchain_groq import ChatGroq


class AssistantNotConfigured(Exception):
    pass


def _groq(model, **kw):
    if not settings.GROQ_API_KEY:
        raise AssistantNotConfigured("GROQ_API_KEY is missing in backend/.env")
    return ChatGroq(model=model, api_key=settings.GROQ_API_KEY, max_retries=1, timeout=60,
                    temperature=kw.pop("temperature", 0), **kw)


def _openrouter():
    """Optional 3rd free provider, used only if Groq fails. Needs: pip install langchain-openai"""
    if not settings.OPENROUTER_API_KEY:
        return None
    try:
        from langchain_openai import ChatOpenAI # type: ignore
    except ImportError:
        return None
    return ChatOpenAI(model=settings.OPENROUTER_MODEL, api_key=settings.OPENROUTER_API_KEY,
                      base_url="https://openrouter.ai/api/v1", temperature=0.1, timeout=60, max_retries=1)


def fast_llm():
    """Small and quick: used for routing and small talk."""
    return _groq(settings.GROQ_MODEL_FAST, max_tokens=1500)


def agent_llm(tools, *, allow_tools=True):
    """The main brain. Tries the main model, then each fallback (a different model family), then OpenRouter."""
    def make(model_or_llm):
        llm = model_or_llm if not isinstance(model_or_llm, str) else _groq(model_or_llm, temperature=0.1)
        if not tools:
            return llm
        return llm.bind_tools(tools, tool_choice="auto" if allow_tools else "none")

    primary = make(settings.GROQ_MODEL_MAIN)
    backups = [make(m) for m in settings.GROQ_MODEL_FALLBACKS if m != settings.GROQ_MODEL_MAIN]
    extra = _openrouter()
    if extra is not None:
        backups.append(make(extra))
    return primary.with_fallbacks(backups) if backups else primary


def text_of(message):
    """Message content as plain text (some models return a list of parts)."""
    c = getattr(message, "content", "")
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return c or ""
