import asyncio
from collections.abc import Sequence
from time import perf_counter

from app.models import KnowledgeBase
from app.providers.llm.base import LLMProvider, LLMProviderError
from app.providers.query.base import QueryProcessor, QueryProcessResult


SYSTEM_PROMPT = """你是检索 query 改写器，不是问答助手。
输入：用户原话 + 知识库名称 + 知识库领域说明。
输出：一条中文短句，更贴近文档常用表述，不超过 30 字。
不编造事实，不扩展用户没问的内容，不生成答案。
只输出改写后的句子，不输出解释。"""


def domain_description(kb: KnowledgeBase) -> str:
    description = kb.description or ""
    hint = kb.settings.get("rewrite_hint") if isinstance(kb.settings, dict) else None
    if isinstance(hint, str) and hint.strip():
        description += "\n" + hint
    return description


class LLMRewriteProcessor(QueryProcessor):
    def __init__(self, llm: LLMProvider | None, timeout_ms: int):
        self.llm = llm
        self.timeout = timeout_ms / 1000

    async def process(self, result: QueryProcessResult, bases: Sequence[KnowledgeBase]) -> QueryProcessResult:
        started = perf_counter()
        result.strategy = "rewrite"
        reason = None
        if self.llm is None:
            reason = "未配置改写模型"
        else:
            try:
                async with asyncio.timeout(self.timeout):
                    text = await self.llm.chat_text(
                        system_prompt=SYSTEM_PROMPT,
                        user_payload={
                            "query": result.raw_query, "kb_name": "、".join(kb.name for kb in bases),
                            "description": "\n".join(domain_description(kb) for kb in bases),
                        },
                        temperature=0, max_tokens=128, timeout_seconds=self.timeout,
                    )
                text = text.strip()
                if not text or len(text) > 30 or len(text.splitlines()) != 1 or "\x00" in text:
                    reason = "模型未返回有效短句"
                else:
                    result.effective_query = result.search_query = text
            except TimeoutError:
                reason = "模型响应超时"
            except LLMProviderError as exc:
                reason = exc.error.message
            except Exception:
                reason = "改写模型调用失败"
        result.rewrite_latency_ms = int((perf_counter() - started) * 1000)
        result.degraded = reason is not None
        result.degraded_reason = reason
        return result
