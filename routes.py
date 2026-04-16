"""
routes.py — Rotas do aplicativo (as "páginas").

Cada função aqui corresponde a uma URL que o usuário pode acessar.
As rotas recebem dados do navegador e devolvem HTML renderizado.

CONCEITO IMPORTANTE:
- @bp.route("/url") define QUAL URL ativa a função
- methods=["GET", "POST"] define se a rota aceita leitura e/ou envio de dados
- render_template() pega um HTML e injeta dados Python nele
- redirect() manda o usuário para outra página
- request.form["campo"] pega o que o usuário digitou no formulário
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import db, Posicao, Provento, MetaFundo, MetaSegmento, MetaCategoria, ConfigCenario, PrecoAlvo
from services import (calcular_resumo_carteira, buscar_cotacao,
                      buscar_historico_patrimonio, calcular_gaps_metas,
                      calcular_recomendacao, registrar_snapshot_patrimonio,
                      mercado_aberto, hora_brasilia,
                      atualizar_cenario_automatico, _periodo_eleitoral)
from datetime import date

# Blueprint = um "módulo" de rotas. Permite organizar rotas em arquivos separados.
bp = Blueprint("main", __name__)


@bp.route("/")
def dashboard():
    """
    Página principal — mostra o resumo da carteira.
    
    Fluxo:
    1. Chama calcular_resumo_carteira() que faz toda a lógica pesada
    2. Passa os dados para o template dashboard.html
    3. O Jinja2 renderiza o HTML com os dados
    """
    registrar_snapshot_patrimonio()   # acumula ponto intraday se mercado aberto

    resumo    = calcular_resumo_carteira()
    historico = buscar_historico_patrimonio()
    gaps      = calcular_gaps_metas()

    agora_br       = hora_brasilia()
    mercado_status = mercado_aberto()

    return render_template(
        "dashboard.html",
        resumo=resumo,
        historico=historico,
        gaps=gaps,
        mercado_aberto=mercado_status,
        hora_brasilia_str=agora_br.strftime("%H:%M"),
    )


@bp.route("/posicoes", methods=["GET", "POST"])
def posicoes():
    """
    Lista posições e permite adicionar novas.
    
    GET  → mostra a página com formulário + lista
    POST → recebe os dados do formulário e salva no banco
    """
    if request.method == "POST":
        # Pega dados do formulário
        ticker = request.form["ticker"].upper().strip()
        quantidade = int(request.form["quantidade"])
        preco = float(request.form["preco_unitario"])
        data_str = request.form.get("data_compra", "")

        # Converte a data (o formulário manda como "2026-04-15")
        if data_str:
            data_compra = date.fromisoformat(data_str)
        else:
            data_compra = date.today()

        # Busca cotação para inferir o segmento automaticamente.
        # Se a API falhar, segmento fica None — o usuário pode preencher depois.
        cotacao = buscar_cotacao(ticker)
        segmento = cotacao["segmento"] if cotacao else None

        # Cria o objeto e salva no banco
        nova_posicao = Posicao(
            ticker=ticker,
            quantidade=quantidade,
            preco_unitario=preco,
            data_compra=data_compra,
            segmento=segmento,
        )
        db.session.add(nova_posicao)
        db.session.commit()

        flash(f"Posição adicionada: {quantidade}x {ticker} a R$ {preco:.2f}", "success")
        return redirect(url_for("main.posicoes"))

    # GET — busca todas as posições ordenadas por data
    todas_posicoes = Posicao.query.order_by(Posicao.data_compra.desc()).all()
    return render_template("posicoes.html", posicoes=todas_posicoes)


@bp.route("/posicoes/deletar/<int:id>")
def deletar_posicao(id):
    """
    Deleta uma posição pelo ID.
    
    O <int:id> no URL captura o número e passa como parâmetro.
    Ex: /posicoes/deletar/3 → id=3
    """
    posicao = Posicao.query.get_or_404(id)
    db.session.delete(posicao)
    db.session.commit()
    flash(f"Posição {posicao.ticker} removida.", "info")
    return redirect(url_for("main.posicoes"))


@bp.route("/posicoes/editar/<int:id>", methods=["GET", "POST"])
def editar_posicao(id):
    """
    Edita uma posição existente.

    CONCEITO — Por que GET e POST aqui?
    - GET:  o usuário clicou em "Editar". Precisamos CARREGAR os dados atuais
            e exibir o formulário já preenchido.
    - POST: o usuário clicou em "Salvar". Precisamos ATUALIZAR o banco com
            os novos valores e redirecionar.

    CONCEITO — get_or_404(id)
    Tenta buscar a linha com esse id. Se não existir, retorna automaticamente
    um erro 404 (Não encontrado) — sem precisar checar manualmente.
    """
    posicao = Posicao.query.get_or_404(id)

    if request.method == "POST":
        # Sobrescreve os atributos do objeto com os novos valores do formulário.
        # O SQLAlchemy detecta que o objeto mudou e ao fazer commit ele executa
        # um UPDATE (não um INSERT), porque o objeto já existe no banco.
        posicao.ticker = request.form["ticker"].upper().strip()
        posicao.quantidade = int(request.form["quantidade"])
        posicao.preco_unitario = float(request.form["preco_unitario"])
        posicao.segmento = request.form.get("segmento", "").strip() or None

        data_str = request.form.get("data_compra", "")
        if data_str:
            posicao.data_compra = date.fromisoformat(data_str)

        db.session.commit()  # Aqui o SQLAlchemy gera: UPDATE posicoes SET ... WHERE id = ?

        flash(f"Posição {posicao.ticker} atualizada com sucesso.", "success")
        return redirect(url_for("main.posicoes"))

    # GET — apenas renderiza o template passando o objeto posicao.
    # O template vai usar os valores do objeto para preencher os campos.
    return render_template("editar_posicao.html", posicao=posicao)


@bp.route("/proventos", methods=["GET", "POST"])
def proventos():
    """
    Lista proventos e permite registrar novos.
    """
    if request.method == "POST":
        ticker = request.form["ticker"].upper().strip()
        valor_total = float(request.form["valor_total"])
        data_str = request.form.get("data_pagamento", "")

        if data_str:
            data_pagamento = date.fromisoformat(data_str)
        else:
            data_pagamento = date.today()

        novo_provento = Provento(
            ticker=ticker,
            valor_total=valor_total,
            data_pagamento=data_pagamento,
        )
        db.session.add(novo_provento)
        db.session.commit()

        flash(f"Provento registrado: {ticker} — R$ {valor_total:.2f}", "success")
        return redirect(url_for("main.proventos"))

    todos_proventos = Provento.query.order_by(Provento.data_pagamento.desc()).all()
    return render_template("proventos.html", proventos=todos_proventos)


@bp.route("/proventos/deletar/<int:id>")
def deletar_provento(id):
    """Deleta um provento pelo ID."""
    provento = Provento.query.get_or_404(id)
    db.session.delete(provento)
    db.session.commit()
    flash(f"Provento {provento.ticker} removido.", "info")
    return redirect(url_for("main.proventos"))


# ─────────────────────────────────────────────
#  METAS DE ALOCAÇÃO
# ─────────────────────────────────────────────

@bp.route("/metas", methods=["GET", "POST"])
def metas():
    """
    Exibe e permite editar as três camadas de meta:
      - Camada 0: peso-alvo por fundo (MetaFundo)
      - Camada 1: guardrails por segmento (MetaSegmento)
      - Camada 2: guardrails por categoria (MetaCategoria)

    No POST, salva todos os campos de uma vez.
    """
    if request.method == "POST":
        # Camada 0 — meta por fundo
        for meta in MetaFundo.query.all():
            val = request.form.get(f"meta_{meta.ticker}")
            if val is not None:
                meta.meta_pct = float(val)

        # Camada 1 — guardrails de segmento
        for seg in MetaSegmento.query.all():
            piso = request.form.get(f"piso_seg_{seg.segmento}")
            teto = request.form.get(f"teto_seg_{seg.segmento}")
            if piso:
                seg.piso_pct = float(piso)
            if teto:
                seg.teto_pct = float(teto)

        # Camada 2 — guardrails de categoria
        for cat in MetaCategoria.query.all():
            piso = request.form.get(f"piso_cat_{cat.categoria}")
            teto = request.form.get(f"teto_cat_{cat.categoria}")
            if piso:
                cat.piso_pct = float(piso)
            if teto:
                cat.teto_pct = float(teto)

        db.session.commit()
        flash("Metas atualizadas com sucesso.", "success")
        return redirect(url_for("main.metas"))

    gaps          = calcular_gaps_metas()
    meta_segmentos = MetaSegmento.query.order_by(MetaSegmento.segmento).all()
    meta_categorias = MetaCategoria.query.order_by(MetaCategoria.categoria).all()

    # Agrega peso real por segmento e categoria para exibir na página
    peso_seg = {}
    peso_cat = {}
    for g in gaps:
        peso_seg[g["segmento"]]  = round(peso_seg.get(g["segmento"], 0)  + g["peso_real"], 1)
        peso_cat[g["categoria"]] = round(peso_cat.get(g["categoria"], 0) + g["peso_real"], 1)

    return render_template(
        "metas.html",
        gaps=gaps,
        meta_segmentos=meta_segmentos,
        meta_categorias=meta_categorias,
        peso_seg=peso_seg,
        peso_cat=peso_cat,
    )


@bp.route("/recomendar", methods=["GET", "POST"])
def recomendar():
    """
    GET  → mostra formulário (valor disponível).
    POST → roda o motor de recomendação e exibe o resultado.
    """
    resultado = None
    valor_str = ""

    if request.method == "POST":
        valor_str = request.form.get("valor", "0").replace(",", ".")
        try:
            valor = float(valor_str)
            if valor > 0:
                resultado = calcular_recomendacao(valor)
            else:
                flash("Informe um valor positivo.", "warning")
        except ValueError:
            flash("Valor inválido.", "warning")

    cenario = ConfigCenario.query.get(1)
    return render_template("recomendar.html", resultado=resultado,
                           valor_str=valor_str, cenario=cenario)


@bp.route("/config", methods=["GET", "POST"])
def config():
    """
    Cenário macro é calculado automaticamente (Selic via BCB + COPOM + eleições).
    Esta rota permite: forçar atualização da Selic e editar preços-alvo.
    """
    cenario = ConfigCenario.query.get(1)
    precos  = PrecoAlvo.query.order_by(PrecoAlvo.ticker).all()

    if request.method == "POST":
        acao = request.form.get("acao")

        if acao == "atualizar_selic":
            ok = atualizar_cenario_automatico()
            if ok:
                flash("Selic e cenário atualizados automaticamente.", "success")
            else:
                flash("Não foi possível conectar à API do Banco Central. Tente novamente.", "warning")
            return redirect(url_for("main.config"))

        elif acao == "precos":
            for preco in precos:
                val   = request.form.get(f"alvo_{preco.ticker}")
                ativo = request.form.get(f"ativo_{preco.ticker}") == "1"
                if val:
                    preco.preco_alvo = float(val)
                preco.ativo = ativo
            novo_ticker = request.form.get("novo_ticker", "").upper().strip()
            novo_alvo   = request.form.get("novo_alvo", "")
            if novo_ticker and novo_alvo:
                existente = PrecoAlvo.query.get(novo_ticker)
                if existente:
                    existente.preco_alvo = float(novo_alvo)
                    existente.ativo = True
                else:
                    db.session.add(PrecoAlvo(ticker=novo_ticker,
                                             preco_alvo=float(novo_alvo), ativo=True))
            db.session.commit()
            flash("Preços-alvo atualizados.", "success")

        return redirect(url_for("main.config"))

    # Auto-atualiza se os dados são de um dia anterior
    if not cenario or not cenario.atualizado_em or cenario.atualizado_em < date.today():
        atualizar_cenario_automatico()
        cenario = ConfigCenario.query.get(1)

    acumulacao_eleitoral, aviso_campanha = _periodo_eleitoral()

    return render_template(
        "config.html",
        cenario=cenario,
        precos=precos,
        acumulacao_eleitoral=acumulacao_eleitoral,
        aviso_campanha=aviso_campanha,
    )
