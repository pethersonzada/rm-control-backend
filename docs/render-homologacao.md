# Homologação gratuita: Render + Neon

O Blueprint provisiona **somente** um Web Service Docker Free. Não cria banco no Render, disco, worker ou serviço pago. O banco separado é PostgreSQL Neon Free, `rm_control`, branch `homologacao`, região Ohio. Use apenas dados sintéticos.

1. Confirme o workspace Render e o plano Free antes de aplicar: https://dashboard.render.com/blueprint/new?repo=https://github.com/pethersonzada/rm-control-backend
2. No Neon, selecione a branch de homologação, o banco rm_control e copie a string de conexão direta (pooling desligado). Insira-a exclusivamente no campo secreto DATABASE_URL do Render. Não cole no GitHub ou no chat.
3. Confirme a criação apenas do serviço gratuito. O Render gera RM_HOMOLOGATION_TOKEN automaticamente. Não copie essa credencial para o frontend público nem para o repositório.
4. O comando de início valida a URL, exige TLS com verificação de certificado, deriva a identidade de teste, executa alembic upgrade head e inicia o Uvicorn no PORT atribuído.
5. Quando o serviço estiver Live, confira /health e /ready. /occurrences sem credencial deve retornar 401. Teste /me com Authorization: Bearer seguido do valor de RM_HOMOLOGATION_TOKEN, em cliente local autorizado, sem registrar o cabeçalho. A identidade só tem acesso à organização rm-homologacao e à loja sintética 2.
6. A demo Vercel permanece offline em relação aos dados. Para um frontend operacional separado, defina a origem HTTPS exata em CORS_ORIGINS e configure um build privado de homologação conforme o README do frontend. Nunca vincule dados reais à demo.

O health check do Render usa /health para não manter o banco ativo apenas por sondagens. Consulte /ready manualmente para verificar banco e esquema. O serviço gratuito pode suspender por inatividade; não use keep-alive artificial. A migração usa a conexão direta e a identidade do banco inicial, adequadas apenas à homologação; separe os papéis de migração e runtime antes de operação real.

A aplicação é iniciada somente se as configurações obrigatórias estiverem válidas. O deploy precisa ser confirmado no painel e os testes de ponta a ponta ainda precisam ser realizados.
