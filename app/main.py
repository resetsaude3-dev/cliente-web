
from datetime import date
import os
import requests

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import joinedload
from passlib.context import CryptContext

from app.database import Base, engine, SessionLocal
from app.models import Usuario, Cliente, Conta

print("🔥 APP INICIOU 🔥")

app = FastAPI()

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# =========================
# STARTUP
# =========================
@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    if not db.query(Usuario).first():
        db.add(Usuario(username="admin", senha=pwd_context.hash("123456")))
        db.commit()
    db.close()


# =========================
# AUTH
# =========================
def usuario_logado(request: Request):
    return request.cookies.get("usuario")


def exigir_login(request: Request):
    if not usuario_logado(request):
        return RedirectResponse("/login", status_code=303)
    return None


# =========================
# LOGIN
# =========================
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login(request: Request, username: str = Form(...), senha: str = Form(...)):
    db = SessionLocal()
    user = db.query(Usuario).filter(Usuario.username == username).first()
    db.close()

    if not user or not pwd_context.verify(senha, user.senha):
        return templates.TemplateResponse("login.html", {"request": request, "erro": "Login inválido"})

    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("usuario", user.username)
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("usuario")
    return response


# =========================
# HOME
# =========================
@app.get("/")
def home():
    return RedirectResponse("/dashboard")


# =========================
# DASHBOARD
# =========================
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    hoje = date.today()

    dados = {
        "total_clientes": db.query(Cliente).count(),
        "total_contas": db.query(Conta).count(),
        "pendentes": db.query(Conta).filter(Conta.status == "pendente").count(),
        "vencidas": db.query(Conta).filter(
            Conta.status == "pendente",
            Conta.data_vencimento < hoje
        ).count()
    }

    db.close()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "usuario": usuario_logado(request),
        **dados
    })


# =========================
# CLIENTES
# =========================
@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    clientes = db.query(Cliente).all()
    db.close()

    return templates.TemplateResponse("clientes.html", {
        "request": request,
        "clientes": clientes
    })


# =========================
# CONTAS
# =========================
@app.get("/contas", response_class=HTMLResponse)
def contas(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    contas = db.query(Conta).options(joinedload(Conta.cliente)).all()
    db.close()

    return templates.TemplateResponse("contas.html", {
        "request": request,
        "contas": contas
    })


# =========================
# ENVIO WHATSAPP (MANUAL)
# =========================
@app.get("/enviar-cobranca-oficial/{conta_id}")
def enviar_cobranca(conta_id: int):
    db = SessionLocal()

    conta = db.query(Conta).options(joinedload(Conta.cliente)).filter(Conta.id == conta_id).first()

    telefone = "55" + "".join(filter(str.isdigit, conta.cliente.telefone))

    payload = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "template",
        "template": {
            "name": "cobranca_link",
            "language": {"code": "pt_BR"},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": conta.cliente.nome},
                    {"type": "text", "text": conta.servico},
                    {"type": "text", "text": conta.login},
                    {"type": "text", "text": f"R$ {conta.valor:.2f}".replace(".", ",")},
                    {"type": "text", "text": conta.data_vencimento.strftime("%d/%m/%Y")}
                ]
            }]
        }
    }

    headers = {
        "Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}",
        "Content-Type": "application/json"
    }

    requests.post(
        f"https://graph.facebook.com/v20.0/{os.getenv('WHATSAPP_PHONE_NUMBER_ID')}/messages",
        headers=headers,
        json=payload
    )

    conta.status = "cobrado"
    db.commit()
    db.close()

    return {"ok": True}


# =========================
# ENVIO AUTOMÁTICO
# =========================
@app.get("/enviar-cobrancas-automatico")
def enviar_auto():
    db = SessionLocal()

    hoje = date.today()

    contas = db.query(Conta).options(joinedload(Conta.cliente)).filter(
        Conta.status == "pendente",
        Conta.data_vencimento <= hoje
    ).all()

    enviados = 0

    for c in contas:
        telefone = "55" + "".join(filter(str.isdigit, c.cliente.telefone))

        payload = {
            "messaging_product": "whatsapp",
            "to": telefone,
            "type": "template",
            "template": {
                "name": "cobranca_link",
                "language": {"code": "pt_BR"},
                "components": [{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": c.cliente.nome},
                        {"type": "text", "text": c.servico},
                        {"type": "text", "text": c.login},
                        {"type": "text", "text": f"R$ {c.valor:.2f}".replace(".", ",")},
                        {"type": "text", "text": c.data_vencimento.strftime("%d/%m/%Y")}
                    ]
                }]
            }
        }

        headers = {
            "Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}",
            "Content-Type": "application/json"
        }

        r = requests.post(
            f"https://graph.facebook.com/v20.0/{os.getenv('WHATSAPP_PHONE_NUMBER_ID')}/messages",
            headers=headers,
            json=payload
        )

        if r.ok:
            c.status = "cobrado"
            enviados += 1

    db.commit()
    db.close()

    return {"ok": True, "enviados": enviados}

