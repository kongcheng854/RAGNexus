import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

from fakeredis.aioredis import FakeRedis

from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError
from app.services.rate_limit_service import QUOTA_ZONE, RateLimitService, Reservation
from app.tenant.plan_presets import PLAN_PRESETS


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = FakeRedis()
        self.addAsyncCleanup(self.redis.aclose)
        self.rates = RateLimitService(self.redis)
        self.now = datetime(2026, 1, 31, 23, 59, 59, tzinfo=QUOTA_ZONE)
        self.clock = self.enterContext(patch("app.services.rate_limit_service.datetime"))
        self.clock.now.return_value = self.now

    async def test_qps_failure_and_tenant_boundaries(self):
        plan = PLAN_PRESETS["free"]
        for _ in range(3):
            with self.assertRaises(ValueError):
                async with self.rates.retrieval("one", plan):
                    raise ValueError("failed retrieve")
        with self.assertRaises(ServiceError) as denied:
            async with self.rates.retrieval("one", plan):
                self.fail("QPS must reject the fourth request")
        self.assertEqual(denied.exception.error, ErrorCode.API_RATE_LIMIT)
        self.assertEqual(await self.rates.daily_usage("one"), 0)
        async with self.rates.retrieval("two", plan):
            pass
        self.assertEqual(await self.rates.daily_usage("two"), 1)
        self.assertIn(await self.redis.ttl(f"rag:ratelimit:retrieve:one:{int(self.now.timestamp())}"), (1, 2))

    async def test_concurrent_daily_reservations_and_rollover(self):
        plan = PLAN_PRESETS["pro"]
        plan = replace(plan, limits=replace(plan.limits, retrieve_daily=2))
        results = await asyncio.gather(*(self.rates.check_retrieve("one", plan) for _ in range(10)),
                                       return_exceptions=True)
        tickets = [result for result in results if isinstance(result, Reservation)]
        self.assertEqual(len(tickets), 2)
        self.assertTrue(all(isinstance(r, Reservation) or
                            isinstance(r, ServiceError) and r.error == ErrorCode.QUOTA_EXCEEDED for r in results))
        self.assertEqual(await self.rates.daily_usage("one"), 0)
        await asyncio.gather(*(self.rates.record_retrieve_success(ticket) for ticket in tickets))
        self.assertEqual(await self.rates.daily_usage("one"), 2)
        self.assertIn(await self.redis.ttl(tickets[0].daily_key), (89999, 90000))
        self.clock.now.return_value = self.now + timedelta(seconds=1)
        self.assertEqual(await self.rates.daily_usage("one"), 0)
        async with self.rates.retrieval("one", plan):
            pass
        self.assertEqual(await self.rates.daily_usage("one"), 1)
