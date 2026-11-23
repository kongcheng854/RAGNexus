from enum import IntEnum
from http import HTTPStatus


class ErrorCode(IntEnum):
    def __new__(cls, code: int, message: str, status: int):
        member = int.__new__(cls, code)
        member._value_ = code
        member.message = message
        member.status = status
        return member

    SUCCESS = (0, "success", 200)

    API_REQUEST_ERROR = (20001, "请求失败", 400)
    API_TIMEOUT = (20002, "请求超时", 408)
    API_RATE_LIMIT = (20005, "调用频率超限，请稍后再试", 429)
    PARAM_ERROR = (20004, "请求参数有误", 422)
    UNAUTHORIZED = (20010, "API Key 无效或未提供", 401)
    FORBIDDEN = (20006, "权限不足", 403)
    NOT_FOUND = (20007, "资源不存在", 404)
    METHOD_ERROR = (20008, "请求方法不受支持", 405)
    KNOWLEDGE_BASE_NOT_FOUND = (20009, "知识库不存在", 404)
    PAYLOAD_TOO_LARGE = (20012, "请求内容过大", 413)
    UNSUPPORTED_MEDIA_TYPE = (20011, "请求内容格式不受支持", 415)

    FEATURE_NOT_ALLOWED = (20013, "当前套餐不支持所选检索预设或功能", 403)
    QUOTA_EXCEEDED = (20014, "已达到当前套餐配额", 429)

    FEEDBACK_UNAVAILABLE = (20020, "反馈暂时无法保存，请检查 Langfuse 服务", 503)
    FEEDBACK_TRACE_MISMATCH = (20021, "检索记录与追踪标识不匹配或无权访问", 403)
    DOCUMENT_PARSE_FAILED = (20023, "文档解析失败", 422)
    FEEDBACK_INVALID_SCORE = (20022, "评分必须是 1 至 5 的整数", 422)

    DB_ERROR = (30001, "数据库操作失败", 500)
    DATA_DUPLICATE = (30002, "数据已存在", 409)
    DATA_CONFLICT = (30003, "数据与当前状态冲突", 409)
    DB_UNAVAILABLE = (30004, "存储服务暂不可用，请稍后再试", 503)
    DB_TIMEOUT = (30005, "存储服务响应超时，请稍后再试", 503)
    KEYWORD_SEARCH_ERROR = (30006, "关键词索引服务暂不可用", 503)
    RETRIEVAL_FAILED = (30007, "检索暂时不可用，请稍后再试", 503)

    LLM_ERROR = (40000, "模型调用失败", 502)
    LLM_TIMEOUT = (40001, "模型响应超时", 504)
    LLM_NO_RESPONSE = (40002, "模型未返回有效结果", 502)
    LLM_CONTENT_VIOLATION = (40003, "模型服务拒绝处理该内容", 422)
    LLM_TOKEN_LIMIT = (40004, "输入超过模型长度限制", 422)
    LLM_RATE_LIMIT = (40005, "模型服务调用频率超限，请稍后再试", 429)
    LLM_MODEL_ERROR = (40006, "模型不存在或暂不可用", 502)
    LLM_AUTH_ERROR = (40007, "模型服务暂不可用", 502)

    SERVER_ERROR = (50000, "服务暂时无法处理请求", 500)
    SYSTEM_BUSY = (50001, "系统繁忙，请稍后再试", 503)
    INDEXING_FAILED = (50002, "文档索引失败", 500)


HTTP_ERROR_CODES = {
    HTTPStatus.BAD_REQUEST: ErrorCode.API_REQUEST_ERROR,
    HTTPStatus.UNAUTHORIZED: ErrorCode.UNAUTHORIZED,
    HTTPStatus.FORBIDDEN: ErrorCode.FORBIDDEN,
    HTTPStatus.NOT_FOUND: ErrorCode.NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: ErrorCode.METHOD_ERROR,
    HTTPStatus.REQUEST_TIMEOUT: ErrorCode.API_TIMEOUT,
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: ErrorCode.PAYLOAD_TOO_LARGE,
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    HTTPStatus.UNPROCESSABLE_ENTITY: ErrorCode.PARAM_ERROR,
    HTTPStatus.TOO_MANY_REQUESTS: ErrorCode.API_RATE_LIMIT,
    HTTPStatus.SERVICE_UNAVAILABLE: ErrorCode.SYSTEM_BUSY,
}
