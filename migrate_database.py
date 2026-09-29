"""Offline import from the current SQLite schema into an empty PostgreSQL database."""
import argparse
from pathlib import Path
import sqlite3

import app
from database import postgres_enabled

TABLES = ('users', 'profiles', 'listings', 'orders', 'purchases', 'events', 'vault_meta', 'commerce_migrations')


def migrate(source_path):
    if not postgres_enabled():
        raise RuntimeError('Configure MARKET_DATABASE=postgres no destino.')
    source_path = Path(source_path).resolve(strict=True)
    source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute('BEGIN')  # Consistent read snapshot; never modify the source.
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(TABLES) <= tables:
            raise RuntimeError('Atualize o SQLite com a versão atual da loja antes de importar.')
        from vault import Vault
        vault = Vault(app.KEY_PATH, allow_create=False)
        proof = source.execute('SELECT proof FROM vault_meta WHERE id=1').fetchone()
        if not proof or vault.open(proof['proof'], 'key-check') != 'campus-market':
            raise RuntimeError('A chave não corresponde ao SQLite original.')
        # Validate every encrypted record before touching the destination.
        for row in source.execute('SELECT id,name FROM users'):
            vault.open(row['name'], f'name:{row["id"]}')
        for row in source.execute('SELECT user_id,data FROM profiles'):
            vault.open(row['data'], f'profile:{row["user_id"]}')
        for row in source.execute('SELECT delivery,checkout_token FROM purchases'):
            vault.open(row['delivery'], f'order:{row["checkout_token"]}')
        app.init()
        with app.connect() as target:
            target.connection.execute('LOCK TABLE users,profiles,listings,orders,purchases,events,sessions,checkouts IN ACCESS EXCLUSIVE MODE')
            for table in ('users', 'profiles', 'orders', 'purchases', 'events', 'sessions', 'checkouts'):
                if target.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
                    raise RuntimeError('Destino já contém dados. Importação cancelada sem sobrescrever registros.')
            listings = target.execute('SELECT id,seller_id,title,description,cents,stock,icon FROM listings ORDER BY id').fetchall()
            expected = [(p[0], None, p[1], p[2], p[3], 30, p[4]) for p in app.PRODUCTS]
            if [tuple(r.values()) for r in listings] != expected:
                raise RuntimeError('O catálogo do destino já foi alterado. Importação cancelada.')
            for table in reversed(TABLES):
                target.execute(f'DELETE FROM {table}')
            counts = {}
            for table in TABLES:
                columns = [r[1] for r in source.execute(f'PRAGMA table_info({table})')]
                # Column names are restricted to those known by the target schema.
                allowed = {r['column_name'] for r in target.connection.execute(
                    'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s',
                    (table,)).fetchall()}
                if not set(columns) <= allowed:
                    raise RuntimeError('Esquema de origem incompatível com o destino.')
                names = ','.join('"' + c + '"' for c in columns)
                placeholders = ','.join('?' for _ in columns)
                counts[table] = 0
                for row in source.execute(f'SELECT * FROM {table}'):
                    target.execute(f'INSERT INTO {table}({names}) VALUES({placeholders})', tuple(row))
                    counts[table] += 1
                if target.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] != counts[table]:
                    raise RuntimeError('Contagem divergente; transação revertida.')
            for table in ('users', 'orders', 'events', 'listings', 'purchases'):
                target.sync_identity(table)
        return counts
    finally:
        source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', required=True)
    args = parser.parse_args()
    try:
        counts = migrate(args.sqlite)
    except Exception as error:
        # Database errors may contain personal values: print only the exception type.
        parser.exit(1, f'Importação não concluída ({type(error).__name__}). Verifique banco vazio, versão e chave original.\n')
    for table, count in counts.items():
        print(f'{table}: {count} registros importados')
    print('Importação concluída. Sessões e checkouts temporários não foram transferidos.')
