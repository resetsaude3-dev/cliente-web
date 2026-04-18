from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# 🔥 Pega do Render (produção)
DATABASE_URL = os.getenv("DATABASE_URL")

# 🔁 Se não tiver (rodando local)
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./app/clientes.db"

# ⚙️ Configuração do engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

# 📦 Sessão do banco
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# 🧱 Base dos modelos
Base = declarative_base()