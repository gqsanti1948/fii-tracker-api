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
from datetime import date, datetime, timedelta, timezone

# Brasília é UTC-3 permanente (sem horário de verão desde 2019).
# Usar timezone.utc → astimezone garante conversão correta
# independente do timezone configurado no sistema operacional.
BRASILIA = timezone(timedelta(hours=-3))


def hora_brasilia() -> datetime:
    """Retorna o datetime atual no fuso de Brasília (UTC-3)."""
    return datetime.now(timezone.utc).astimezone(BRASILIA)


def mercado_aberto() -> bool:
    """
    Retorna True se a B3 está aberta agora.
    Critérios: dias úteis (seg-sex) entre 10h e 17h horário de Brasília.
    Feriados não são verificados (requereria API externa).
    """
    agora = hora_brasilia()
    if agora.weekday() >= 5:          # sábado=5, domingo=6
        return False
    return 10 <= agora.hour < 17
from models import (db, Posicao, Provento, HistoricoPatrimonio,
                    MetaFundo, MetaSegmento, MetaCategoria,
                    ConfigCenario, PrecoAlvo)
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
    Salva o patrimônio atual com timestamp em UTC-3 (Brasília).

    Ordem de verificação (da mais barata para a mais cara):
      1. mercado_aberto()        → só checagem de hora, zero I/O
      2. throttle (15 min)       → 1 SELECT leve no banco
      3. calcular_resumo_carteira() → chamadas à API externa
      4. INSERT no banco
    Se qualquer etapa falhar, as seguintes não são executadas.
    """
    # ── 1. Mercado fechado → saída imediata, sem tocar no banco nem na API ──
    if not mercado_aberto():
        return

    # ── 2. Throttle: não grava se o último snapshot tem menos de 15 min ──
    agora_br = hora_brasilia()
    agora    = agora_br.replace(tzinfo=None)   # sem tz para compatibilidade com SQLite

    ultimo = (
        HistoricoPatrimonio.query
        .order_by(HistoricoPatrimonio.data_hora.desc())
        .first()
    )
    if ultimo and (agora - ultimo.data_hora) < timedelta(hours=1):
        return

    # ── 3. Busca patrimônio atual via API ────────────────────────────────
    resumo = calcular_resumo_carteira()
    if resumo["patrimonio_atual"] <= 0:
        return

    # ── 4. Persiste o snapshot ───────────────────────────────────────────
    snapshot = HistoricoPatrimonio(data_hora=agora, valor=resumo["patrimonio_atual"])
    db.session.add(snapshot)
    db.session.commit()
    print(f"Snapshot registrado: R$ {resumo['patrimonio_atual']:.2f} às {agora.strftime('%H:%M')} (Brasília)")


def buscar_proventos_mensais() -> list[dict]:
    """
    Agrupa proventos por mês em ordem cronológica.
    Retorna lista de {mes: "MM/YYYY", total: float}.
    """
    proventos = Provento.query.order_by(Provento.data_pagamento).all()
    por_mes: dict[str, float] = {}
    for p in proventos:
        chave = p.data_pagamento.strftime("%m/%Y")
        por_mes[chave] = round(por_mes.get(chave, 0) + p.valor_total, 2)

    # Ordena cronologicamente (strptime garante ordem correta mesmo com meses < 10)
    from datetime import datetime as _dt
    itens = sorted(por_mes.items(), key=lambda x: _dt.strptime(x[0], "%m/%Y"))
    return [{"mes": k, "total": v} for k, v in itens]


def calcular_previsao_proventos() -> dict:
    """
    Prevê o próximo provento de cada ticker da carteira com base no histórico local.

    Para cada ticker:
      - Data estimada: mesmo dia do mês do último pagamento, mês seguinte.
      - Valor estimado: média dos últimos 3 proventos registrados.
      - sem_historico=True quando não há proventos registrados ainda.

    Retorna dict com lista de previsões (todos os tickers da carteira)
    e total estimado para tickers com histórico.
    """
    from calendar import monthrange

    def _proximo_mes(dt: date) -> date:
        mes = dt.month + 1
        ano = dt.year
        if mes > 12:
            mes = 1
            ano += 1
        dia = min(dt.day, monthrange(ano, mes)[1])
        return date(ano, mes, dia)

    cotas_por_ticker: dict[str, int] = {}
    for pos in Posicao.query.all():
        cotas_por_ticker[pos.ticker] = cotas_por_ticker.get(pos.ticker, 0) + pos.quantidade

    hoje = hora_brasilia().date()

    previsoes = []
    for ticker in sorted(cotas_por_ticker):
        historico = (
            Provento.query
            .filter_by(ticker=ticker)
            .order_by(Provento.data_pagamento.desc())
            .limit(6)
            .all()
        )

        if not historico:
            previsoes.append({
                "ticker":           ticker,
                "ultimo_pagamento": None,
                "valor_estimado":   None,
                "proxima_data":     None,
                "status":           None,
                "sem_historico":    True,
            })
            continue

        ultimos3  = historico[:3]
        valor_est = round(sum(p.valor_total for p in ultimos3) / len(ultimos3), 2)
        prox_data = _proximo_mes(historico[0].data_pagamento)
        status    = "previsto" if prox_data >= hoje else "aguardando"

        previsoes.append({
            "ticker":           ticker,
            "ultimo_pagamento": historico[0].data_pagamento,
            "valor_estimado":   valor_est,
            "proxima_data":     prox_data,
            "status":           status,
            "sem_historico":    False,
        })

    previsoes.sort(key=lambda x: (x["sem_historico"], x["proxima_data"] or date.max))
    total_estimado = round(
        sum(p["valor_estimado"] for p in previsoes if not p["sem_historico"]), 2
    )

    return {"previsoes": previsoes, "total_estimado": total_estimado}


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


# ─────────────────────────────────────────────
#  SELIC E CENÁRIO AUTOMÁTICO
# ─────────────────────────────────────────────

# Calendário oficial de reuniões do COPOM (datas de divulgação da decisão).
# Fonte: https://www.bcb.gov.br/controleinflacao/calendarioreunioescopom
# Atualizar anualmente com o calendário publicado pelo BCB em outubro/novembro.
COPOM_REUNIOES: list[date] = [
    # 2026
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 5, 6),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
]


def _buscar_serie_selic(data_inicial: date, data_final: date) -> dict[date, float]:
    """
    Busca a série SGS 432 do BCB entre duas datas.
    Retorna dict {date: taxa} para lookup rápido por data.
    """
    try:
        url = (
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados"
            f"?dataInicial={data_inicial.strftime('%d/%m/%Y')}"
            f"&dataFinal={data_final.strftime('%d/%m/%Y')}"
            "&formato=json"
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        result = {}
        for d in r.json():
            partes = d["data"].split("/")
            dt = date(int(partes[2]), int(partes[1]), int(partes[0]))
            result[dt] = float(d["valor"])
        return result
    except Exception as e:
        print(f"Erro ao buscar série Selic BCB: {e}")
        return {}


def buscar_selic() -> float | None:
    """Retorna a meta Selic atual (% a.a.) buscando os últimos 7 dias da série."""
    hoje = hora_brasilia().date()
    serie = _buscar_serie_selic(hoje - timedelta(days=7), hoje)
    if serie:
        return serie[max(serie)]
    return None


def _taxa_na_reuniao(reuniao: date, serie: dict[date, float]) -> float | None:
    """
    Retorna a taxa Selic vigente na data da reunião.
    Tenta a data exata e os 5 dias seguintes (tolerância para feriados).
    """
    for delta in range(6):
        dt = reuniao + timedelta(days=delta)
        if dt in serie:
            return serie[dt]
    return None


def _decisoes_copom_recentes(n: int = 3) -> list[float]:
    """
    Retorna as taxas das últimas N reuniões do COPOM já realizadas,
    em ordem cronológica (mais antiga primeiro).

    Diferente de detectar apenas mudanças: inclui reuniões de manutenção,
    pois usamos o calendário oficial e buscamos a taxa vigente em cada data.
    """
    hoje = hora_brasilia().date()
    reunioes_passadas = sorted(
        [r for r in COPOM_REUNIOES if r <= hoje], reverse=True
    )[:n][::-1]   # pega as N mais recentes, reordena ascendente

    if not reunioes_passadas:
        return []

    serie = _buscar_serie_selic(reunioes_passadas[0], hoje)
    if not serie:
        return []

    taxas = []
    for reuniao in reunioes_passadas:
        taxa = _taxa_na_reuniao(reuniao, serie)
        if taxa is not None:
            taxas.append(taxa)

    return taxas


def _periodo_eleitoral() -> tuple[bool, bool]:
    """
    Retorna (acumulacao_ativa, aviso_campanha).

    Eleições federais brasileiras: outubro de anos onde ano % 4 == 2
    (2022, 2026, 2030…).

      Julho e agosto     → incerteza máxima   → modo_acumulacao ativo
      Setembro e outubro → campanha formal    → apenas aviso nas recomendações
      Demais meses       → sem efeito eleitoral
    """
    agora = hora_brasilia()
    if agora.year % 4 != 2:
        return False, False
    if agora.month in {7, 8}:
        return True, False
    if agora.month in {9, 10}:
        return False, True
    return False, False


def _inferir_cenario(taxas_reunioes: list[float], selic_atual: float) -> tuple[str, bool]:
    """
    Retorna (cenario, modo_acumulacao) com base nas últimas decisões do COPOM.

    Jul/ago de ano eleitoral sobrescreve → volatilidade + acumulação.

    Tabela de decisão (última reunião × anterior):
      corte ≥ 0.5          | qualquer          → "corte"
      alta                 | qualquer          → "volatilidade"
      corte pequeno        | corte pequeno     → "corte"
      manutenção           | manutenção        → "volatilidade"
      corte pequeno        | manutenção        → "estavel"
      manutenção           | corte pequeno     → "estavel"
      sem histórico (< 2)  | —                 → fallback por Selic absoluta
    """
    acumulacao_eleitoral, _ = _periodo_eleitoral()
    if acumulacao_eleitoral:
        return "volatilidade", True

    if len(taxas_reunioes) >= 2:
        ultimo_delta   = round(taxas_reunioes[-1] - taxas_reunioes[-2], 4)

        if ultimo_delta <= -0.50:            # corte grande
            return "corte", False

        if ultimo_delta > 0:                 # alta
            return "volatilidade", False

        # Manutenção (0) ou corte pequeno (< 0.5) — precisa da reunião anterior
        if len(taxas_reunioes) >= 3:
            penultimo_delta = round(taxas_reunioes[-2] - taxas_reunioes[-3], 4)

            if ultimo_delta < 0 and penultimo_delta < 0:    # dois cortes seguidos
                return "corte", False

            if ultimo_delta >= 0 and penultimo_delta >= 0:  # duas sem corte
                return "volatilidade", False

        return "estavel", False

    # Fallback: sem histórico COPOM suficiente
    if selic_atual > 13.0:
        return "volatilidade", False
    if selic_atual > 9.0:
        return "estavel", False
    return "corte", False


def atualizar_cenario_automatico() -> bool:
    """
    Busca a Selic atual e as últimas 3 decisões do COPOM pelo calendário oficial,
    calcula cenário e modo_acumulacao e persiste em ConfigCenario.
    Retorna True se conseguiu atualizar, False se a API do BCB falhou.
    """
    selic_atual = buscar_selic()
    if selic_atual is None:
        return False

    taxas   = _decisoes_copom_recentes(3)
    cenario, acumulacao = _inferir_cenario(taxas, selic_atual)

    cfg = ConfigCenario.query.get(1)
    if cfg:
        cfg.selic_atual     = selic_atual
        cfg.cenario         = cenario
        cfg.modo_acumulacao = acumulacao
        cfg.atualizado_em   = date.today()
        db.session.commit()
        print(f"Cenário atualizado: {cenario} | Selic {selic_atual}% | Acumulação {acumulacao}")
    return True


# ─────────────────────────────────────────────
#  ENGINE DE METAS E RECOMENDAÇÃO
# ─────────────────────────────────────────────

def calcular_gaps_metas() -> list[dict]:
    """
    Compara o peso real de cada fundo na carteira com sua meta (MetaFundo).

    Retorna lista de dicts ordenada pelo gap (mais negativo primeiro),
    incluindo os pesos agregados por segmento e categoria.
    Usado tanto pelo motor de recomendação quanto pelo widget do dashboard.
    """
    resumo = calcular_resumo_carteira()

    # Mapa ticker → peso real atual (%)
    peso_por_ticker = {p["ticker"]: p["peso"] for p in resumo["posicoes"]}

    metas = MetaFundo.query.all()
    if not metas:
        return []

    gaps = []
    for meta in metas:
        peso_real = peso_por_ticker.get(meta.ticker, 0.0)
        gap = round(meta.meta_pct - peso_real, 1)

        # Tenta pegar o preço atual do resumo; se o ticker não estiver
        # na carteira ainda, busca a cotação direto.
        pos = next((p for p in resumo["posicoes"] if p["ticker"] == meta.ticker), None)
        preco_atual = pos["preco_atual"] if pos else 0.0

        gaps.append({
            "ticker":    meta.ticker,
            "segmento":  meta.segmento,
            "categoria": meta.categoria,
            "meta_pct":  meta.meta_pct,
            "peso_real": round(peso_real, 1),
            "gap":       gap,          # positivo = abaixo da meta (precisa comprar)
            "preco_atual": preco_atual,
        })

    # Ordena: maior gap positivo primeiro (mais subponderado)
    gaps.sort(key=lambda x: -x["gap"])
    return gaps


def calcular_recomendacao(valor_disponivel: float) -> dict:
    """
    Motor de recomendação em 4 etapas:

    Etapa 1 — Guardrails: verifica se categoria ou segmento saiu do piso/teto.
    Etapa 2 — Cenário macro: filtra quais categorias priorizar.
    Etapa 3 — Gap da meta: ordena candidatos pelo desvio da Camada 0.
    Etapa 4 — Regras de oportunidade: preço abaixo do alvo eleva prioridade.

    Retorna dict com alertas, recomendações, gaps e saldo restante.
    """
    gaps = calcular_gaps_metas()
    if not gaps:
        return {"erro": "Nenhuma meta de fundo cadastrada."}

    # ── Etapa 1: Guardrails ──────────────────────────────────────────
    alertas = []

    # Aviso de campanha eleitoral (set-out de ano eleitoral)
    _, aviso_campanha = _periodo_eleitoral()
    if aviso_campanha:
        alertas.append({
            "tipo": "aviso",
            "msg": "Campanha eleitoral em curso (set–out) — considere cautela extra no aporte.",
        })

    # Pesos por categoria e segmento com base na carteira real
    peso_cat = {}
    peso_seg = {}
    for g in gaps:
        peso_cat[g["categoria"]] = peso_cat.get(g["categoria"], 0) + g["peso_real"]
        peso_seg[g["segmento"]]  = peso_seg.get(g["segmento"], 0)  + g["peso_real"]

    for cat in MetaCategoria.query.all():
        atual = round(peso_cat.get(cat.categoria, 0), 1)
        if atual < cat.piso_pct:
            alertas.append({
                "tipo": "urgente",
                "msg":  f"{cat.categoria.title()} em {atual}% — abaixo do piso ({cat.piso_pct}%)",
            })
        elif atual > cat.teto_pct:
            alertas.append({
                "tipo": "aviso",
                "msg":  f"{cat.categoria.title()} em {atual}% — acima do teto ({cat.teto_pct}%)",
            })

    for seg in MetaSegmento.query.all():
        atual = round(peso_seg.get(seg.segmento, 0), 1)
        if atual < seg.piso_pct:
            alertas.append({
                "tipo": "aviso",
                "msg":  f"{seg.segmento} em {atual}% — abaixo do piso ({seg.piso_pct}%)",
            })
        elif atual > seg.teto_pct:
            alertas.append({
                "tipo": "aviso",
                "msg":  f"{seg.segmento} em {atual}% — acima do teto ({seg.teto_pct}%)",
            })

    # Concentração excessiva em fundo individual (> 25%)
    for g in gaps:
        if g["peso_real"] > 25:
            alertas.append({
                "tipo": "aviso",
                "msg":  f"Concentração excessiva em {g['ticker']} ({g['peso_real']}%)",
            })

    # ── Etapa 2: Cenário macro ───────────────────────────────────────
    cenario_cfg = ConfigCenario.query.get(1)
    cenario     = cenario_cfg.cenario if cenario_cfg else "estavel"
    selic       = cenario_cfg.selic_atual if cenario_cfg else 14.75
    acumulacao  = cenario_cfg.modo_acumulacao if cenario_cfg else False

    # Modo acumulação: só BTHF11
    if acumulacao:
        preco_bthf = next((g["preco_atual"] for g in gaps if g["ticker"] == "BTHF11"), 9.29)
        preco_bthf = preco_bthf or 9.29
        cotas = int(valor_disponivel // preco_bthf)
        return {
            "alertas": alertas,
            "cenario": cenario,
            "selic": selic,
            "acumulacao": True,
            "valor_disponivel": valor_disponivel,
            "valor_restante": round(valor_disponivel - cotas * preco_bthf, 2),
            "total_investido": round(cotas * preco_bthf, 2),
            "recomendacoes": [{
                "ticker": "BTHF11",
                "cotas": cotas,
                "preco": preco_bthf,
                "custo": round(cotas * preco_bthf, 2),
                "motivo": "Modo acumulação ativo — aguardando desconto para tijolo",
                "tipo": "acumulacao",
            }],
            "gaps": gaps,
        }

    # Filtro de categoria por cenário
    if cenario == "corte":
        cats_ok = {"tijolo", "multi"}
    elif cenario == "estavel":
        cats_ok = {"papel", "multi"}
    else:   # volatilidade sem acumulação → sem filtro
        cats_ok = None

    candidatos = [g for g in gaps if cats_ok is None or g["categoria"] in cats_ok]
    if not candidatos:
        candidatos = gaps   # fallback se filtro zerar a lista

    # ── Etapa 4: Regras de oportunidade ─────────────────────────────
    precos_alvo = {p.ticker: p.preco_alvo
                   for p in PrecoAlvo.query.filter_by(ativo=True).all()}

    for c in candidatos:
        c["oportunidade"] = False
        alvo = precos_alvo.get(c["ticker"])
        if alvo and c["preco_atual"] > 0 and c["preco_atual"] < alvo:
            c["oportunidade"] = True
            alertas.append({
                "tipo": "oportunidade",
                "msg":  f"{c['ticker']} abaixo do preço-alvo "
                        f"(R$ {c['preco_atual']:.2f} < R$ {alvo:.2f})",
            })

    # Ordenação final: oportunidade > gap desc
    candidatos.sort(key=lambda x: (not x["oportunidade"], -x["gap"]))

    # ── Etapa 3: Alocação greedy ─────────────────────────────────────
    # Compra 1 cota por fundo candidato (do maior gap ao menor),
    # depois usa o restante para BTHF11 (cota mais barata da carteira).
    recomendacoes = []
    sobra = valor_disponivel

    for c in candidatos:
        preco = c["preco_atual"]
        if preco <= 0 or preco > sobra:
            continue

        tipo   = "principal" if not recomendacoes else "secundaria"
        motivo = f"Gap de {c['gap']:+.1f} p.p. em relação à meta"
        if c["oportunidade"]:
            motivo += " + abaixo do preço-alvo"

        custo = round(preco, 2)
        sobra = round(sobra - custo, 2)

        recomendacoes.append({
            "ticker": c["ticker"],
            "cotas":  1,
            "preco":  preco,
            "custo":  custo,
            "motivo": motivo,
            "tipo":   tipo,
        })

    # Sobra → BTHF11 (se não foi recomendado antes)
    if not any(r["ticker"] == "BTHF11" for r in recomendacoes):
        preco_bthf = next((g["preco_atual"] for g in gaps if g["ticker"] == "BTHF11"), 0)
        if preco_bthf and sobra >= preco_bthf:
            cotas_bthf = int(sobra // preco_bthf)
            custo_bthf = round(cotas_bthf * preco_bthf, 2)
            sobra      = round(sobra - custo_bthf, 2)
            recomendacoes.append({
                "ticker": "BTHF11",
                "cotas":  cotas_bthf,
                "preco":  preco_bthf,
                "custo":  custo_bthf,
                "motivo": "Destino padrão para valor residual",
                "tipo":   "sobra",
            })

    return {
        "alertas":         alertas,
        "recomendacoes":   recomendacoes,
        "gaps":            gaps,
        "cenario":         cenario,
        "selic":           selic,
        "acumulacao":      False,
        "valor_disponivel": valor_disponivel,
        "total_investido": round(valor_disponivel - sobra, 2),
        "valor_restante":  sobra,
    }
