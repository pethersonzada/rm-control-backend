import os

from alembic import context
from sqlalchemy import create_engine, pool

from app.models import Base

url = os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError("Defina DATABASE_URL para executar as migrações.")


def include_object(obj, name, type_, reflected, compare_to):
    # A tabela do protótipo é preservada para conferência ou migração assistida.
    return not (type_ == "table" and name == "occurrences")


options = {"target_metadata": Base.metadata, "include_object": include_object}

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, **options)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, **options)
        with context.begin_transaction():
            context.run_migrations()
