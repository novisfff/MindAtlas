from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from app.stats.router import router as stats_router  # noqa: E402
from app.stats.schemas import HeatmapResponse  # noqa: E402


class StatsHeatmapApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(stats_router)

        def _override_get_db():  # noqa: ANN001
            yield self.db

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.db.close()

    def test_heatmap_requires_start_and_end_together(self) -> None:
        with patch(
            "app.stats.router.StatsService.get_heatmap",
            return_value=HeatmapResponse(start_date=date(2026, 2, 1), end_date=date(2026, 2, 1), data=[]),
        ):
            resp = self.client.get("/api/stats/heatmap?startDate=2026-02-01")

        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 400)
        self.assertIn("startDate and endDate", payload["message"])

    def test_heatmap_rejects_end_before_start(self) -> None:
        with patch(
            "app.stats.router.StatsService.get_heatmap",
            return_value=HeatmapResponse(start_date=date(2026, 2, 1), end_date=date(2026, 2, 1), data=[]),
        ):
            resp = self.client.get("/api/stats/heatmap?startDate=2026-02-02&endDate=2026-02-01")

        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 400)
        self.assertIn("endDate must be >=", payload["message"])

    def test_heatmap_accepts_explicit_date_range(self) -> None:
        with patch(
            "app.stats.router.StatsService.get_heatmap",
        ) as mocked:
            mocked.return_value = HeatmapResponse(
                start_date=date(2026, 2, 1),
                end_date=date(2026, 2, 28),
                data=[],
            )
            resp = self.client.get("/api/stats/heatmap?startDate=2026-02-01&endDate=2026-02-28")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["start_date"], date(2026, 2, 1))
        self.assertEqual(kwargs["end_date"], date(2026, 2, 28))
