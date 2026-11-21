import httpx

from app.core.error_codes import ErrorCode


def upstream_error_code(response: httpx.Response) -> ErrorCode:
    match response.status_code:
        case 401 | 403:
            return ErrorCode.LLM_AUTH_ERROR
        case 404:
            return ErrorCode.LLM_MODEL_ERROR
        case 408 | 504:
            return ErrorCode.LLM_TIMEOUT
        case 429:
            return ErrorCode.LLM_RATE_LIMIT

    if response.status_code in (400, 422):
        try:
            payload = response.json()
        except ValueError:
            return ErrorCode.LLM_ERROR
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            for key in ("code", "type"):
                match error.get(key):
                    case "context_length_exceeded" | "max_tokens_exceeded":
                        return ErrorCode.LLM_TOKEN_LIMIT
                    case "content_filter" | "content_policy_violation":
                        return ErrorCode.LLM_CONTENT_VIOLATION
                    case "model_not_found":
                        return ErrorCode.LLM_MODEL_ERROR
    return ErrorCode.LLM_ERROR
