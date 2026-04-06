from datetime import date, datetime, timedelta

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import joinedload
from passlib.context import CryptContext
from urllib.parse import quote

from app.database import Base, engine, SessionLocal
from app.models import Usuario, Cliente, Conta

import json
from io import BytesIO

app = FastAPI()

Base.metadata.create_all(bind=engine)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# =========================
# LOGIN
# =========================
def usuario_logado(request: Request):
    return request.cookies.get("usuario")


def exigir_login(request: Request):
    if not usuario_logado(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


# =========================
# COBRANÇAS (FUNÇÃO PRINCIPAL)
# =========================
def montar_cobrancas_pendentes(db):
    hoje = date.today()

    contas = db.query(Conta).options(
        joinedload(Conta.cliente)
    ).filter(
        Conta.status == "pendente",
        Conta.cliente_id.isnot(None),
        Conta.data_vencimento <= hoje
    ).order_by(Conta.data_vencimento.asc()).all()

    agrupadas = {}

    for conta in contas:
        chave = (conta.cliente_id, conta.data_vencimento.isoformat())

        nome_cliente = conta.cliente.nome if conta.cliente else "Sem nome"
        telefone_cliente = conta.cliente.telefone if conta.cliente else ""

        if chave not in agrupadas:
            agrupadas[chave] = {
                "cliente_id": conta.cliente_id,
                "cliente_nome": nome_cliente,
                "telefone": telefone_cliente,
                "data_vencimento": conta.data_vencimento.isoformat(),
                "contas_detalhes": [],
                "valor_total": 0.0
            }

        agrupadas[chave]["contas_detalhes"].append({
            "id": conta.id,
            "servico": conta.servico,
            "login": conta.login or "",
            "perfil": conta.perfil or "",
            "valor": float(conta.valor or 0)
        })

        agrupadas[chave]["valor_total"] += float(conta.valor or 0)

    resultado = []

    for item in agrupadas.values():
        linhas = []

        for c in item["contas_detalhes"]:
            linha = f"{c['servico']}"

            if c["login"]:
                linha += f"\nUsuário: {c['login']}"

            if c["perfil"]:
                linha += f"\nPerfil: {c['perfil']}"

            linhas.append(linha)

        mensagem = (
            f"Olá {item['cliente_nome']}, tudo bem?\n\n"
            f"Os seguintes serviços estão vencidos:\n\n"
            + "\n\n".join(linhas)
            + f"\n\nValor total: R$ {item['valor_total']:.2f}\n\n"
            f"Me avisa após o pagamento."
        )

        telefone = "".join(filter(str.isdigit, item["telefone"] or ""))

        if telefone and not telefone.startswith("55"):
            telefone = "55" + telefone

        item["whatsapp"] = f"https://wa.me/{telefone}?text={quote(mensagem)}"
        resultado.append(item)

    return resultado


# =========================
# PÁGINAS
# =========================
@app.get("/cobrancas", response_class=HTMLResponse)
def pagina_cobrancas(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    dados = montar_cobrancas_pendentes(db)
    db.close()

    return templates.TemplateResponse(
        "cobrancas.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "cobrancas_pendentes": dados
        }
    )


@app.get("/cobrados", response_class=HTMLResponse)
def cobrados(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    contas = db.query(Conta).options(
        joinedload(Conta.cliente)
    ).filter(
        Conta.status == "cobrado"
    ).all()

    db.close()

    return templates.TemplateResponse(
        "cobrados.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "contas": contas
        }
    )


# =========================
# COBRAR CLIENTE
# =========================
@app.get("/cobrar/{id}")
def cobrar_cliente(id: int):
    db = SessionLocal()

    conta = db.query(Conta).filter(Conta.id == id).first()

    if not conta or not conta.cliente:
        db.close()
        return RedirectResponse("/contas", status_code=303)

    conta.status = "cobrado"
    db.commit()

    mensagem = f"""
Olá {conta.cliente.nome},

Serviço: {conta.servico}
Usuário: {conta.login}

Valor: R$ {conta.valor}

Por favor, regularize.
"""

    telefone = "".join(filter(str.isdigit, conta.cliente.telefone or ""))

    if not telefone.startswith("55"):
        telefone = "55" + telefone

    db.close()

    return RedirectResponse(
        f"https://wa.me/{telefone}?text={quote(mensagem)}",
        status_code=302
    )