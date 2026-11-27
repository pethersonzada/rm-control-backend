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


## Gestão de lojas e chamados

- A conta Firebase permanece bloqueada até vínculo ativo. `POST /access/request` aceita somente um ID token válido e registra solicitação idempotente; não concede permissão. O responsável da organização lista pendências em `GET /admin/access-requests`, rejeita em `POST /admin/access-requests/{uid}/reject` ou autoriza em `PUT /admin/users`. A fila automática é limitada a um projeto Firebase associado a uma organização proprietária; múltiplas organizações exigem seleção/invitação explícita futuramente.
- `GET /stores` lista lojas acessíveis. `PUT /admin/stores` cadastra/edita/desativa por código numérico; apenas o administrador inicial com autoridade de organização pode gerenciar lojas. A desativação bloqueia novas operações e conserva histórico. Códigos antigos são migrados com nome provisório.
- `POST /tickets` abre chamado nativo em estado PENDENTE com `Idempotency-Key` obrigatória, loja, data de recebimento ISO, motivo, produtos e notas. O identificador RM é interno, independente do número oficial externo opcional. `PUT /tickets/{code}` edita campos do formulário com revisão esperada, mantendo status e datas de ciclo de vida.
- `GET /occurrences/{code}/history?store=…` fornece histórico paginado por cursor, com identidade auditada, mudança de status e campos alterados em edições nativas. Eventos antigos podem não conter detalhes.
- `GET /reports/chamados.xlsx` exporta os filtros da listagem (até 20 mil linhas por arquivo). IDs, PLUs e notas são textos para preservar zeros; valores superiores à precisão do Excel são textos exatos. Datas conhecidas são formatadas, valores ausentes ficam em branco e textos não são executados como fórmulas. O relatório é um snapshot, não sincronização automática de um arquivo externo. O layout é provisório até comparação com modelo autorizado.
- A abertura é interna ao RM Control; não envia chamados oficiais ao D-Supply.

## Prévia e mapeamento

A prévia de importação compara registros com o banco sem gravar ocorrências. `comparison` contém contagens `new`, `updated`, `unchanged` e `conflicts`; `classifications` identifica cada chamado por código e loja. Conflitos impedem confirmação. A comparação é informativa; a confirmação revalida autorização, chave de idempotência, duplicidades e conflitos em transação. Não substitui o controle de revisão do formulário nativo. Importação permanece parcial e não remove itens existentes.

O clipboard aceita o formato sintético D-Supply documentado e TSV com cabeçalhos explícitos da consolidação. Para colar somente o modal Editar de um chamado já importado, informe `target_code` e, se houver mais de uma loja com o código, `target_store`. A carga, loja, origem e motivo devem corresponder. Não cole credenciais, observações pessoais ou telas não autorizadas.

Para XLSX com cabeçalhos diferentes, informe a aba e o campo multipart opcional `mapping` (JSON). Exemplo sintético: `{"LOJA":"Unidade","PLU":"Código produto","N° CHAMADO":"Ticket"}`. Chaves representam campos canônicos; valores representam títulos exatos da planilha. O mapeamento deve ser unívoco. Não há adivinhação de posições. A análise é limitada e síncrona, com cancelamento do cliente; o servidor pode continuar até o limite de processamento. Não há worker persistente contratado.


## Conferências, configuração e controle financeiro

- `GET/POST /supplier-checks`: conferências concluídas por loja, fornecedor, conferente e data/hora com offset. Código do fornecedor e referência da entrega são opcionais. A mesma empresa pode ter várias conferências. A criação exige Idempotency-Key; a correção/cancelamento usa PUT por ID, revisão e motivo obrigatório, sem exclusão física. Quem registrou ou administrador autorizado pode corrigir. GET /supplier-checks/{id}/history expõe versões anteriores; GET /reports/fornecedores.xlsx exporta o filtro e totaliza por conferente e loja. O formato é inicial, sujeito à conferência da planilha original.
- `GET/PUT /admin/policy`: calendário por organização, desativado até configuração explícita com referência. Dias úteis, início, fuso e feriados, revisão otimista e auditoria. Regra persistida prevalece sobre configuração de ambiente. Não altera status automaticamente.
- `GET/PUT /occurrences/{code}/finance?store=...`: valores aprovados, recuperados e pendentes registrados pelo responsável mediante referência conferida, com revisão e auditoria. Nenhum valor ausente é inferido; resumo soma apenas valores preenchidos e informa a cobertura.
- `GET/PUT /admin/units`: referência de conversão por PLU e embalagem, exige catálogo ativo, fator positivo e fonte conferida. A conciliação aceita embalagem opcional e exibe quantidades normalizadas somente quando há referência ativa, mantendo as originais e sem ajuste contábil.
- `GET /admin/diagnostics`: contadores e tempo médio por processo desde a inicialização, sem dados de requisição. Render Free pode suspender/reiniciar o processo. Não constitui monitoramento persistente nem alerta de SLA.

Ambiente atual exclusivamente sintético. Antes de dados reais, obtenha autorização da empresa, provisione banco e serviço separados e credenciais próprias; configure CORS e CSP apenas para o domínio respectivo, migre banco vazio e teste isolamento. Não reutilize a base compartilhada de homologação nem copie dados de produção para testes. Não configure valores, feriados, conversões ou integrações oficiais sem referência operacional validada.
