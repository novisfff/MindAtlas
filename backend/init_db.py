"""
Database initialization script
Drops all tables and recreates them with proper schema
"""
from datetime import datetime

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Base

# Import all models
from app.entry_type.models import EntryType
from app.tag.models import Tag
from app.entry.models import Entry, entry_tag
from app.relation.models import Relation, RelationType
from app.attachment.models import Attachment


DEFAULT_ENTRY_TYPES: list[dict] = [
    {
        "code": "KNOWLEDGE",
        "name": "知识",
        "description": "学习的知识点",
        "color": "#3B82F6",
        "icon": "book",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "PROJECT",
        "name": "项目",
        "description": "参与的项目",
        "color": "#10B981",
        "icon": "folder",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "COMPETITION",
        "name": "比赛",
        "description": "参加的比赛",
        "color": "#F59E0B",
        "icon": "trophy",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "EXPERIENCE",
        "name": "经历",
        "description": "个人经历",
        "color": "#8B5CF6",
        "icon": "star",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "ACHIEVEMENT",
        "name": "成果",
        "description": "取得的成果",
        "color": "#EF4444",
        "icon": "award",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "TECHNOLOGY",
        "name": "技术",
        "description": "掌握的技术",
        "color": "#06B6D4",
        "icon": "code",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "DOCUMENT",
        "name": "资料",
        "description": "收集的资料",
        "color": "#6B7280",
        "icon": "file",
        "graph_enabled": True,
        "ai_enabled": False,
        "enabled": True,
    },
]

DEFAULT_RELATION_TYPES: list[dict] = [
    {
        "code": "BELONGS_TO",
        "name": "属于",
        "inverse_name": "包含",
        "description": "表示从属关系",
        "color": "#3B82F6",
        "directed": True,
        "enabled": True,
    },
    {
        "code": "USES",
        "name": "使用",
        "inverse_name": "被使用",
        "description": "表示使用关系",
        "color": "#10B981",
        "directed": True,
        "enabled": True,
    },
    {
        "code": "PARTICIPATES",
        "name": "参与",
        "inverse_name": "参与者",
        "description": "表示参与关系",
        "color": "#F59E0B",
        "directed": True,
        "enabled": True,
    },
    {
        "code": "RELATES_TO",
        "name": "关联",
        "inverse_name": "关联",
        "description": "表示一般关联",
        "color": "#8B5CF6",
        "directed": False,
        "enabled": True,
    },
    {
        "code": "DERIVES_FROM",
        "name": "派生自",
        "inverse_name": "派生出",
        "description": "表示派生关系",
        "color": "#EF4444",
        "directed": True,
        "enabled": True,
    },
]


def _seed_entry_types(db: Session, now: datetime) -> None:
    print("\nSeeding default EntryType data (7 rows)...")
    codes = [item["code"] for item in DEFAULT_ENTRY_TYPES]
    existing_codes = set(
        db.execute(select(EntryType.code).where(EntryType.code.in_(codes))).scalars().all()
    )

    inserted = 0
    skipped = 0
    for item in DEFAULT_ENTRY_TYPES:
        code = item["code"]
        if code in existing_codes:
            print(f"  - EntryType {code}: exists, skip")
            skipped += 1
            continue

        db.add(EntryType(**item, created_at=now, updated_at=now))
        print(f"  - EntryType {code}: inserted")
        inserted += 1

    print(f"EntryType seeding done (inserted={inserted}, skipped={skipped}).")


def _seed_relation_types(db: Session, now: datetime) -> None:
    print("\nSeeding default RelationType data (5 rows)...")
    codes = [item["code"] for item in DEFAULT_RELATION_TYPES]
    existing_codes = set(
        db.execute(select(RelationType.code).where(RelationType.code.in_(codes))).scalars().all()
    )

    inserted = 0
    skipped = 0
    for item in DEFAULT_RELATION_TYPES:
        code = item["code"]
        if code in existing_codes:
            print(f"  - RelationType {code}: exists, skip")
            skipped += 1
            continue

        db.add(RelationType(**item, created_at=now, updated_at=now))
        print(f"  - RelationType {code}: inserted")
        inserted += 1

    print(f"RelationType seeding done (inserted={inserted}, skipped={skipped}).")


def init_db():
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_database_uri())

    # Drop all tables
    print("Dropping all tables...")
    Base.metadata.drop_all(bind=engine)

    # Create all tables
    print("Creating all tables...")
    Base.metadata.create_all(bind=engine)

    # Seed default data
    print("Seeding default data...")
    SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        future=True,
    )
    db = SessionLocal()
    try:
        now = datetime.now()
        _seed_entry_types(db, now)
        _seed_relation_types(db, now)
        db.commit()
        print("\n✅ Default data seeded successfully!")
    except Exception:
        db.rollback()
        print("\n❌ Failed to seed default data (rolled back).")
        raise
    finally:
        db.close()

    print("\n✅ Database initialized successfully!")

    # Show created tables
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"\nCreated {len(tables)} tables:")
    for table in tables:
        print(f"  - {table}")

if __name__ == "__main__":
    init_db()
