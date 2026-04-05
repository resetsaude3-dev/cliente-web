import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# pega do Render (PostgreSQL)
DATABASE_URL = os.getenv("DATABASE_URL")

# fallback (caso rode local)
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./clientes.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()