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
from models import db
from routes import bp

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

    return app


# Quando você roda `python app.py`, esse bloco executa
if __name__ == "__main__":
    app = create_app()
    # debug=True faz o servidor recarregar quando você muda o código
    # NUNCA use debug=True em produção
    app.run(debug=True, port=5000)
