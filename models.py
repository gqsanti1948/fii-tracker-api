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
    origem = db.Column(db.String(20), default="aporte")       # "aporte" ou "reinvestimento"

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
    valor_por_cota = db.Column(db.Float, nullable=False)      # Ex: 0.80
    valor_total = db.Column(db.Float, nullable=False)          # Ex: 4.00 (0.80 * 5 cotas)
    data_pagamento = db.Column(db.Date, nullable=False, default=date.today)

    def __repr__(self):
        return f"<Provento {self.ticker} | R${self.valor_total:.2f} em {self.data_pagamento}>"
