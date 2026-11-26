# RM Control | Backend

API REST para importação e acompanhamento de ocorrências logísticas.

> Versão 0.2.0 — protótipo em desenvolvimento, para execução local.

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

## Novidades da versão 0.2

- Detalhes de produtos e notas fiscais (número e série; chaves fiscais e dados de motoristas não são persistidos).
- POST /imports/spreadsheet: prévia de histórico XLSX por cabeçalhos; conflitos bloqueiam importação por padrão.
- GET /summary: valores informados, sem equivalência automática a ressarcimentos aprovados.
- POST /inventory/reconcile: cruza divergências por loja e PLU com chamados pendentes; não aplica ajustes.
- Token opcional: configure RM_ACCESS_TOKEN no ambiente e envie o cabeçalho X-RM-Token. Nunca coloque o token em código público.
- GitHub Actions para testes e validação de sintaxe.

## Limitações

A identificação de chamados usa o código da ocorrência. Registros de fontes diferentes com itens no mesmo chamado exigem revisão manual; não são mesclados automaticamente. A importação histórica usa valores salvos no XLSX e não recalcula fórmulas. A conciliação é preliminar: não soma embalagens incompatíveis nem calcula ajustes financeiros. Prazos, autenticação individual e permissões por perfil ainda precisam ser implementados. O armazenamento inicial usa JSON por ocorrência; normalização e migrações permanecem pendentes. Não exponha a API fora da máquina local.

Frontend: https://github.com/pethersonzada/rm-control-frontend

Software proprietário. Consulte [LICENSE](LICENSE).
