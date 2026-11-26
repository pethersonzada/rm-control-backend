# Firebase na homologação

A autenticação usa o Firebase Spark e os dados operacionais ficam no PostgreSQL do Neon. O FastAPI verifica assinatura RS256, emissor, audiência, validade, UID e horários com certificados públicos do Google. O escopo vem do banco em cada requisição. A demo Vercel permanece desativada para operações.

## Primeiro administrador

1. Adicione o domínio estável da homologação aos domínios autorizados em Firebase Authentication.
2. Entre pelo Google ou por uma conta de e-mail provisionada no Firebase. A tela de acesso pendente mostra o UID sem mostrar tokens. Confira o mesmo UID no Firebase Console e confirme que pertence à conta do proprietário.
3. No serviço de homologação Render, defina `RM_BOOTSTRAP_ADMIN_UID` com esse UID em Environment. Faça o deploy. O ambiente deve ter `RM_FIREBASE_PROJECT_ID=rm-control-webapp` e `RM_ALLOW_LEGACY_TOKEN=0`.
4. Após validar o novo acesso, remova `RM_BOOTSTRAP_ADMIN_UID`. O registro continua ativo no PostgreSQL.

O bootstrap roda depois do Alembic. Ele é idempotente, protegido contra concorrência e opera uma vez para a organização `rm-homologacao`, loja sintética `2`. A alteração gera auditoria em `rm_membership_audit`. O primeiro administrador pode delegar perfil administrativo e é protegido contra alteração pela API. Outros administradores gerenciam operadores e visualizadores nas lojas que já administram. A API exige um UID previamente confirmado pelo responsável ao criar vínculos. Login por si só não concede acesso.

Desativar um vínculo bloqueia imediatamente novas chamadas, mesmo com ID token ainda válido. A revogação feita somente no console Firebase segue a vida útil do ID token já emitido; para bloqueio imediato, desative também o vínculo no RM Control. A API usa chaves públicas e dispensa chave privada de serviço. Aplique a migração `0002` apenas por Alembic.

## Rotas e limites

- `/health` verifica o processo e `/ready` confere o banco e as tabelas de autenticação quando o Firebase está ativo.
- `/me` retorna perfil, organização e lojas.
- `GET /admin/users` lista vínculos administráveis por escopo. `PUT /admin/users` grava vínculo e auditoria na mesma transação. A rota aceita UID, perfil, lojas e status ativo.
- O Render Free executa um processo. Há uma leitura XLSX simultânea por processo desde o início do upload e duas operações de importação transacionais simultâneas. Use upload multipart e reenvie a confirmação com a mesma chave de idempotência quando necessário.
- O primeiro acesso após ociosidade pode levar cerca de um minuto. A demo pública responde a partir do próprio frontend.
