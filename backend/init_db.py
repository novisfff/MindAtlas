"""
Database initialization script
Drops all tables and recreates them with proper schema
"""
from datetime import datetime

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Base
from app.system_settings.initialization_defaults_loader import (
    load_initialization_entry_type_defaults,
    load_initialization_relation_type_defaults,
)
from app.system_settings.service import get_default_system_locale

# Import all models
from app.entry_type.models import EntryType
from app.tag.models import Tag
from app.entry.models import Entry, entry_tag
from app.relation.models import Relation, RelationType
from app.attachment.models import Attachment


def _seed_entry_types(db: Session, now: datetime) -> None:
    locale = get_default_system_locale()
    default_entry_types = [
        {
            "code": item.code,
            "name": item.name,
            "description": item.description,
            "color": item.color,
            "icon": item.icon,
            "graph_enabled": item.graph_enabled,
            "ai_enabled": item.ai_enabled,
            "enabled": item.enabled,
        }
        for item in load_initialization_entry_type_defaults(locale)
    ]

    print("\nSeeding default EntryType data (7 rows)...")
    codes = [item["code"] for item in default_entry_types]
    existing_codes = set(
        db.execute(select(EntryType.code).where(EntryType.code.in_(codes))).scalars().all()
    )

    inserted = 0
    skipped = 0
    for item in default_entry_types:
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
    locale = get_default_system_locale()
    default_relation_types = [
        {
            "code": item.code,
            "name": item.name,
            "inverse_name": item.inverse_name,
            "description": item.description,
            "color": item.color,
            "directed": item.directed,
            "enabled": item.enabled,
        }
        for item in load_initialization_relation_type_defaults(locale)
    ]

    print("\nSeeding default RelationType data (5 rows)...")
    codes = [item["code"] for item in default_relation_types]
    existing_codes = set(
        db.execute(select(RelationType.code).where(RelationType.code.in_(codes))).scalars().all()
    )

    inserted = 0
    skipped = 0
    for item in default_relation_types:
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
