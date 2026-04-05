from datetime import date, datetime, timedelta
from urllib.parse import quote

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
# LOGIN
# =========================
def usuario_logado(request: Request):
    return request.cookies.get("usuario")


def exigir_login(request: Request):
    if not usuario_logado(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


@app.on_event("startup")
def criar_usuario_padrao():
    db = SessionLocal()

    # apaga TODOS usuários antigos
    db.query(Usuario).delete()
    db.commit()

    # cria usuário novo
    usuario = Usuario(username="hdstore", senha="wad13sil")
    db.add(usuario)
    db.commit()

    db.close()


@app.get("/login", response_class=HTMLResponse)
def pagina_login(request: Request):
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
    usuario = db.query(Usuario).filter(
        Usuario.username == username,
        Usuario.senha == senha
    ).first()
    db.close()

    if not usuario:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "erro": "Usuário ou senha inválidos"
            }
        )

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("usuario", usuario.username, httponly=True)
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("usuario")
    return response


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

    hoje = date.today()
    db = SessionLocal()

    total_clientes = db.query(Cliente).count()
    total_contas = db.query(Conta).count()

    contas_hoje = db.query(Conta).filter(
        Conta.status == "pendente",
        Conta.cliente_id.isnot(None),
        Conta.data_vencimento == hoje
    ).all()

    contas_atrasadas = db.query(Conta).filter(
        Conta.status == "pendente",
        Conta.cliente_id.isnot(None),
        Conta.data_vencimento < hoje
    ).all()

    valor_hoje = sum(c.valor or 0 for c in contas_hoje)
    valor_atrasado = sum(c.valor or 0 for c in contas_atrasadas)

    cobrancas_pendentes = montar_cobrancas_pendentes(db)

    db.close()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "total_clientes": total_clientes,
            "total_contas": total_contas,
            "valor_hoje": valor_hoje,
            "valor_atrasado": valor_atrasado,
            "qtd_hoje": len(contas_hoje),
            "qtd_atrasado": len(contas_atrasadas),
            "cobrancas_pendentes": cobrancas_pendentes
        }
    )


# =========================
# CLIENTES
# =========================
@app.get("/clientes", response_class=HTMLResponse)
def listar_clientes(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()
    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    db.close()

    return templates.TemplateResponse(
        request,
        "clientes.html",
        {
            "request": request,
            "usuario": usuario_logado(request),
            "clientes": clientes,
            "cliente_editar": None
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
def listar_contas(request: Request):
    redir = exigir_login(request)
    if redir:
        return redir

    db = SessionLocal()

    contas = db.query(Conta).options(
        joinedload(Conta.cliente)
    ).order_by(Conta.data_vencimento.asc()).all()

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
            "conta_editar": None
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