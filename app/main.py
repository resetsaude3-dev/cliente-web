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
from sqlalchemy import text
from passlib.context import CryptContext

from app.database import Base, engine, SessionLocal
from app.models import Usuario, Cliente, Conta

print("🔥 APP INICIOU 🔥")

app = FastAPI(
    docs_url="/docs",
    redoc_url="/redoc"
)

@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse(url="/dashboard", status_code=302)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class BaixarCobranca(BaseModel):
    cliente_id: int
    data_vencimento: str


def garantir_colunas_whatsapp():
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE contas ADD COLUMN IF NOT EXISTS whatsapp_message_id TEXT")
            conn.exec_driver_sql("ALTER TABLE contas ADD COLUMN IF NOT EXISTS whatsapp_status TEXT")
            conn.exec_driver_sql("ALTER TABLE contas ADD COLUMN IF NOT EXISTS whatsapp_status_at TEXT")
    except Exception as e:
        print("Erro ao garantir colunas:", e)


def salvar_message_id_nas_contas(contas, message_id: str):
    for conta in contas:
        conta.whatsapp_message_id = message_id
        conta.whatsapp_status = "sent"
        conta.whatsapp_status_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def atualizar_status_por_message_id(db, message_id: str, novo_status: str):
    contas = db.query(Conta).filter(Conta.whatsapp_message_id == message_id).all()

    for conta in contas:
        conta.whatsapp_status = novo_status
        conta.whatsapp_status_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db.commit()


# =========================
# SENHA / LOGIN
# =========================
def gerar_hash_senha(senha: str) -> str:
    return pwd_context.hash(senha)


def verificar_senha(senha_digitada: str, senha_salva: str) -> bool:
    try:
        return pwd_context.verify(senha_digitada, senha_salva)
    except Exception:
        return False


def usuario_logado(request: Request):
    return request.cookies.get("usuario")


def exigir_login(request: Request):
    path = request.url.path

    # libera rotas públicas
    if (
        path.startswith("/docs") or
        path.startswith("/openapi") or
        path.startswith("/redoc") or
        path.startswith("/webhook-whatsapp")
    ):
        return None

    # proteção normal
    if not usuario_logado(request):
        return RedirectResponse(url="/login", status_code=303)

    return None


@app.on_event("startup")
def criar_usuario_padrao():
    Base.metadata.create_all(bind=engine)
    garantir_colunas_whatsapp()

    db = SessionLocal()

    usuario = db.query(Usuario).filter(Usuario.username == "hdstore").first()

    if not usuario:
        usuario = Usuario(
            username="hdstore",
            senha=gerar_hash_senha("wad13sil")
        )
        db.add(usuario)
        db.commit()

    db.close()


# =========================
# LOGIN
# =========================
@app.get("/login", response_class=HTMLResponse)
def pagina_login(request: Request):
    if usuario_logado(request):
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "erro": None
        }
    )


@app.post("/login", response_class=HTMLResponse)
def fazer_login(
    request: Request,
    username: str = Form(...),
    senha: str = Form(...)
):
    db = SessionLocal()
    usuario = db.query(Usuario).filter(Usuario.username == username).first()
    db.close()

    if not usuario or not verificar_senha(senha, usuario.senha):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "erro": "Usuário ou senha inválidos"
            }
        )

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="usuario",
        value=usuario.username,
        httponly=True,
        samesite="lax"
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("usuario")
    return response


@app.get("/trocar-senha", response_class=HTMLResponse)
def pagina_trocar_senha(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    return templates.TemplateResponse(
        request,
        "trocar_senha.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "erro": None,
            "sucesso": None
        }
    )


@app.post("/trocar-senha", response_class=HTMLResponse)
def trocar_senha(
    request: Request,
    senha_atual: str = Form(...),
    nova_senha: str = Form(...),
    confirmar_senha: str = Form(...)
):
    redir = exigir_login(request)
    if redir:
        return redir

    username = usuario_logado(request)
    db = SessionLocal()
    usuario = db.query(Usuario).filter(Usuario.username == username).first()

    if not usuario:
        db.close()
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie("usuario")
        return response

    if not verificar_senha(senha_atual, usuario.senha):
        db.close()
        return templates.TemplateResponse(
            request,
            "trocar_senha.html",
            {
                "request": request,
                "usuario": username,
                "erro": "Senha atual incorreta",
                "sucesso": None
            }
        )

    if len(nova_senha) < 6:
        db.close()
        return templates.TemplateResponse(
            request,
            "trocar_senha.html",
            {
                "request": request,
                "usuario": username,
                "erro": "A nova senha deve ter pelo menos 6 caracteres",
                "sucesso": None
            }
        )

    if nova_senha != confirmar_senha:
        db.close()
        return templates.TemplateResponse(
            request,
            "trocar_senha.html",
            {
                "request": request,
                "usuario": username,
                "erro": "A confirmação da senha não confere",
                "sucesso": None
            }
        )

    usuario.senha = gerar_hash_senha(nova_senha)
    db.commit()
    db.close()

    return templates.TemplateResponse(
        request,
        "trocar_senha.html",
        {
            "request": request,
            "usuario": username,
            "erro": None,
            "sucesso": "Senha alterada com sucesso"
        }
    )


# =========================
# FUNÇÃO AUXILIAR DE COBRANÇAS
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
        telefone_cliente = conta.cliente.telefone if conta.cliente and conta.cliente.telefone else ""

        if chave not in agrupadas:
            agrupadas[chave] = {
                "cliente_id": conta.cliente_id,
                "cliente_nome": nome_cliente,
                "telefone": telefone_cliente,
                "data_vencimento": conta.data_vencimento.isoformat(),
                "servicos": [],
                "contas_detalhes": [],
                "valor_total": 0.0,
                "total_contas": 0,
                "tipo": "vence_hoje" if conta.data_vencimento == hoje else "atrasada"
            }

        agrupadas[chave]["servicos"].append(conta.servico)
        agrupadas[chave]["contas_detalhes"].append({
            "id": conta.id,
            "servico": conta.servico,
            "login": conta.login or "",
            "perfil": conta.perfil or "",
            "valor": float(conta.valor or 0)
        })
        agrupadas[chave]["valor_total"] += float(conta.valor or 0)
        agrupadas[chave]["total_contas"] += 1

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
        whatsapp = None

        if telefone:
            if not telefone.startswith("55"):
                telefone = "55" + telefone
            whatsapp = f"https://wa.me/{telefone}?text={quote(mensagem)}"

        item["whatsapp"] = whatsapp
        item["cobrar_url"] = f"/cobrar-cliente/{item['cliente_id']}/{item['data_vencimento']}"
        resultado.append(item)

    resultado.sort(key=lambda x: (x["data_vencimento"], x["cliente_nome"].lower()))
    return resultado


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

    total_clientes = db.query(Cliente).count()
    total_contas = db.query(Conta).count()

    pendentes = db.query(Conta).filter(Conta.status == "pendente").count()

    vencidas = db.query(Conta).filter(
        Conta.status == "pendente",
        Conta.data_vencimento < hoje
    ).count()

    vencendo_hoje = db.query(Conta).filter(
        Conta.data_vencimento == hoje
    ).count()

    pagas = db.query(Conta).filter(Conta.status == "paga").count()

    db.close()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "total_clientes": total_clientes,
            "total_contas": total_contas,
            "pendentes": pendentes,
            "vencidas": vencidas,
            "vencendo_hoje": vencendo_hoje,
            "pagas": pagas,
        }
    )


# =========================
# CLIENTES
# =========================
@app.get("/clientes", response_class=HTMLResponse)
def listar_clientes(request: Request, busca: str = ""):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    query = db.query(Cliente)

    if busca:
        query = query.filter(Cliente.nome.ilike(f"%{busca}%"))

    clientes = query.order_by(Cliente.nome.asc()).all()

    db.close()

    return templates.TemplateResponse(
        request,
        "clientes.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "clientes": clientes,
            "cliente_editar": None,
            "busca": busca
        }
    )


@app.post("/clientes")
def criar_cliente(
    request: Request,
    nome: str = Form(...),
    telefone: str = Form(""),
    observacao: str = Form("")
):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    cliente = Cliente(nome=nome, telefone=telefone, observacao=observacao)
    db.add(cliente)
    db.commit()
    db.close()
    return RedirectResponse(url="/clientes", status_code=303)


@app.get("/editar-cliente/{id}", response_class=HTMLResponse)
def editar_cliente_form(request: Request, id: int):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    cliente_editar = db.query(Cliente).filter(Cliente.id == id).first()
    db.close()

    return templates.TemplateResponse(
        request,
        "clientes.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "clientes": clientes,
            "cliente_editar": cliente_editar
        }
    )


@app.post("/editar-cliente/{id}")
def editar_cliente(
    request: Request,
    id: int,
    nome: str = Form(...),
    telefone: str = Form(""),
    observacao: str = Form("")
):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    cliente = db.query(Cliente).filter(Cliente.id == id).first()

    if cliente:
        cliente.nome = nome
        cliente.telefone = telefone
        cliente.observacao = observacao
        db.commit()

    db.close()
    return RedirectResponse(url="/clientes", status_code=303)


@app.get("/deletar-cliente/{id}")
def deletar_cliente(request: Request, id: int):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    cliente = db.query(Cliente).filter(Cliente.id == id).first()

    if cliente:
        db.delete(cliente)
        db.commit()

    db.close()
    return RedirectResponse(url="/clientes", status_code=303)


# =========================
# CONTAS
# =========================
@app.get("/contas", response_class=HTMLResponse)
def listar_contas(
    request: Request,
    cliente_id: str = "",
    servico: str = "",
    status: str = ""
):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    query = db.query(Conta).options(joinedload(Conta.cliente))

    if cliente_id.strip():
        query = query.filter(Conta.cliente_id == int(cliente_id))

    if servico.strip():
        query = query.filter(Conta.servico.ilike(f"%{servico.strip()}%"))

    if status.strip():
        query = query.filter(Conta.status == status.strip())

    contas = query.order_by(Conta.data_vencimento.asc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()

    db.close()

    return templates.TemplateResponse(
        request,
        "contas.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "contas": contas,
            "clientes": clientes,
            "conta_editar": None,
            "cliente_id": cliente_id,
            "servico": servico,
            "status": status
        }
    )


@app.post("/contas")
def criar_conta(
    request: Request,
    cliente_id: int = Form(...),
    servico: str = Form(...),
    login: str = Form(""),
    senha: str = Form(""),
    perfil: str = Form(""),
    valor: float = Form(...),
    data_vencimento: str = Form(...),
    status: str = Form("pendente"),
    observacao: str = Form("")
):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    conta = Conta(
        cliente_id=cliente_id,
        servico=servico,
        login=login,
        senha=senha,
        perfil=perfil,
        valor=valor,
        data_vencimento=datetime.strptime(data_vencimento, "%Y-%m-%d").date(),
        status=status,
        observacao=observacao
    )

    db.add(conta)
    db.commit()
    db.close()

    return RedirectResponse(url="/contas", status_code=303)


@app.get("/editar-conta/{id}", response_class=HTMLResponse)
def editar_conta_form(request: Request, id: int):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    contas = db.query(Conta).options(
        joinedload(Conta.cliente)
    ).order_by(Conta.data_vencimento.asc()).all()

    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    conta_editar = db.query(Conta).filter(Conta.id == id).first()

    db.close()

    return templates.TemplateResponse(
        request,
        "contas.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "contas": contas,
            "clientes": clientes,
            "conta_editar": conta_editar
        }
    )


@app.post("/editar-conta/{id}")
def editar_conta(
    request: Request,
    id: int,
    cliente_id: int = Form(...),
    servico: str = Form(...),
    login: str = Form(""),
    senha: str = Form(""),
    perfil: str = Form(""),
    valor: float = Form(...),
    data_vencimento: str = Form(...),
    status: str = Form("pendente"),
    observacao: str = Form(""),
    motivo_manutencao: str = Form("")
):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    conta = db.query(Conta).filter(Conta.id == id).first()

    if conta:
        conta.cliente_id = cliente_id
        conta.servico = servico
        conta.login = login
        conta.senha = senha
        conta.perfil = perfil
        conta.valor = valor
        conta.data_vencimento = datetime.strptime(data_vencimento, "%Y-%m-%d").date()
        conta.status = status
        conta.observacao = observacao
        conta.motivo_manutencao = motivo_manutencao
        db.commit()

    db.close()
    return RedirectResponse(url="/contas", status_code=303)


@app.get("/deletar-conta/{id}")
def deletar_conta(request: Request, id: int):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    conta = db.query(Conta).filter(Conta.id == id).first()

    if conta:
        db.delete(conta)
        db.commit()

    db.close()
    return RedirectResponse(url="/contas", status_code=303)


@app.get("/renovar-conta/{id}")
def renovar_conta(request: Request, id: int):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    conta = db.query(Conta).filter(Conta.id == id).first()

    if conta:
        conta.data_vencimento = conta.data_vencimento + timedelta(days=30)
        conta.status = "pendente"
        db.commit()

    db.close()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/desvincular-conta/{id}")
def desvincular_conta(request: Request, id: int):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    conta = db.query(Conta).filter(Conta.id == id).first()

    if conta:
        conta.cliente_id = None
        conta.status = "disponivel"
        conta.motivo_manutencao = None
        db.commit()

    db.close()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/manutencao-conta/{id}")
def manutencao_conta(
    request: Request,
    id: int,
    motivo: str = Form("")
):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    conta = db.query(Conta).filter(Conta.id == id).first()

    if conta:
        conta.cliente_id = None
        conta.status = "manutencao"
        conta.motivo_manutencao = motivo
        db.commit()

    db.close()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/disponiveis", response_class=HTMLResponse)
def listar_disponiveis(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    contas = db.query(Conta).filter(
        Conta.status == "disponivel"
    ).order_by(Conta.servico.asc()).all()

    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()

    db.close()

    return templates.TemplateResponse(
        request,
        "disponiveis.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "contas": contas,
            "clientes": clientes
        }
    )


@app.post("/vincular-conta/{id}")
def vincular_conta(
    request: Request,
    id: int,
    cliente_id: int = Form(...),
    data_vencimento: str = Form(...)
):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    conta = db.query(Conta).filter(Conta.id == id).first()

    if conta:
        conta.cliente_id = cliente_id
        conta.data_vencimento = datetime.strptime(data_vencimento, "%Y-%m-%d").date()
        conta.status = "pendente"
        conta.motivo_manutencao = None
        db.commit()

    db.close()
    return RedirectResponse(url="/disponiveis", status_code=303)


@app.get("/manutencao", response_class=HTMLResponse)
def listar_manutencao(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    contas = db.query(Conta).filter(
        Conta.status == "manutencao"
    ).order_by(Conta.servico.asc()).all()

    db.close()

    return templates.TemplateResponse(
        request,
        "manutencao.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "contas": contas
        }
    )


@app.get("/disponibilizar-conta/{id}")
def disponibilizar_conta(request: Request, id: int):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    conta = db.query(Conta).filter(Conta.id == id).first()

    if conta:
        conta.status = "disponivel"
        db.commit()

    db.close()
    return RedirectResponse(url="/manutencao", status_code=303)


# =========================
# COBRANCAS
# =========================
@app.get("/cobrancas", response_class=HTMLResponse)
def pagina_cobrancas(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    cobrancas_pendentes = montar_cobrancas_pendentes(db)
    db.close()

    return templates.TemplateResponse(
        request,
        "cobrancas.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "cobrancas_pendentes": cobrancas_pendentes,
            "contas": cobrancas_pendentes
        }
    )


@app.get("/api/cobrancas")
def listar_cobrancas(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    resultado = montar_cobrancas_pendentes(db)
    db.close()
    return resultado


@app.put("/api/cobrancas/pagar")
def pagar(request: Request, payload: BaixarCobranca):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    data = datetime.strptime(payload.data_vencimento, "%Y-%m-%d").date()

    contas = db.query(Conta).filter(
        Conta.cliente_id == payload.cliente_id,
        Conta.data_vencimento == data,
        Conta.status == "pendente"
    ).all()

    for c in contas:
        c.status = "paga"

    db.commit()
    db.close()

    return {"ok": True}


@app.get("/cobrar-cliente/{cliente_id}/{data_vencimento}")
def cobrar_cliente_agrupado(cliente_id: int, data_vencimento: str, request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    data = datetime.strptime(data_vencimento, "%Y-%m-%d").date()

    contas = db.query(Conta).options(
        joinedload(Conta.cliente)
    ).filter(
        Conta.cliente_id == cliente_id,
        Conta.data_vencimento == data,
        Conta.status == "pendente"
    ).all()

    if not contas:
        db.close()
        return RedirectResponse(url="/cobrancas", status_code=303)

    cliente = contas[0].cliente
    telefone = ""

    if cliente and cliente.telefone:
        telefone = "".join(filter(str.isdigit, cliente.telefone))
        if telefone and not telefone.startswith("55"):
            telefone = "55" + telefone

    linhas = []
    valor_total = 0.0

    for conta in contas:
        conta.status = "cobrado"
        valor_total += float(conta.valor or 0)

        linha = f"{conta.servico}"
        if conta.login:
            linha += f"\nUsuário: {conta.login}"
        if conta.perfil:
            linha += f"\nPerfil: {conta.perfil}"

        linhas.append(linha)

    db.commit()
    db.close()

    mensagem = (
        f"Olá {cliente.nome if cliente else ''}, tudo bem?\n\n"
        f"Os seguintes serviços estão vencidos:\n\n"
        + "\n\n".join(linhas)
        + f"\n\nValor total: R$ {valor_total:.2f}\n\n"
        f"Me avisa após o pagamento."
    )

    if telefone:
        return RedirectResponse(
            url=f"https://wa.me/{telefone}?text={quote(mensagem)}",
            status_code=302
        )

    return RedirectResponse(url="/cobrados", status_code=303)


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
        request,
        "cobrados.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "contas": contas
        }
    )


# =========================
# BACKUP
# =========================
@app.get("/backup")
def gerar_backup(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    usuarios = db.query(Usuario).all()
    clientes = db.query(Cliente).all()
    contas = db.query(Conta).all()

    dados = {
        "usuarios": [
            {
                "id": u.id,
                "username": u.username,
                "senha": u.senha
            }
            for u in usuarios
        ],
        "clientes": [
            {
                "id": c.id,
                "nome": c.nome,
                "telefone": c.telefone,
                "observacao": c.observacao
            }
            for c in clientes
        ],
        "contas": [
            {
                "id": c.id,
                "cliente_id": c.cliente_id,
                "servico": c.servico,
                "login": c.login,
                "senha": c.senha,
                "perfil": c.perfil,
                "valor": c.valor,
                "data_vencimento": c.data_vencimento.isoformat() if c.data_vencimento else None,
                "status": c.status,
                "observacao": c.observacao,
                "motivo_manutencao": c.motivo_manutencao
            }
            for c in contas
        ]
    }

    db.close()

    conteudo = json.dumps(dados, ensure_ascii=False, indent=2)
    arquivo = BytesIO(conteudo.encode("utf-8"))

    nome_arquivo = f"backup_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"

    return StreamingResponse(
        arquivo,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{nome_arquivo}"'
        }
    )


# =========================
# USUARIOS
# =========================
@app.get("/usuarios", response_class=HTMLResponse)
def listar_usuarios(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    usuarios = db.query(Usuario).order_by(Usuario.username.asc()).all()
    db.close()

    return templates.TemplateResponse(
        request,
        "usuarios.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "usuarios": usuarios,
            "erro": None,
            "sucesso": None
        }
    )


@app.post("/usuarios")
def criar_usuario(
    request: Request,
    username: str = Form(...),
    senha: str = Form(...)
):
    redir = exigir_login(request)
    if redir:
        return redir

    username = username.strip()

    db = SessionLocal()

    existente = db.query(Usuario).filter(Usuario.username == username).first()
    if existente:
        usuarios = db.query(Usuario).order_by(Usuario.username.asc()).all()
        db.close()

        return templates.TemplateResponse(
            request,
            "usuarios.html",
            {
                "request": request,
                "usuario": usuario_logado(request),
                "usuarios": usuarios,
                "erro": "Esse usuário já existe",
                "sucesso": None
            }
        )

    if len(senha) < 6:
        usuarios = db.query(Usuario).order_by(Usuario.username.asc()).all()
        db.close()

        return templates.TemplateResponse(
            request,
            "usuarios.html",
            {
                "request": request,
                "usuario": usuario_logado(request),
                "usuarios": usuarios,
                "erro": "A senha deve ter pelo menos 6 caracteres",
                "sucesso": None
            }
        )

    novo_usuario = Usuario(
        username=username,
        senha=gerar_hash_senha(senha)
    )

    db.add(novo_usuario)
    db.commit()
    db.close()

    return RedirectResponse(url="/usuarios", status_code=303)


@app.get("/deletar-usuario/{id}")
def deletar_usuario(request: Request, id: int):
    redir = exigir_login(request)
    if redir:
        return redir

    usuario_atual = usuario_logado(request)

    db = SessionLocal()
    usuario = db.query(Usuario).filter(Usuario.id == id).first()

    if usuario:
        if usuario.username == usuario_atual:
            db.close()
            return RedirectResponse(url="/usuarios", status_code=303)

        db.delete(usuario)
        db.commit()

    db.close()
    return RedirectResponse(url="/usuarios", status_code=303)
    
@app.get("/enviar-cobranca-oficial/{conta_id}")
def enviar_cobranca_oficial(conta_id: int, request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    print("DEBUG conta_id recebido:", conta_id)
    print("DEBUG DATABASE_URL:", os.getenv("DATABASE_URL"))

    contas_debug = db.query(Conta).all()
    print("DEBUG ids no banco:", [c.id for c in contas_debug])

    conta = db.query(Conta).options(
        joinedload(Conta.cliente)
    ).filter(Conta.id == conta_id).first()

    print("DEBUG conta encontrada:", conta)

    if not conta:
        db.close()
        return HTMLResponse(f"❌ Conta não encontrada: {conta_id}", status_code=404)

    if not conta.cliente:
        db.close()
        return HTMLResponse("❌ Conta sem cliente vinculado", status_code=400)

    token = os.getenv("WHATSAPP_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    template_name = os.getenv("WHATSAPP_TEMPLATE_NAME")
    template_lang = os.getenv("WHATSAPP_TEMPLATE_LANG", "pt_BR")

    if not token:
        db.close()
        return HTMLResponse("❌ WHATSAPP_TOKEN não configurado no Render", status_code=500)

    if not phone_id:
        db.close()
        return HTMLResponse("❌ WHATSAPP_PHONE_NUMBER_ID não configurado no Render", status_code=500)

    if not template_name:
        db.close()
        return HTMLResponse("❌ WHATSAPP_TEMPLATE_NAME não configurado no Render", status_code=500)

    telefone = "".join(filter(str.isdigit, conta.cliente.telefone or ""))

    if not telefone:
        db.close()
        return HTMLResponse("❌ Cliente sem telefone cadastrado", status_code=400)

    if not telefone.startswith("55"):
        telefone = "55" + telefone

    data = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {
                "code": template_lang
            },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": conta.cliente.nome or "-"},
                        {"type": "text", "text": conta.servico or "-"},
                        {"type": "text", "text": conta.login or "-"},
                        {"type": "text", "text": f"{float(conta.valor or 0):.2f}"},
                        {"type": "text", "text": conta.data_vencimento.strftime("%d/%m/%Y") if conta.data_vencimento else "-"}
                    ]
                }
            ]
        }
    }

    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        print("STATUS:", response.status_code)
        print("RESPOSTA:", response.text)

        if response.ok:
            resposta_json = response.json()
            message_id = None

            if resposta_json.get("messages"):
                message_id = resposta_json["messages"][0].get("id")

            conta.status = "cobrado"

            if message_id:
                salvar_message_id_nas_contas([conta], message_id)

            db.commit()
            db.close()
            return RedirectResponse(url="/cobrados", status_code=303)
        else:
            erro = response.text
            db.close()
            return HTMLResponse(f"❌ Erro ao enviar WhatsApp:<br><pre>{erro}</pre>", status_code=400)

    except Exception as e:
        db.close()
        return HTMLResponse(f"❌ Erro interno: {str(e)}", status_code=500)


@app.get("/enviar-cobrancas-automatico")
def enviar_cobrancas_automatico():
    db = SessionLocal()

    hoje = date.today()

    contas = db.query(Conta).options(
        joinedload(Conta.cliente)
    ).filter(
        Conta.status == "pendente",
        Conta.data_vencimento <= hoje
    ).all()

    token = os.getenv("WHATSAPP_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    template_name = os.getenv("WHATSAPP_TEMPLATE_NAME")
    template_lang = os.getenv("WHATSAPP_TEMPLATE_LANG", "pt_BR")

    enviados = 0
    erros = 0

    for conta in contas:
        if not conta.cliente or not conta.cliente.telefone:
            continue

        telefone = "".join(filter(str.isdigit, conta.cliente.telefone or ""))

        if not telefone.startswith("55"):
            telefone = "55" + telefone

        data = {
            "messaging_product": "whatsapp",
            "to": telefone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": template_lang
                },
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": conta.cliente.nome or "-"},
                            {"type": "text", "text": conta.servico or "-"},
                            {"type": "text", "text": conta.login or "-"},
                            {"type": "text", "text": f"{float(conta.valor or 0):.2f}"},
                            {"type": "text", "text": conta.data_vencimento.strftime("%d/%m/%Y") if conta.data_vencimento else "-"}
                        ]
                    }
                ]
            }
        }

        url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(url, headers=headers, json=data, timeout=30)

            if response.ok:
                resposta_json = response.json()
                message_id = None

                if resposta_json.get("messages"):
                    message_id = resposta_json["messages"][0].get("id")

                conta.status = "cobrado"

                if message_id:
                    salvar_message_id_nas_contas([conta], message_id)

                enviados += 1
            else:
                erros += 1

        except Exception as e:
            print("ERRO:", e)
            erros += 1

    db.commit()
    db.close()

    return {
        "ok": True,
        "data": str(hoje),
        "enviados": enviados,
        "erros": erros
    }


@app.get("/webhook-whatsapp")
def verificar_webhook_whatsapp(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")

    if mode == "subscribe" and token == verify_token:
        return HTMLResponse(content=challenge or "", status_code=200)

    return HTMLResponse(content="Token inválido", status_code=403)


@app.post("/webhook-whatsapp")
async def receber_webhook_whatsapp(request: Request):
    body = await request.json()
    print("WEBHOOK WHATSAPP:", json.dumps(body, ensure_ascii=False))

    try:
        db = SessionLocal()

        entries = body.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})

                statuses = value.get("statuses", [])
                for status_item in statuses:
                    message_id = status_item.get("id")
                    status = status_item.get("status")

                    if message_id and status:
                        atualizar_status_por_message_id(db, message_id, status)

        db.close()
        return {"ok": True}

    except Exception as e:
        print("ERRO WEBHOOK:", str(e))
        return {"ok": False, "erro": str(e)}

@app.get("/gerar-pix/{conta_id}")
def gerar_pix(conta_id: int, request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    try:
        conta = db.query(Conta).filter(Conta.id == conta_id).first()

        if not conta:
            db.close()
            return {"erro": "Conta não encontrada"}

        token = os.getenv("DEFLOW_TOKEN")
        slug = os.getenv("DEFLOW_SLUG")

        if not token or not slug:
            db.close()
            return {"erro": "DEFLOW_TOKEN ou DEFLOW_SLUG não configurado"}

        url = f"https://deflow.exchange/api/links/invoice/{slug}/pay"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        data = {
            "amountInCents": int(float(conta.valor) * 100)
        }

        response = requests.post(url, json=data, headers=headers, timeout=30)

        if response.status_code not in [200, 201]:
            erro = response.text
            db.close()
            return {"erro": erro}

        db.close()
        return RedirectResponse(
            url=f"https://deflow.exchange/invoice/{slug}",
            status_code=303
        )

    except Exception as e:
        db.close()
        return {"erro": str(e)}
        
@app.get("/teste123")
def teste123():
    return {"ok": True, "rota": "teste123"}
    
    
print("ROTAS CARREGADAS:")
for r in app.routes:
    try:
        print(r.path, r.methods)
    except Exception:
        print(r)