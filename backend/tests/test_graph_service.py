from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class GraphServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402
        from app.relation.models import Relation, RelationType  # noqa: E402

        t_on = EntryType(code="on", name="On", color="#0", graph_enabled=True, ai_enabled=True, enabled=True)
        t_off = EntryType(code="off", name="Off", color="#f", graph_enabled=False, ai_enabled=True, enabled=True)
        self.db.add_all([t_on, t_off])
        self.db.commit()

        self.e1 = Entry(
            title="e1",
            content=None,
            type_id=t_on.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.e2 = Entry(
            title="e2",
            content=None,
            type_id=t_on.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.e3 = Entry(
            title="e3",
            content=None,
            type_id=t_off.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add_all([self.e1, self.e2, self.e3])
        self.db.commit()

        rt = RelationType(code="ref", name="Ref", color="#1", directed=True, enabled=True)
        self.db.add(rt)
        self.db.commit()

        # One link between enabled nodes, one link involving disabled node.
        self.r1 = Relation(source_entry_id=self.e1.id, target_entry_id=self.e2.id, relation_type_id=rt.id)
        self.r2 = Relation(source_entry_id=self.e1.id, target_entry_id=self.e3.id, relation_type_id=rt.id)
        self.db.add_all([self.r1, self.r2])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_graph_filters_nodes_and_links(self) -> None:
        from app.graph.service import GraphService  # noqa: E402

        svc = GraphService(self.db)
        data = svc.get_graph_data()

        node_ids = {n.id for n in data.nodes}
        link_ids = {l.id for l in data.links}

        self.assertEqual(node_ids, {str(self.e1.id), str(self.e2.id)})
        self.assertEqual(link_ids, {str(self.r1.id)})

