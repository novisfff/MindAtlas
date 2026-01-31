from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, text, union_all
from sqlalchemy.orm import Session

from app.entry.models import Entry, TimeMode
from app.entry_type.models import EntryType
from app.relation.models import Relation
from app.stats.schemas import (
    DashboardStats,
    DayEntriesResponse,
    DayEntry,
    HeatmapDay,
    HeatmapResponse,
    HotnessResponse,
    TagHotness,
    TypeCount,
    TypeHotness,
    WeeklyMetrics,
)
from app.tag.models import Tag


class StatsService:
    def __init__(self, db: Session):
        self.db = db

    def get_dashboard_stats(self) -> DashboardStats:
        # Count total entries
        total_entries = self.db.query(func.count(Entry.id)).scalar() or 0

        # Count total tags
        total_tags = self.db.query(func.count(Tag.id)).scalar() or 0

        # Count total relations
        total_relations = self.db.query(func.count(Relation.id)).scalar() or 0

        # Entries by type
        counts = dict(
            self.db.query(Entry.type_id, func.count(Entry.id))
            .group_by(Entry.type_id)
            .all()
        )
        entry_types = self.db.query(EntryType).all()
        entries_by_type = [
            TypeCount(
                type_id=str(et.id),
                type_name=et.name,
                type_color=et.color,
                count=int(counts.get(et.id, 0) or 0),
            )
            for et in entry_types
        ]

        return DashboardStats(
            total_entries=total_entries,
            total_tags=total_tags,
            total_relations=total_relations,
            entries_by_type=entries_by_type,
        )

    def get_heatmap(
        self,
        months: int = 3,
        type_id: UUID | None = None,
    ) -> HeatmapResponse:
        """
        Heatmap aggregation using difference array + prefix sum approach.

        Counts:
        - point_count: POINT entries by date
        - range_start_count: RANGE entries starting on that date
        - range_active_count: RANGE entries covering that date (via prefix sum)
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=months * 30)

        type_filter = ""
        params: dict = {"start": start_date, "end": end_date}
        if type_id:
            type_filter = "AND type_id = :type_id"
            params["type_id"] = str(type_id)

        sql = text(f"""
            WITH
            days AS (
                SELECT generate_series(
                    (:start)::date,
                    ((:end)::date - interval '1 day')::date,
                    interval '1 day'
                )::date AS d
            ),
            point AS (
                SELECT
                    (time_at AT TIME ZONE 'UTC')::date AS d,
                    count(*)::int AS point_count
                FROM entry
                WHERE time_mode = 'POINT' AND time_at IS NOT NULL
                  AND time_at >= :start AND time_at < :end
                  {type_filter}
                GROUP BY (time_at AT TIME ZONE 'UTC')::date
            ),
            range_start AS (
                SELECT
                    (time_from AT TIME ZONE 'UTC')::date AS d,
                    count(*)::int AS range_start_count
                FROM entry
                WHERE time_mode = 'RANGE'
                  AND time_from IS NOT NULL AND time_to IS NOT NULL
                  AND time_from >= :start AND time_from < :end
                  {type_filter}
                GROUP BY (time_from AT TIME ZONE 'UTC')::date
            ),
            range_seed AS (
                SELECT count(*)::int AS seed_count
                FROM entry
                WHERE time_mode = 'RANGE'
                  AND time_from IS NOT NULL AND time_to IS NOT NULL
                  AND (time_from AT TIME ZONE 'UTC')::date < (:start)::date
                  AND (time_to AT TIME ZONE 'UTC')::date >= (:start)::date
                  {type_filter}
            ),
            range_clipped AS (
                SELECT
                    greatest((time_from AT TIME ZONE 'UTC')::date, (:start)::date) AS s,
                    least((time_to AT TIME ZONE 'UTC')::date, ((:end)::date - interval '1 day')::date) AS e
                FROM entry
                WHERE time_mode = 'RANGE'
                  AND time_from IS NOT NULL AND time_to IS NOT NULL
                  AND (time_to AT TIME ZONE 'UTC')::date >= (:start)::date
                  AND (time_from AT TIME ZONE 'UTC')::date < (:end)::date
                  {type_filter}
            ),
            range_deltas AS (
                SELECT s AS d, 1 AS delta
                FROM range_clipped
                WHERE s <= e
                UNION ALL
                SELECT (e + 1) AS d, -1 AS delta
                FROM range_clipped
                WHERE s <= e AND (e + 1) < (:end)::date
            ),
            range_delta_by_day AS (
                SELECT d, sum(delta)::int AS delta
                FROM range_deltas
                GROUP BY d
            ),
            range_active AS (
                SELECT
                    days.d,
                    (SELECT seed_count FROM range_seed)
                      + sum(coalesce(range_delta_by_day.delta, 0)) OVER (
                          ORDER BY days.d
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                        ) AS range_active_count
                FROM days
                LEFT JOIN range_delta_by_day ON range_delta_by_day.d = days.d
            ),
            combined AS (
                SELECT
                    days.d AS date,
                    coalesce(point.point_count, 0)::int AS point_count,
                    coalesce(range_start.range_start_count, 0)::int AS range_start_count,
                    coalesce(range_active.range_active_count, 0)::int AS range_active_count
                FROM days
                LEFT JOIN point ON point.d = days.d
                LEFT JOIN range_start ON range_start.d = days.d
                LEFT JOIN range_active ON range_active.d = days.d
            )
            SELECT
                date,
                (point_count + range_active_count)::int AS count,
                point_count,
                range_start_count,
                range_active_count
            FROM combined
            WHERE (point_count + range_active_count) > 0
            ORDER BY date
        """)

        rows = self.db.execute(sql, params).fetchall()

        data = [
            HeatmapDay(
                date=row.date,
                count=row.count,
                point_count=row.point_count,
                range_start_count=row.range_start_count,
                range_active_count=row.range_active_count,
                entries=[],
            )
            for row in rows
        ]

        return HeatmapResponse(
            start_date=start_date,
            end_date=end_date,
            data=data,
        )

    def get_day_entries(
        self,
        target_date: date,
        type_id: UUID | None = None,
        limit: int = 50,
    ) -> DayEntriesResponse:
        """
        Return all entries that cover the given UTC date.

        Coverage:
        - POINT: time_at date == target_date
        - RANGE: time_from <= target_date <= time_to

        cover_kind:
        - POINT: TimeMode.POINT entries
        - RANGE_START: RANGE entries starting on target_date
        - RANGE_SPAN: RANGE entries covering but not starting on target_date
        """
        type_filter = ""
        params: dict = {"d": target_date, "limit": limit}
        if type_id:
            type_filter = "AND type_id = :type_id"
            params["type_id"] = str(type_id)

        sql = text(f"""
            SELECT
                e.id::text AS id,
                e.title AS title,
                e.time_mode AS time_mode,
                e.time_at AS time_at,
                e.time_from AS time_from,
                e.time_to AS time_to,
                et.color AS type_color,
                CASE
                    WHEN e.time_mode = 'POINT' THEN 'POINT'
                    WHEN (e.time_from AT TIME ZONE 'UTC')::date = (:d)::date THEN 'RANGE_START'
                    ELSE 'RANGE_SPAN'
                END AS cover_kind
            FROM entry e
            LEFT JOIN entry_type et ON et.id = e.type_id
            WHERE (
                (e.time_mode = 'POINT'
                    AND e.time_at IS NOT NULL
                    AND (e.time_at AT TIME ZONE 'UTC')::date = (:d)::date
                )
                OR
                (e.time_mode = 'RANGE'
                    AND e.time_from IS NOT NULL AND e.time_to IS NOT NULL
                    AND (e.time_from AT TIME ZONE 'UTC')::date <= (:d)::date
                    AND (e.time_to AT TIME ZONE 'UTC')::date >= (:d)::date
                )
            )
            {type_filter}
            ORDER BY
                CASE
                    WHEN e.time_mode = 'POINT' THEN e.time_at
                    ELSE e.time_from
                END NULLS LAST,
                e.title
            LIMIT :limit
        """)

        rows = self.db.execute(sql, params).fetchall()
        entries = [DayEntry(**dict(row._mapping)) for row in rows]
        return DayEntriesResponse(date=target_date, entries=entries)

    def get_weekly_metrics(self) -> WeeklyMetrics:
        today = datetime.now(timezone.utc).date()
        # UTC week: Monday 00:00 to Sunday 23:59 (inclusive)
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        # Single query for all metrics
        sql = text("""
            WITH week_entries AS (
                SELECT id, (time_at AT TIME ZONE 'UTC')::date AS d
                FROM entry
                WHERE time_mode = 'POINT' AND time_at IS NOT NULL
                  AND (time_at AT TIME ZONE 'UTC')::date >= :week_start
                  AND (time_at AT TIME ZONE 'UTC')::date <= :week_end
                UNION ALL
                SELECT id, (time_from AT TIME ZONE 'UTC')::date AS d
                FROM entry
                WHERE time_mode = 'RANGE' AND time_from IS NOT NULL
                  AND (time_from AT TIME ZONE 'UTC')::date >= :week_start
                  AND (time_from AT TIME ZONE 'UTC')::date <= :week_end
            )
            SELECT
                (SELECT count(*) FROM week_entries) AS week_entry_count,
                (SELECT count(DISTINCT d) FROM week_entries) AS active_days,
                (SELECT count(*) FROM entry) AS total_entries,
                (SELECT count(*) FROM relation) AS total_relations
        """)

        row = self.db.execute(
            sql, {"week_start": week_start, "week_end": week_end}
        ).fetchone()

        return WeeklyMetrics(
            week_entry_count=row.week_entry_count or 0,
            active_days=row.active_days or 0,
            total_entries=row.total_entries or 0,
            total_relations=row.total_relations or 0,
            week_start=week_start,
            week_end=week_end,
        )

    def get_hotness(self) -> HotnessResponse:
        today = datetime.now(timezone.utc).date()
        window_start = today - timedelta(days=30)
        window_end = today

        # Top 5 types
        types_sql = text("""
            WITH recent AS (
                SELECT type_id FROM entry
                WHERE time_mode = 'POINT' AND time_at IS NOT NULL
                  AND (time_at AT TIME ZONE 'UTC')::date >= :start
                  AND (time_at AT TIME ZONE 'UTC')::date <= :end
                UNION ALL
                SELECT type_id FROM entry
                WHERE time_mode = 'RANGE' AND time_from IS NOT NULL
                  AND (time_from AT TIME ZONE 'UTC')::date >= :start
                  AND (time_from AT TIME ZONE 'UTC')::date <= :end
            )
            SELECT et.id, et.name, et.color, count(*) AS cnt
            FROM recent r
            JOIN entry_type et ON et.id = r.type_id
            GROUP BY et.id, et.name, et.color
            ORDER BY cnt DESC
            LIMIT 5
        """)
        type_rows = self.db.execute(
            types_sql, {"start": window_start, "end": window_end}
        ).fetchall()

        top_types = [
            TypeHotness(
                type_id=str(r.id),
                type_name=r.name,
                type_color=r.color,
                count=r.cnt,
            )
            for r in type_rows
        ]

        # Top 5 tags
        tags_sql = text("""
            WITH recent AS (
                SELECT id FROM entry
                WHERE time_mode = 'POINT' AND time_at IS NOT NULL
                  AND (time_at AT TIME ZONE 'UTC')::date >= :start
                  AND (time_at AT TIME ZONE 'UTC')::date <= :end
                UNION ALL
                SELECT id FROM entry
                WHERE time_mode = 'RANGE' AND time_from IS NOT NULL
                  AND (time_from AT TIME ZONE 'UTC')::date >= :start
                  AND (time_from AT TIME ZONE 'UTC')::date <= :end
            )
            SELECT t.id, t.name, t.color, count(*) AS cnt
            FROM recent r
            JOIN entry_tag et ON et.entry_id = r.id
            JOIN tag t ON t.id = et.tag_id
            GROUP BY t.id, t.name, t.color
            ORDER BY cnt DESC
            LIMIT 5
        """)
        tag_rows = self.db.execute(
            tags_sql, {"start": window_start, "end": window_end}
        ).fetchall()

        top_tags = [
            TagHotness(
                tag_id=str(r.id),
                tag_name=r.name,
                tag_color=r.color,
                count=r.cnt,
            )
            for r in tag_rows
        ]

        return HotnessResponse(
            top_types=top_types,
            top_tags=top_tags,
            window_start=window_start,
            window_end=window_end,
        )
