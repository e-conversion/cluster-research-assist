from alembic import context

from cra.app.history.tables import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations through `cra db`, not the alembic command")

# render_as_batch: SQLite cannot ALTER columns in place, batch mode recreates the table
context.configure(
    connection=connection, target_metadata=Base.metadata, render_as_batch=True
)
with context.begin_transaction():
    context.run_migrations()
