from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    telefone = Column(String, nullable=True)
    observacao = Column(String, nullable=True)

    contas = relationship("Conta", back_populates="cliente")


class Conta(Base):
    __tablename__ = "contas"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True)

    servico = Column(String, nullable=False)
    login = Column(String, nullable=True)
    senha = Column(String, nullable=True)
    perfil = Column(String, nullable=True)
    valor = Column(Float, default=0)
    data_vencimento = Column(Date, nullable=False)
    status = Column(String, default="pendente")
    observacao = Column(String, nullable=True)
    motivo_manutencao = Column(String, nullable=True)

    cliente = relationship("Cliente", back_populates="contas")