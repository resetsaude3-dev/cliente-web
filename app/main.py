from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
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
from fastapi.responses import StreamingResponse


app = FastAPI()

Base.metadata.create_all(bind=engine)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class BaixarCobranca(BaseModel):
    cliente_id: int
    data_vencimento: str


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
    if not usuario_logado(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


@app.on_event("startup")
def criar_usuario_padrao():
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


@app.get("/")
def home(request: Request):
    if not usuario_logado(request):
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=302)


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
        mensagem = (
            f"Olá {item['cliente_nome']}, tudo bem?\n\n"
            f"Os seguintes serviços estão vencidos:\n"
            f"- " + "\n- ".join(item["servicos"]) + "\n\n"
            f"Valor total: R$ {item['valor_total']:.2f}\n\n"
            f"Me avisa após o pagamento."
        )

        telefone = "".join(filter(str.isdigit, item["telefone"] or ""))
        whatsapp = None

        if telefone:
            if not telefone.startswith("55"):
                telefone = "55" + telefone
            whatsapp = f"https://wa.me/{telefone}?text={quote(mensagem)}"

        item["whatsapp"] = whatsapp
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
            "cobrancas_pendentes": cobrancas_pendentes
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
    
@app.get("/cobrar/{id}")
def cobrar_cliente(id: int):
    db = SessionLocal()

    conta = db.query(Conta).filter(Conta.id == id).first()

    if not conta or not conta.cliente:
        db.close()
        return RedirectResponse("/contas", status_code=303)

    # 🔥 MARCA COMO COBRADO
    conta.status = "cobrado"
    db.commit()

    telefone = conta.cliente.telefone

    mensagem = f"""
Olá {conta.cliente.nome},

Sua conta ({conta.servico}) venceu em {conta.data_vencimento}.

Valor: R$ {conta.valor}

Por favor, regularize o pagamento.
"""

    mensagem_formatada = quote(mensagem)

    db.close()

    return RedirectResponse(
        f"https://wa.me/{telefone}?text={mensagem_formatada}",
        status_code=302
    )
    
@app.get("/cobrados")
def cobrados():
    return {"ok": "funcionando"}