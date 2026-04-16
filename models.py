"""
models.py — Definição das tabelas do banco de dados.

Cada classe aqui vira uma tabela no SQLite.
O SQLAlchemy traduz essas classes Python em SQL automaticamente.

CONCEITO IMPORTANTE:
- db.Column define uma coluna da tabela
- db.Integer, db.String, db.Float, db.Date são os tipos de dados
- primary_key=True significa que é o identificador único da linha
- nullable=False significa que o campo é obrigatório
"""

from flask_sqlalchemy import SQLAlchemy
from datetime import date

# Cria a instância do SQLAlchemy (será conectada ao Flask no app.py)
db = SQLAlchemy()


class Posicao(db.Model):
    """
    Cada linha representa UMA compra de cotas.
    
    Se você comprou BTLG11 duas vezes (em datas/preços diferentes),
    terá duas linhas aqui. O preço médio é CALCULADO, não armazenado.
    
    Exemplo:
        Posicao(ticker="BTLG11", quantidade=5, preco_unitario=103.87, data_compra=date(2026, 4, 15))
    """
    __tablename__ = "posicoes"

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(10), nullable=False)        # Ex: "BTLG11"
    quantidade = db.Column(db.Integer, nullable=False)        # Ex: 5
    preco_unitario = db.Column(db.Float, nullable=False)      # Ex: 103.87
    data_compra = db.Column(db.Date, nullable=False, default=date.today)
    # nullable=True porque posições já cadastradas não têm esse dado ainda.
    # O segmento será inferido automaticamente pela brapi ao cadastrar.
    segmento = db.Column(db.String(50), nullable=True)

    def __repr__(self):
        return f"<Posicao {self.ticker} | {self.quantidade}x R${self.preco_unitario:.2f}>"


class Provento(db.Model):
    """
    Cada linha representa UM pagamento de dividendo recebido.
    
    O valor_total é calculado como valor_por_cota * cotas que você tinha na data.
    Mas para simplificar, vamos registrar o valor total recebido diretamente.
    
    Exemplo:
        Provento(ticker="BTLG11", valor_por_cota=0.80, valor_total=4.00, data_pagamento=date(2026, 4, 15))
    """
    __tablename__ = "proventos"

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(10), nullable=False)         # Ex: "BTLG11"
    valor_total = db.Column(db.Float, nullable=False)         
    data_pagamento = db.Column(db.Date, nullable=False, default=date.today)

    def __repr__(self):
        return f"<Provento {self.ticker} | R${self.valor_total:.2f} em {self.data_pagamento}>"


class HistoricoPatrimonio(db.Model):
    """
    Snapshot do patrimônio com data e hora completos.
    Permite múltiplos registros por dia (intraday) para o gráfico 1D.
    O throttle de 15 minutos é aplicado em Python, não no banco.
    """
    __tablename__ = "historico_patrimonio"

    id        = db.Column(db.Integer, primary_key=True)
    data_hora = db.Column(db.DateTime, nullable=False)
    valor     = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<HistoricoPatrimonio {self.data_hora} | R${self.valor:.2f}>"


# ─────────────────────────────────────────────
#  FEATURE: META DE ALOCAÇÃO
# ─────────────────────────────────────────────

class MetaFundo(db.Model):
    """
    Camada 0 — peso-alvo de cada FII individualmente.
    É a fonte de verdade: as metas de segmento e categoria
    são derivadas somando os fundos de cada grupo.
    """
    __tablename__ = "meta_fundos"

    ticker    = db.Column(db.String(10), primary_key=True)   # "BTLG11"
    segmento  = db.Column(db.String(50), nullable=False)     # "Logística"
    categoria = db.Column(db.String(20), nullable=False)     # "tijolo" | "papel" | "multi"
    meta_pct  = db.Column(db.Float,      nullable=False)     # 8.0 (% do patrimônio)

    def __repr__(self):
        return f"<MetaFundo {self.ticker} | meta {self.meta_pct}%>"


class MetaSegmento(db.Model):
    """
    Camada 1 — guardrails por segmento.
    Piso e teto definem a faixa aceitável; se a alocação real sair
    desse intervalo, o motor de recomendação emite alerta.
    """
    __tablename__ = "meta_segmentos"

    segmento = db.Column(db.String(50), primary_key=True)
    piso_pct = db.Column(db.Float, nullable=False)   # mínimo aceitável (%)
    teto_pct = db.Column(db.Float, nullable=False)   # máximo aceitável (%)

    def __repr__(self):
        return f"<MetaSegmento {self.segmento} | {self.piso_pct}–{self.teto_pct}%>"


class MetaCategoria(db.Model):
    """
    Camada 2 — guardrails macro (papel / tijolo / multi).
    Proteção contra desequilíbrio macroeconômico na carteira.
    """
    __tablename__ = "meta_categorias"

    categoria = db.Column(db.String(20), primary_key=True)   # "tijolo" | "papel" | "multi"
    piso_pct  = db.Column(db.Float, nullable=False)
    teto_pct  = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<MetaCategoria {self.categoria} | {self.piso_pct}–{self.teto_pct}%>"


class ConfigCenario(db.Model):
    """
    Cenário macro atual. Existe sempre exatamente 1 linha (id=1).
    O cenário orienta quais categorias de FII priorizar no aporte.
    """
    __tablename__ = "config_cenario"

    id              = db.Column(db.Integer, primary_key=True)
    cenario         = db.Column(db.String(20), nullable=False, default="estavel")
    # "corte"       → Selic caindo   → priorizar tijolo
    # "estavel"     → Selic mantida  → priorizar papel
    # "volatilidade"→ Crise          → modo acumulação
    selic_atual       = db.Column(db.Float, nullable=False, default=14.75)
    modo_acumulacao   = db.Column(db.Boolean, nullable=False, default=False)
    atualizado_em     = db.Column(db.Date, nullable=True)

    def __repr__(self):
        return f"<ConfigCenario {self.cenario} | Selic {self.selic_atual}%>"


class PrecoAlvo(db.Model):
    """
    Preço-alvo de compra por ticker.
    Se cotacao_atual < preco_alvo, o motor emite alerta de oportunidade
    e eleva a prioridade desse fundo na recomendação.
    """
    __tablename__ = "precos_alvo"

    ticker     = db.Column(db.String(10), primary_key=True)
    preco_alvo = db.Column(db.Float, nullable=False)
    ativo      = db.Column(db.Boolean, nullable=False, default=True)

    def __repr__(self):
        return f"<PrecoAlvo {self.ticker} | R${self.preco_alvo}>"
