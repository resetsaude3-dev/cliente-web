```python
from datetime import date, datetime, timedelta
from urllib.parse import quote
import json
from io import BytesIO
import requests
import os

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
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
# FIX SQLITE (IMPORTANTE)
# =========================
def garantir_colunas_whatsapp():
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE contas ADD COLUMN whatsapp_message_id TEXT")
    except:
        pass

    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE contas ADD COLUMN whatsapp_status TEXT")
    except:
        pass

    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE contas ADD COLUMN whatsapp_status_at TEXT")
    except:
        pass


# =========================
# LOGIN
# =========================
def gerar_hash_senha(senha: str) -> str:
    return pwd_context.hash(senha)


def verificar_senha(senha_digitada: str, senha_salva: str) -> bool:
    try:
        return pwd_context.verify(senha_digitada, senha_salva)
    except:
        return False


def usuario_logado(request: Request):
    return request.cookies.get("usuario")


def exigir_login(request: Request):
    if not usuario_logado(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    garantir_colunas_whatsapp()

    db = SessionLocal()

    if not db.query(Usuario).filter(Usuario.username == "admin").first():
        db.add(Usuario(username="admin", senha=gerar_hash_senha("123456")))
        db.commit()

    db.close()


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

    if not user or not verificar_senha(senha, user.senha):
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
        "paga": db.query(Conta).filter(Conta.status == "paga").count()
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
# WHATSAPP AUTOMATICO
# =========================
@app.get("/enviar-cobrancas-automatico")
def enviar_cobrancas():
    db = SessionLocal()
    hoje = date.today()

    contas = db.query(Conta).options(joinedload(Conta.cliente)).filter(
        Conta.status == "pendente",
        Conta.data_vencimento <= hoje
    ).all()

    token = os.getenv("WHATSAPP_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    template = os.getenv("WHATSAPP_TEMPLATE_NAME")

    enviados = 0

    for c in contas:
        if not c.cliente or not c.cliente.telefone:
            continue

        telefone = "55" + "".join(filter(str.isdigit, c.cliente.telefone))

        data = {
            "messaging_product": "whatsapp",
            "to": telefone,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": "pt_BR"},
                "components": [{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": c.cliente.nome},
                        {"type": "text", "text": c.servico},
                        {"type": "text", "text": f"{c.valor:.2f}"}
                    ]
                }]
            }
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        r = requests.post(
            f"https://graph.facebook.com/v20.0/{phone_id}/messages",
            headers=headers,
            json=data
        )

        if r.ok:
            c.status = "cobrado"
            enviados += 1

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
```
