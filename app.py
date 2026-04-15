"""
app.py — Ponto de entrada do aplicativo.

Este é o arquivo que você roda com `python app.py`.
Ele faz 3 coisas:
1. Cria a instância do Flask
2. Configura o banco de dados
3. Registra as rotas

CONCEITO IMPORTANTE:
- Flask(__name__) cria o "restaurante"
- app.config configura onde fica o banco de dados
- db.init_app(app) conecta o SQLAlchemy ao Flask
- app.register_blueprint(bp) registra todas as rotas de routes.py
- db.create_all() cria as tabelas no banco se não existirem
"""

from flask import Flask
from models import db, Posicao
from routes import bp
from services import buscar_cotacao

def create_app():
    """
    Factory function — cria e configura o app Flask.
    
    Por que usar uma factory?
    - Permite criar múltiplas instâncias (útil para testes)
    - Evita imports circulares
    - É o padrão recomendado pelo Flask
    """
    app = Flask(__name__)

    # Configurações
    app.config["SECRET_KEY"] = "fii-tracker-dev-key-mude-em-producao"
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///fii_tracker.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Inicializa o banco de dados com o app
    db.init_app(app)

    # Registra as rotas
    app.register_blueprint(bp)

    # Cria as tabelas do banco (se não existirem)
    with app.app_context():
        db.create_all()
        _migrar_banco(db)
        _preencher_segmentos_faltantes()

    return app


def _preencher_segmentos_faltantes():
    """
    Preenche o segmento de posições que ainda não têm esse dado.

    CONCEITO — filter(Posicao.segmento == None)
    O SQLAlchemy traduz isso para: WHERE segmento IS NULL
    Buscamos apenas as linhas sem segmento para não desperdiçar
    chamadas de API em posições que já foram classificadas.

    CONCEITO — tickers únicos
    Várias linhas podem ter o mesmo ticker (compras em datas diferentes).
    Usamos um set() para garantir que cada ticker é consultado uma única vez,
    e depois atualizamos todas as linhas daquele ticker de uma vez.
    """
    # Reprocessa NULL e "Outros" — "Outros" é o fallback quando nenhuma regra
    # bateu; se as regras mudaram, precisamos reclassificar esses registros.
    posicoes_sem_segmento = Posicao.query.filter(
        (Posicao.segmento == None) | (Posicao.segmento == "Outros")
    ).all()

    if not posicoes_sem_segmento:
        return

    # Descobre quais tickers únicos precisam ser consultados
    tickers_unicos = {p.ticker for p in posicoes_sem_segmento}
    print(f"Inferindo segmento para: {', '.join(tickers_unicos)}")

    # Busca o segmento uma vez por ticker
    segmentos = {}
    for ticker in tickers_unicos:
        cotacao = buscar_cotacao(ticker)
        segmentos[ticker] = cotacao["segmento"] if cotacao else "Outros"

    # Atualiza todas as posições com o segmento encontrado
    for posicao in posicoes_sem_segmento:
        posicao.segmento = segmentos[posicao.ticker]

    db.session.commit()
    print("Segmentos preenchidos com sucesso.")


def _migrar_banco(db):
    """
    Aplica migrações manuais no banco de dados.

    CONCEITO — por que não usar db.create_all() para isso?
    O create_all() só CRIA tabelas novas. Se a tabela já existe,
    ele ignora — não adiciona colunas novas.

    Para adicionar uma coluna numa tabela existente usamos ALTER TABLE.
    Verificamos antes se a coluna já existe para não dar erro
    caso o app reinicie depois da migração já ter sido aplicada.

    Em projetos maiores isso seria feito com Flask-Migrate (Alembic).
    Para projetos pequenos, essa abordagem manual é suficiente.
    """
    with db.engine.connect() as conn:
        # Busca as colunas atuais da tabela posicoes
        colunas = [row[1] for row in conn.execute(
            db.text("PRAGMA table_info(posicoes)")
        )]
        if "segmento" not in colunas:
            conn.execute(db.text(
                "ALTER TABLE posicoes ADD COLUMN segmento VARCHAR(50)"
            ))
            conn.commit()
            print("Migração aplicada: coluna 'segmento' adicionada.")


# Quando você roda `python app.py`, esse bloco executa
if __name__ == "__main__":
    app = create_app()
    # debug=True faz o servidor recarregar quando você muda o código
    # NUNCA use debug=True em produção
    app.run(debug=True, port=5000)
