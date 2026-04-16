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
from datetime import date
from models import db, Posicao, MetaFundo, MetaSegmento, MetaCategoria, ConfigCenario, PrecoAlvo
from routes import bp
from services import buscar_cotacao, registrar_snapshot_patrimonio
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

def _iniciar_scheduler(app):
    """
    Agenda um snapshot automático às 16:50 hora de Brasília de segunda a sexta,
    garantindo ao menos um ponto de dados por dia útil mesmo que o usuário
    não acesse o app.

    Brasília é UTC-3 fixo (sem horário de verão desde 2019).
    16:50 BRT → 16:50 + 3h = 19:50 UTC.
    """
    scheduler = BackgroundScheduler()

    def _job():
        with app.app_context():
            registrar_snapshot_patrimonio()

    # 16:50 BRT = 19:50 UTC (UTC-3, sem DST)
    scheduler.add_job(
        func=_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=19, minute=50, timezone="UTC"),
        id="snapshot_fechamento",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler


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
        _seed_metas_iniciais()
        registrar_snapshot_patrimonio()

    _iniciar_scheduler(app)

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
        # Migração 1: coluna segmento em posicoes
        colunas = [row[1] for row in conn.execute(
            db.text("PRAGMA table_info(posicoes)")
        )]
        if "segmento" not in colunas:
            conn.execute(db.text(
                "ALTER TABLE posicoes ADD COLUMN segmento VARCHAR(50)"
            ))
            conn.commit()
            print("Migração aplicada: coluna 'segmento' adicionada.")

        # Migração 2: historico_patrimonio de Date(unique) para DateTime
        # SQLite não suporta DROP CONSTRAINT, então recriamos a tabela.
        hist_cols = [row[1] for row in conn.execute(
            db.text("PRAGMA table_info(historico_patrimonio)")
        )]
        if "data" in hist_cols and "data_hora" not in hist_cols:
            conn.execute(db.text("""
                CREATE TABLE historico_patrimonio_new (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    data_hora DATETIME NOT NULL,
                    valor     FLOAT NOT NULL
                )
            """))
            conn.execute(db.text("""
                INSERT INTO historico_patrimonio_new (data_hora, valor)
                SELECT data || ' 10:00:00', valor FROM historico_patrimonio
            """))
            conn.execute(db.text("DROP TABLE historico_patrimonio"))
            conn.execute(db.text(
                "ALTER TABLE historico_patrimonio_new RENAME TO historico_patrimonio"
            ))
            conn.commit()
            print("Migração aplicada: historico_patrimonio atualizado para datetime.")


def _seed_metas_iniciais():
    """
    Popula as tabelas de meta com os dados iniciais da carteira
    apenas se ainda estiverem vazias — nunca sobrescreve edições do usuário.
    """
    if MetaFundo.query.first():
        return   # já foi semeado

    # ── Camada 0: meta por fundo ────────────────────────────────────
    fundos = [
        ("BTHF11", "Multimercado",       "multi",   20.0),
        ("RCRB11", "Lajes Corporativas", "tijolo",  19.0),
        ("KNIP11", "Papel / CRI",        "papel",   14.0),
        ("VILG11", "Logística",          "tijolo",  12.0),
        ("XPML11", "Shoppings",          "tijolo",   9.0),
        ("PMLL11", "Shoppings",          "tijolo",   9.0),
        ("KNCR11", "Papel / CRI",        "papel",    9.0),
        ("BTLG11", "Logística",          "tijolo",   8.0),
    ]
    for ticker, segmento, categoria, meta_pct in fundos:
        db.session.add(MetaFundo(
            ticker=ticker, segmento=segmento,
            categoria=categoria, meta_pct=meta_pct,
        ))

    # ── Camada 1: guardrails por segmento ───────────────────────────
    segmentos = [
        ("Logística",          15.0, 28.0),
        ("Multimercado",       15.0, 25.0),
        ("Lajes Corporativas", 12.0, 25.0),
        ("Shoppings",          12.0, 25.0),
        ("Papel / CRI",        13.0, 35.0),
    ]
    for segmento, piso, teto in segmentos:
        db.session.add(MetaSegmento(segmento=segmento, piso_pct=piso, teto_pct=teto))

    # ── Camada 2: guardrails por categoria ──────────────────────────
    categorias = [
        ("tijolo", 45.0, 65.0),
        ("papel",  18.0, 40.0),
        ("multi",  15.0, 25.0),
    ]
    for categoria, piso, teto in categorias:
        db.session.add(MetaCategoria(categoria=categoria, piso_pct=piso, teto_pct=teto))

    # ── Cenário inicial ─────────────────────────────────────────────
    db.session.add(ConfigCenario(
        id=1, cenario="estavel", selic_atual=14.75,
        modo_acumulacao=False, atualizado_em=date.today(),
    ))

    # ── Preços-alvo iniciais ────────────────────────────────────────
    precos = [
        ("XPML11", 105.0),
        ("PMLL11", 105.0),
        ("RCRB11", 130.0),
        ("VILG11",  95.0),
    ]
    for ticker, alvo in precos:
        db.session.add(PrecoAlvo(ticker=ticker, preco_alvo=alvo, ativo=True))

    db.session.commit()
    print("Seed de metas iniciais aplicado.")


# Quando você roda `python app.py`, esse bloco executa
if __name__ == "__main__":
    app = create_app()
    # debug=True faz o servidor recarregar quando você muda o código
    # NUNCA use debug=True em produção
    app.run(debug=True, port=5000)
