# FII Tracker 📊

Aplicação web para acompanhar sua carteira de Fundos Imobiliários (FIIs).

## Funcionalidades

- ✅ Cadastrar posições (ticker, cotas, preço de compra)
- ✅ Registrar proventos recebidos
- ✅ Registrar reinvestimentos (aporte + proventos)
- ✅ Cotação em tempo real via API
- ✅ Dashboard com patrimônio, lucro/prejuízo e yield

## Stack

- **Python 3.10+**
- **Flask** — framework web
- **SQLAlchemy** — ORM para banco de dados
- **SQLite** — banco de dados local
- **Jinja2** — templates HTML
- **brapi.dev** — API de cotações de FIIs

## Como rodar

```bash
# 1. Clone o repositório
git clone https://github.com/SEU_USUARIO/fii-tracker.git
cd fii-tracker

# 2. Crie um ambiente virtual
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Rode o app
python app.py

# 5. Acesse no navegador
# http://localhost:5000
```

## Estrutura do Projeto

```
fii-tracker/
├── app.py              ← Arquivo principal (cria o Flask e o banco)
├── models.py           ← Modelos do banco de dados (tabelas)
├── routes.py           ← Rotas/páginas do app
├── services.py         ← Lógica de negócio (cálculos, API de cotação)
├── templates/          ← HTMLs (Jinja2)
│   ├── base.html       ← Layout comum (header, menu)
│   ├── dashboard.html  ← Tela principal
│   ├── posicoes.html   ← Lista e cadastro de posições
│   └── proventos.html  ← Registro de proventos
├── static/
│   └── style.css       ← Estilos
├── requirements.txt    ← Dependências Python
├── .gitignore          ← Arquivos ignorados pelo Git
└── README.md           ← Este arquivo
```

## Próximos passos

- [ ] Histórico de evolução do patrimônio
- [ ] Gráficos (Chart.js)
- [ ] Comparação com IFIX
- [ ] Exportar CSV/Excel
