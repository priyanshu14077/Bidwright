from sqlalchemy import create_engine

from sog.config import settings

engine = create_engine(settings.sog_database_url, pool_pre_ping=True)
