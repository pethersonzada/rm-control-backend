# RM Control | Backend

API REST para importação e acompanhamento de ocorrências logísticas.

> Versão 0.1.0 — protótipo em desenvolvimento, para execução local.

## Funcionalidades implementadas

- Parser determinístico de listagens tabuladas copiadas do navegador.
- Prévia com alertas de registros incompletos.
- Confirmação de importações e persistência por código de ocorrência.
- Preservação dos itens anteriores quando a nova listagem não os exibe.
- Validação de dados com Pydantic.

## Tecnologias

Python, FastAPI, SQLAlchemy, Pydantic e SQLite para desenvolvimento. O driver PostgreSQL também está incluído; sua integração ainda precisa ser validada.

## Executar localmente

Requisitos: Python 3.11+.

```bash
python -m venv .venv
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source .venv/bin/activate
```

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Documentação interativa: http://127.0.0.1:8000/docs.

| Método | Rota | Finalidade |
| --- | --- | --- |
| GET | /health | Disponibilidade |
| POST | /imports/preview | Analisar texto copiado |
| POST | /imports/confirm | Salvar registros validados |
| GET | /occurrences | Consultar chamados |

O SQLite é usado por padrão. Para experimentar PostgreSQL, defina a variável de ambiente `DATABASE_URL` no formato `postgresql+psycopg://usuario:senha@localhost/banco`. O arquivo `.env.example` é uma referência; o backend lê as variáveis do ambiente do processo.

## Testes

```bash
python -m pytest
```

Os casos iniciais utilizam dados sintéticos. A importação precisa ser validada com amostras autorizadas, incluindo todos os estados e formatos de registros.

## Limitações

A versão inicial importa a listagem geral; ainda não processa as abas detalhadas, notas fiscais ou valores financeiros, não importa o histórico Excel e não realiza conciliação de inventários. O código da ocorrência é tratado como identificador único. O armazenamento inicial usa JSON por ocorrência; será necessário normalizar as tabelas e adicionar migrações para a versão seguinte.

Sem autenticação, autorização ou controle de acesso. Não exponha a API fora da máquina local.

Frontend: https://github.com/pethersonzada/rm-control-frontend

Software proprietário. Consulte [LICENSE](LICENSE).
