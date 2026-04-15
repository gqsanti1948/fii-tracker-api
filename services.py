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
from datetime import date, datetime, timedelta
from models import db, Posicao, Provento, HistoricoPatrimonio
from sqlalchemy import func

BRAPI_TOKEN = "dcdMUi2s2j4rJcEEhY8qBc"

# Cada entrada é (palavras_chave, nome_do_segmento).
# A primeira regra que encontrar alguma palavra no longName do FII vence.
# As palavras são verificadas em minúsculo para ignorar maiúsculas/minúsculas.
_REGRAS_SEGMENTO = [
    (["logistic", "logística", "logistico"],                        "Logística"),
    (["shopping", "mall", "varejo"],                                "Shoppings"),
    (["lajes", "corporat", "corporate", "office"],                  "Lajes Corporativas"),
    (["receb", "rendimento", "credito", "crédito", "cri", "papel"], "Papel / CRI"),
    (["residencial", "habitacional", "resi"],                       "Residencial"),
    (["hotel", "hotelaria"],                                        "Hotelaria"),
    (["fundo de fundos", "fof"],                                    "Fundo de Fundos"),
    (["industrial", "galpao", "galpão"],                            "Industrial"),
    (["hedge fund", "multiestratégia", "multimercado"],             "Multimercado"),
]


def inferir_segmento(long_name: str) -> str:
    """
    Infere o segmento do FII a partir do nome longo retornado pela brapi.

    CONCEITO — por que lower()?
    Comparar strings em minúsculo evita que "Logistica" e "LOGISTICA"
    sejam tratadas como coisas diferentes. É uma boa prática sempre que
    você compara texto que veio de fora (API, usuário, arquivo).

    Retorna "Outros" se nenhuma regra bater — nunca retorna None,
    porque o gráfico precisa de um valor para atribuir a cor.
    """
    nome = long_name.lower()
    for palavras_chave, segmento in _REGRAS_SEGMENTO:
        if any(p in nome for p in palavras_chave):
            return segmento
    return "Outros"


def buscar_cotacao(ticker: str) -> dict | None:
    try:
        url = f"https://brapi.dev/api/quote/{ticker}"
        headers = {
            "Authorization": f"Bearer {BRAPI_TOKEN}"
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "results" in data and len(data["results"]) > 0:
            result = data["results"][0]
            long_name = result.get("longName", ticker)
            return {
                "preco": result.get("regularMarketPrice", 0),
                "nome": long_name,
                "variacao": result.get("regularMarketChangePercent", 0),
                "segmento": inferir_segmento(long_name),
            }

        print(f"Sem resultados para {ticker}: {data}")

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
            Posicao.segmento,
            func.sum(Posicao.quantidade).label("total_cotas"),
            func.sum(Posicao.quantidade * Posicao.preco_unitario).label("total_investido"),
        )
        .group_by(Posicao.ticker, Posicao.segmento)
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
            "segmento": row.segmento or "Outros",
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

    for pos in posicoes:
        pos["peso"] = round((pos["valor_atual"] / patrimonio_atual * 100), 1) if patrimonio_atual > 0 else 0

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


def registrar_snapshot_patrimonio():
    """
    Salva o patrimônio atual com timestamp completo.
    Throttle de 15 minutos: ignora se o último snapshot foi há menos de 15 min.
    """
    agora = datetime.now()
    ultimo = (
        HistoricoPatrimonio.query
        .order_by(HistoricoPatrimonio.data_hora.desc())
        .first()
    )
    if ultimo and (agora - ultimo.data_hora) < timedelta(minutes=15):
        return

    resumo = calcular_resumo_carteira()
    if resumo["patrimonio_atual"] <= 0:
        return

    snapshot = HistoricoPatrimonio(data_hora=agora, valor=resumo["patrimonio_atual"])
    db.session.add(snapshot)
    db.session.commit()
    print(f"Snapshot registrado: R$ {resumo['patrimonio_atual']:.2f} às {agora.strftime('%H:%M')}")


def buscar_historico_patrimonio():
    """
    Retorna lista de snapshots ordenados por data_hora.
    Cada item tem 'data_hora' (string "DD/MM/YYYY HH:MM") e 'valor'.
    O frontend separa 1D (eixo HH:MM) dos demais períodos (agrupados por dia).
    """
    registros = (
        HistoricoPatrimonio.query
        .order_by(HistoricoPatrimonio.data_hora)
        .all()
    )
    return [
        {"data_hora": r.data_hora.strftime("%d/%m/%Y %H:%M"), "valor": r.valor}
        for r in registros
    ]
