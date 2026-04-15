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
from models import db, Posicao, Provento
from services import calcular_resumo_carteira
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
    resumo = calcular_resumo_carteira()
    return render_template("dashboard.html", resumo=resumo)


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
        origem = request.form.get("origem", "aporte")

        # Converte a data (o formulário manda como "2026-04-15")
        if data_str:
            data_compra = date.fromisoformat(data_str)
        else:
            data_compra = date.today()

        # Cria o objeto e salva no banco
        nova_posicao = Posicao(
            ticker=ticker,
            quantidade=quantidade,
            preco_unitario=preco,
            data_compra=data_compra,
            origem=origem,
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


@bp.route("/proventos", methods=["GET", "POST"])
def proventos():
    """
    Lista proventos e permite registrar novos.
    """
    if request.method == "POST":
        ticker = request.form["ticker"].upper().strip()
        valor_por_cota = float(request.form["valor_por_cota"])
        valor_total = float(request.form["valor_total"])
        data_str = request.form.get("data_pagamento", "")

        if data_str:
            data_pagamento = date.fromisoformat(data_str)
        else:
            data_pagamento = date.today()

        novo_provento = Provento(
            ticker=ticker,
            valor_por_cota=valor_por_cota,
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
