from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError, raise_error
from app.tenant.plan_presets import PlanContext


QUOTA_ZONE = timezone(timedelta(hours=8))
# Reserve a daily slot before doing model work. A crashed worker's slot expires.
# Completed requests alone increment the public daily counter.
ADMIT = """
local now, qps, daily = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
if tonumber(redis.call('GET', KEYS[1]) or 0) >= qps then return 20005 end
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now)
if tonumber(redis.call('GET', KEYS[2]) or 0) + redis.call('ZCARD', KEYS[3]) >= daily then return 20014 end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], 2)
redis.call('ZADD', KEYS[3], now + 900, ARGV[4])
redis.call('EXPIRE', KEYS[3], 901)
return 0
"""

COMPLETE = """
if redis.call('ZREM', KEYS[2], ARGV[1]) == 0 then return 0 end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], 90000)
return 1
"""


@dataclass(frozen=True)
class Reservation:
    daily_key: str
    token: str

    @property
    def pending_key(self) -> str:
        return self.daily_key + ":pending"


def daily_key(tenant_id: str, now: datetime) -> str:
    return f"rag:quota:retrieve:daily:{tenant_id}:{now:%Y%m%d}"


class RateLimitService:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def daily_usage(self, tenant_id: str) -> int:
        try:
            return int(await self.redis.get(daily_key(tenant_id, datetime.now(QUOTA_ZONE))) or 0)
        except RedisError as exc:
            raise ServiceError(ErrorCode.SYSTEM_BUSY) from exc

    async def check_retrieve(self, tenant_id: str, plan: PlanContext) -> Reservation:
        now = datetime.now(QUOTA_ZONE)
        second = int(now.timestamp())
        ticket = Reservation(daily_key(tenant_id, now), uuid4().hex)
        try:
            code = await self.redis.eval(ADMIT, 3, f"rag:ratelimit:retrieve:{tenant_id}:{second}",
                                         ticket.daily_key, ticket.pending_key, second,
                                         plan.limits.retrieve_qps, plan.limits.retrieve_daily, ticket.token)
        except RedisError as exc:
            raise ServiceError(ErrorCode.SYSTEM_BUSY) from exc
        if code == ErrorCode.API_RATE_LIMIT:
            raise_error(ErrorCode.API_RATE_LIMIT)
        if code == ErrorCode.QUOTA_EXCEEDED:
            raise_error(ErrorCode.QUOTA_EXCEEDED, detail=f"已达到每日检索上限（{plan.limits.retrieve_daily} 次）")
        return ticket

    async def record_retrieve_success(self, ticket: Reservation) -> None:
        try:
            charged = await self.redis.eval(COMPLETE, 2, ticket.daily_key, ticket.pending_key, ticket.token)
        except RedisError as exc:
            raise ServiceError(ErrorCode.SYSTEM_BUSY) from exc
        if not charged:
            raise_error(ErrorCode.API_TIMEOUT)

    @asynccontextmanager
    async def retrieval(self, tenant_id: str, plan: PlanContext):
        ticket = await self.check_retrieve(tenant_id, plan)
        try:
            yield
        except BaseException:
            try:
                await self.redis.zrem(ticket.pending_key, ticket.token)
            except RedisError:
                pass  # The lease expires even when Redis is temporarily unavailable.
            raise
        await self.record_retrieve_success(ticket)
