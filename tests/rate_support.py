from fakeredis.aioredis import FakeRedis

from app.api.dependencies import get_rate_limits
from app.services.rate_limit_service import RateLimitService


def isolated_rates(app, case):
    redis = FakeRedis()
    case.addAsyncCleanup(redis.aclose)
    service = RateLimitService(redis)
    app.dependency_overrides[get_rate_limits] = lambda: service
    return service
