# Changelog

## 0.3.0

- Modo operacional bloqueado por padrão e em Vercel; credenciais individuais, perfis, escopo por organização e loja.
- Persistência relacional PostgreSQL/SQLite com Alembic, transações, página por cursor, idempotência e auditoria de importação e status.
- Parser de clipboard com rejeições explícitas. XLSX multipart com limites, escolha de aba, resumo de rejeições e valores originais em Decimal.
- Prazos opt-in com feriados e data de referência. Conferência por loja e PLU com embalagens preservadas.
- Saúde, prontidão, logs por rota, Docker e CI com PostgreSQL.
- Pendências de aceite: autenticação corporativa com gestão de contas, limites distribuídos em produção, catálogo de unidades validado, regras financeiras aprovadas e mapeamento de formatos históricos homologados.

## 0.3.1

- Verificação de identidade Firebase e autorização persistente por UID.
- Administração de vínculos com escopo por organização e loja, desativação e auditoria.
- Migração 0002 e procedimento auditado de primeiro administrador.
- Limite de leitura XLSX simultânea para o Render Free.
