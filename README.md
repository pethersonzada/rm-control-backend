# RM Control | Backend

API proprietária de importação e gestão de ocorrências logísticas.

## Ambientes e saúde

`GET /health` retorna 200 quando o servidor está ativo. `GET /ready` retorna 200 após habilitar a operação e aplicar as migrações. Na Vercel esta aplicação é desativada para operação e só a saúde está disponível para a demo. O projeto público não compartilha persistência com o operacional. A API responde 503 quando a configuração ou o banco não está disponível.

## Desenvolvimento local

Use Python 3.12 e defina `RM_MODE=local`, `DATABASE_URL=sqlite:///./rm-control.db` e `RM_IDENTITIES_JSON` no ambiente do processo. O arquivo `.env.example` serve de referência.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Para Docker local, configure também `RM_DB_PASSWORD` e uma `DATABASE_URL` PostgreSQL que use o host `db`, depois execute `docker compose up --build`. O Compose aplica as migrações e publica a API somente em loopback. Antes de ensaiar uma atualização, use uma cópia autorizada com backup.

## Credenciais e escopo

Cada pessoa recebe uma credencial aleatória individual. Gere a credencial fora do repositório com `secrets.token_urlsafe(32)`. Em `RM_IDENTITIES_JSON`, cada chave é o SHA-256 em hexadecimal da credencial. Cada valor tem os campos `subject`, `organization`, `role` e `stores`. Liste explicitamente as lojas permitidas, com códigos normalizados. Envie a credencial no cabeçalho `Authorization: Bearer …`. Rotação e revogação são feitas substituindo ou removendo a entrada do ambiente e reiniciando a aplicação. O perfil `viewer` consulta e confere inventário. Os perfis `importer` e `admin` também importam e atualizam status.

Em operação, use `RM_MODE=operational`, PostgreSQL com a URL `postgresql+psycopg://…`, HTTPS e origens HTTPS explícitas em `CORS_ORIGINS`. A operação é destinada a um host privado. Mantenha os segredos no ambiente e separe permissões de migração e runtime. O limitador local atua por instância. Em múltiplas réplicas, complemente com limite compartilhado e alertas no proxy de entrada. Configure logs sem cabeçalhos sensíveis, corpo ou parâmetros de consulta.

## Contrato de operação

- `GET /me` informa o escopo associado à credencial.
- `GET /occurrences` pagina por `cursor` e `limit`. Filtra por `search`, `status`, `store`, `reason`, `date_field`, `from_date`, `to_date` `include_tests` e `order`.
- `POST /imports/preview` analisa clipboard e pode receber `target_code` para resolver ambiguidades.
- `POST /imports/workbook` aceita multipart com `file`, `sheet` e `skip_invalid`. Planilhas de até 21 MB, 180 MB descompactados e 100 mil linhas. O nome da aba é exigido se várias forem compatíveis. O parser usa valores gravados na planilha, informa rejeições e mantém ausências monetárias como nulas.
- `POST /imports/confirm` confirma uma lista de ocorrências em transação. Use `Idempotency-Key` por tentativa de confirmação e mantenha a chave nos reenvios. Até 10 mil ocorrências e 12 MB JSON. A chave pertence à organização e ao usuário. Atualizações de listagem e detalhe são parciais, mantendo itens anteriores até existir um contrato validado para snapshots completos.
- `PATCH /occurrences/{code}/status?store=…` recebe `status` e `expected_status`. A auditoria registra identidade, loja e estados, na mesma transação.
- `GET /summary` apresenta valores informados. Aprovado, recuperado e pendente permanecem indisponíveis até o processo financeiro ter fonte e regras validadas. Se houver valores com sinais a confirmar, o saldo informado fica indisponível.
- `POST /inventory/reconcile` cruza loja e PLU e devolve a quantidade na embalagem original, sem conversão ou geração de ajustes.

Configure `RM_CALENDARS_JSON` por organização com `days`, `start_field` (`included_at` ou `received_at`), `timezone` e `holidays` (datas ISO). O prazo começa a contar no primeiro dia útil após a origem se `days` for maior que zero. A API reporta a data calculada e se ela passou. O estado do chamado e lançamentos financeiros seguem o fluxo aprovado pela operação.

A base antiga `occurrences` é preservada. Para ensaiar sua cópia, aplique a migração no destino, defina `LEGACY_DATABASE_URL` e execute `python -m app.migrate_legacy --organization ID --stores LOJA` em cópia autorizada. Confira as contagens e a atribuição de lojas antes de executar novamente com `--apply`. O comando limita o ensaio a 10 mil registros. Dados de múltiplas organizações exigem mapeamento humano.

## Testes

```bash
python -m pytest -q -s
python -m compileall -q app migrations
```

A suíte usa dados sintéticos e executa SQLite. Defina `RM_TEST_POSTGRES_URL` para incluir PostgreSQL. Use um banco descartável e exclusivo com nome terminado em `_test`. A suíte reverte e reaplica as migrações entre testes. A massa XLSX de 14 a 20 MB é gerada no teste e descartada. Confira o [CHANGELOG](CHANGELOG.md).

Software proprietário. Consulte [LICENSE](LICENSE).

A homologação autenticada usa Firebase, Render e Neon. Consulte [Firebase na homologação](docs/firebase-homologacao.md) para provisionamento, permissões e validação.
