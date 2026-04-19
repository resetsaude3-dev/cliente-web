
from datetime import date
import os
import requests
import json
from io import BytesIO

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
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
# DATABASE STARTUP
# =========================
@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if not db.query(Usuario).filter(Usuario.username == "admin").first():
            db.add(Usuario(username="admin", senha=pwd_context.hash("123456")))
            db.commit()
    finally:
        db.close()


# =========================
# AUTH
# =========================
def usuario_logado(request: Request):
    return request.cookies.get("usuario")


def exigir_login(request: Request):
    if not usuario_logado(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


# =========================
# HOME
# =========================
@app.get("/")
def home():
    return RedirectResponse("/dashboard")


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
# DASHBOARD
# =========================
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    dados = {
        "total_clientes": db.query(Cliente).count(),
        "total_contas": db.query(Conta).count(),
        "pendentes": db.query(Conta).filter(Conta.status == "pendente").count(),
        "pagas": db.query(Conta).filter(Conta.status == "paga").count()
    }

    db.close()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "usuario": usuario_logado(request),
        **dados
    })


# =========================
# TESTE
# =========================
@app.get("/teste-auto")
def teste_auto():
    return {"status": "ok"}


# =========================
# ENVIO MANUAL (WHATSAPP)
# =========================
@app.get("/enviar-cobranca-oficial/{conta_id}")
def enviar_cobranca_oficial(conta_id: int, request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    try:
        conta = db.query(Conta).options(joinedload(Conta.cliente)).filter(Conta.id == conta_id).first()

        if not conta or not conta.cliente or not conta.cliente.telefone:
            return HTMLResponse("❌ Dados inválidos", status_code=400)

        telefone = "".join(filter(str.isdigit, conta.cliente.telefone))
        if not telefone.startswith("55"):
            telefone = "55" + telefone

        payload = {
            "messaging_product": "whatsapp",
            "to": telefone,
            "type": "template",
            "template": {
                "name": "cobranca_link",
                "language": {"code": "pt_BR"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": conta.cliente.nome},
                            {"type": "text", "text": conta.servico},
                            {"type": "text", "text": conta.login},
                            {"type": "text", "text": f"R$ {conta.valor:.2f}".replace(".", ",")},
                            {"type": "text", "text": conta.data_vencimento.strftime("%d/%m/%Y")}
                        ]
                    }
                ]
            }
        }

        headers = {
            "Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}",
            "Content-Type": "application/json"
        }

        resp = requests.post(
            f"https://graph.facebook.com/v20.0/{os.getenv('WHATSAPP_PHONE_NUMBER_ID')}/messages",
            headers=headers,
            json=payload
        )

        print(resp.text)

        if resp.status_code in [200, 201]:
            conta.status = "cobrado"
            db.commit()
            return HTMLResponse("✅ Enviado")

        return HTMLResponse(resp.text, status_code=500)

    finally:
        db.close()


# =========================
# AUTOMÁTICO
# =========================
@app.get("/enviar-cobrancas-automatico")
def enviar_cobrancas():
    db = SessionLocal()
    hoje = date.today()

    contas = db.query(Conta).options(joinedload(Conta.cliente)).filter(
        Conta.status == "pendente",
        Conta.data_vencimento <= hoje
    ).all()

    enviados = 0

    for c in contas:
        try:
            telefone = "55" + "".join(filter(str.isdigit, c.cliente.telefone))

            payload = {
                "messaging_product": "whatsapp",
                "to": telefone,
                "type": "template",
                "template": {
                    "name": "cobranca_link",
                    "language": {"code": "pt_BR"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": c.cliente.nome},
                                {"type": "text", "text": c.servico},
                                {"type": "text", "text": c.login},
                                {"type": "text", "text": f"R$ {c.valor:.2f}".replace(".", ",")},
                                {"type": "text", "text": c.data_vencimento.strftime("%d/%m/%Y")}
                            ]
                        }
                    ]
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

        except Exception as e:
            print("ERRO:", e)

    db.commit()
    db.close()

    return {"ok": True, "enviados": enviados}


# =========================
# BACKUP
# =========================
@app.get("/backup")
def backup():
    db = SessionLocal()

    data = {
        "clientes": [c.nome for c in db.query(Cliente).all()]
    }

    db.close()

    file = BytesIO(json.dumps(data).encode())

    return StreamingResponse(file, media_type="application/json")

