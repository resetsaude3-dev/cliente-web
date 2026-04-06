from datetime import date, datetime, timedelta
from urllib.parse import quote
import json
from io import BytesIO
import requests
import os

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import joinedload

from app.database import Base, engine, SessionLocal
from app.models import Usuario, Cliente, Conta

app = FastAPI()

Base.metadata.create_all(bind=engine)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


# =========================
# 🔐 LOGIN (simplificado)
# =========================
def exigir_login(request: Request):
    if not request.cookies.get("usuario"):
        return RedirectResponse(url="/login", status_code=303)
    return None


# =========================
# 📲 ENVIAR COBRANÇA OFICIAL
# =========================
@app.get("/enviar-cobranca-oficial/{conta_id}")
def enviar_cobranca_oficial(conta_id: int, request: Request):

    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    conta = db.query(Conta)\
        .options(joinedload(Conta.cliente))\
        .filter(Conta.id == conta_id)\
        .first()

    # 🔎 DEBUG de existência
    if not conta:
        db.close()
        return HTMLResponse("❌ Conta não encontrada", status_code=404)

    if not conta.cliente:
        db.close()
        return HTMLResponse("❌ Conta sem cliente", status_code=400)

    # =========================
    # 🔑 VARIÁVEIS DO RENDER
    # =========================
    token = os.getenv("WHATSAPP_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

    # 🔎 DEBUG
    print("TOKEN:", token)
    print("PHONE_ID:", phone_id)

    # =========================
    # 📞 TELEFONE
    # =========================
    telefone = "".join(filter(str.isdigit, conta.cliente.telefone or ""))

    if not telefone.startswith("55"):
        telefone = "55" + telefone

    print("TELEFONE FINAL:", telefone)

    # =========================
    # 💬 MENSAGEM
    # =========================
    mensagem = (
        f"Olá {conta.cliente.nome}, tudo bem?\n\n"
        f"Serviço: {conta.servico}\n"
        f"Usuário: {conta.login or '-'}\n"
        f"Valor: R$ {float(conta.valor or 0):.2f}\n\n"
        f"Por favor, regularize o pagamento."
    )

    print("MENSAGEM:", mensagem)

    # =========================
    # 🌐 REQUISIÇÃO
    # =========================
    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "text",
        "text": {
            "body": mensagem
        }
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)

        print("STATUS:", response.status_code)
        print("RESPOSTA:", response.text)

        if response.status_code in [200, 201]:
            conta.status = "cobrado"
            db.commit()

        else:
            print("❌ ERRO NO ENVIO")

    except Exception as e:
        print("❌ EXCEÇÃO:", str(e))

    db.close()

    return RedirectResponse(url="/cobrados", status_code=303)


# =========================
# 📋 COBRADOS
# =========================
@app.get("/cobrados", response_class=HTMLResponse)
def cobrados(request: Request):
    db = SessionLocal()

    contas = db.query(Conta)\
        .options(joinedload(Conta.cliente))\
        .filter(Conta.status == "cobrado")\
        .all()

    db.close()

    return templates.TemplateResponse(
        "cobrados.html",
        {"request": request, "contas": contas}
    )