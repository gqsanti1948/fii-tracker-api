"""
services.py — Lógica de negócio e integração com API externa.

Este arquivo separa a "inteligência" do app das rotas.
As rotas (routes.py) só cuidam de receber/devolver dados.
A lógica pesada fica aqui.

CONCEITO IMPORTANTE:
- Separar responsabilidades torna o código mais fácil de testar e manter.
- Se amanhã a API de cotação mudar, você só mexe aqui.
"""

import requests
from models import db, Posicao, Provento
from sqlalchemy import func


def buscar_cotacao(ticker: str) -> dict | None:
    """
    Busca a cotação em tempo real de um FII usando a API da brapi.dev.
    
    A brapi.dev é uma API gratuita (com limite) que retorna cotações da B3.
    Endpoint: https://brapi.dev/api/quote/{TICKER}
    
    Retorna:
        dict com 'preco', 'nome', 'variacao' ou None se falhar
    """
    try:
        url = f"https://brapi.dev/api/quote/{ticker}"
        response = requests.get(url, timeout=10)
        data = response.json()

        if "results" in data and len(data["results"]) > 0:
            result = data["results"][0]
            return {
                "preco": result.get("regularMarketPrice", 0),
                "nome": result.get("longName", ticker),
                "variacao": result.get("regularMarketChangePercent", 0),
            }
    except Exception as e:
        print(f"Erro ao buscar cotação de {ticker}: {e}")

    return None


def buscar_cotacoes_carteira(tickers: list[str]) -> dict:
    """
    Busca cotações de vários tickers de uma vez.
    
    Retorna:
        dict no formato {"BTLG11": {"preco": 103.87, ...}, ...}
    """
    cotacoes = {}
    for ticker in tickers:
        cotacao = buscar_cotacao(ticker)
        if cotacao:
            cotacoes[ticker] = cotacao
    return cotacoes


def calcular_resumo_carteira() -> dict:
    """
    Calcula o resumo completo da carteira:
    - Posições consolidadas (agrupadas por ticker)
    - Preço médio de cada FII
    - Total investido
    - Proventos acumulados
    
    CONCEITO IMPORTANTE:
    O preço médio é calculado assim:
        preco_medio = soma(quantidade * preco) / soma(quantidade)
    
    Isso é feito via SQL com GROUP BY, que o SQLAlchemy traduz pra gente.
    """
    # Agrupa posições por ticker e calcula preço médio
    posicoes_agrupadas = (
        db.session.query(
            Posicao.ticker,
            func.sum(Posicao.quantidade).label("total_cotas"),
            func.sum(Posicao.quantidade * Posicao.preco_unitario).label("total_investido"),
        )
        .group_by(Posicao.ticker)
        .all()
    )

    # Monta lista de posições consolidadas
    posicoes = []
    tickers = []
    total_investido_carteira = 0

    for row in posicoes_agrupadas:
        ticker = row.ticker
        total_cotas = row.total_cotas
        total_investido = row.total_investido
        preco_medio = total_investido / total_cotas if total_cotas > 0 else 0

        tickers.append(ticker)
        total_investido_carteira += total_investido

        posicoes.append({
            "ticker": ticker,
            "cotas": total_cotas,
            "preco_medio": round(preco_medio, 2),
            "total_investido": round(total_investido, 2),
        })

    # Busca cotações atuais
    cotacoes = buscar_cotacoes_carteira(tickers)

    # Enriquece posições com dados de mercado
    patrimonio_atual = 0
    for pos in posicoes:
        ticker = pos["ticker"]
        if ticker in cotacoes:
            preco_atual = cotacoes[ticker]["preco"]
            pos["preco_atual"] = preco_atual
            pos["nome"] = cotacoes[ticker]["nome"]
            pos["variacao"] = cotacoes[ticker]["variacao"]
            pos["valor_atual"] = round(preco_atual * pos["cotas"], 2)
            pos["lucro_prejuizo"] = round(pos["valor_atual"] - pos["total_investido"], 2)
            pos["lucro_pct"] = round(
                ((pos["valor_atual"] / pos["total_investido"]) - 1) * 100, 2
            ) if pos["total_investido"] > 0 else 0
            patrimonio_atual += pos["valor_atual"]
        else:
            pos["preco_atual"] = 0
            pos["nome"] = ticker
            pos["variacao"] = 0
            pos["valor_atual"] = 0
            pos["lucro_prejuizo"] = 0
            pos["lucro_pct"] = 0

    # Calcula proventos totais
    proventos_total = db.session.query(
        func.sum(Provento.valor_total)
    ).scalar() or 0

    # Proventos por ticker
    proventos_por_ticker = dict(
        db.session.query(
            Provento.ticker,
            func.sum(Provento.valor_total)
        ).group_by(Provento.ticker).all()
    )

    for pos in posicoes:
        pos["proventos_recebidos"] = round(proventos_por_ticker.get(pos["ticker"], 0), 2)

    return {
        "posicoes": posicoes,
        "total_investido": round(total_investido_carteira, 2),
        "patrimonio_atual": round(patrimonio_atual, 2),
        "lucro_total": round(patrimonio_atual - total_investido_carteira, 2),
        "lucro_total_pct": round(
            ((patrimonio_atual / total_investido_carteira) - 1) * 100, 2
        ) if total_investido_carteira > 0 else 0,
        "proventos_total": round(proventos_total, 2),
        "yield_real": round(
            (proventos_total / total_investido_carteira) * 100, 2
        ) if total_investido_carteira > 0 else 0,
    }
