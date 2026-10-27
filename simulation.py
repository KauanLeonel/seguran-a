"""Atomic synthetic market batches and a read-only view of their records."""
from datetime import datetime, timedelta, timezone
import html
import secrets
import time

from generate_users import synthetic_user
from commerce import validate_profile

PAGE_SIZE = 25
esc = lambda value: html.escape(str(value), quote=True)


def init_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS simulation_batches (
      batch_id TEXT PRIMARY KEY, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS simulation_users (
      user_id INTEGER PRIMARY KEY REFERENCES users(id),
      batch_id TEXT NOT NULL REFERENCES simulation_batches(batch_id));
    CREATE TABLE IF NOT EXISTS simulation_listings (
      listing_id INTEGER PRIMARY KEY REFERENCES listings(id),
      batch_id TEXT NOT NULL REFERENCES simulation_batches(batch_id));
    CREATE TABLE IF NOT EXISTS simulation_purchases (
      purchase_id INTEGER PRIMARY KEY REFERENCES purchases(id),
      batch_id TEXT NOT NULL REFERENCES simulation_batches(batch_id));
    ''')


def create_batch(batch_id):
    """Commit exactly 5 users, 5 listings and 5 purchases, or nothing.

    A persistent batch ID also prevents duplication after restart or when two
    workers try to generate the same time slot.
    """
    import app
    # Expensive password hashes happen outside the inventory lock.
    accounts = []
    for _ in range(5):
        user = synthetic_user()
        salt = secrets.token_bytes(16)
        accounts.append((user, salt, app.password_hash(user['password'], salt)))
    created = datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec='seconds')
    with app.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM simulation_batches WHERE batch_id=?', (batch_id,)).fetchone():
            return None
        db.execute('INSERT INTO simulation_batches VALUES(?,?)', (batch_id, created))
        users, listings, purchases = [], [], []
        for user, salt, password in accounts:
            profile = validate_profile(lambda key: user.get(key, ''))
            uid = db.execute('INSERT INTO users(name,email,salt,password) VALUES(?,?,?,?)',
                             ('', user['email'], salt, password)).lastrowid
            db.execute('UPDATE users SET name=? WHERE id=?',
                       (app.MARKET.vault.seal(user['name'], f'name:{uid}'), uid))
            app.MARKET.create_profile(db, uid, profile)
            db.execute('INSERT INTO simulation_users VALUES(?,?)', (uid, batch_id))
            users.append(uid)
        titles = ('Fone de teste', 'Teclado de teste', 'Mochila de teste', 'Mouse de teste', 'Caderno de teste')
        for seller, title in zip(users, titles):
            title = f'{title} · {secrets.token_hex(3)}'
            listing_id = db.execute('INSERT INTO listings(seller_id,title,description,cents,stock) VALUES(?,?,?,?,?)',
                                    (seller, title, 'Produto fictício gerado para a simulação da aula.',
                                     1000 + secrets.randbelow(14000), 5)).lastrowid
            db.execute('INSERT INTO simulation_listings VALUES(?,?)', (listing_id, batch_id))
            listings.append(listing_id)
        # Each account buys the previous account's listing: never its own.
        for index, listing_id in enumerate(listings):
            buyer = users[(index + 1) % 5]
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO checkouts VALUES(?,?,?,?)', (token, buyer, listing_id, time.time() + 900))
            order_id = app.MARKET.create_purchase(db, buyer, token, '1', 'standard')
            db.execute('INSERT INTO simulation_purchases VALUES(?,?)', (order_id, batch_id))
            db.execute('DELETE FROM checkouts WHERE token=?', (token,))
            purchases.append(order_id)
    return {'batch_id': batch_id, 'created': created, 'users': users, 'listings': listings, 'purchases': purchases}


def table(title, columns, rows, query):
    head = ''.join(f'<th scope="col">{esc(column)}</th>' for column in columns)
    body = ''.join('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in row) + '</tr>' for row in rows)
    if not body:
        body = f'<tr><td colspan="{len(columns)}" class="empty-row">Nenhum registro nesta página.</td></tr>'
    return (f'<section class="sql-panel"><h2>{esc(title)}</h2><pre class="sql-query"><code>{esc(query)}</code></pre>'
            f'<div class="sql-scroll" tabindex="0" role="region" aria-label="{esc(title)}">'
            f'<table class="sql-table"><caption>{esc(title)} — registros da simulação</caption>'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>')


def render(connect, market, page):
    offset = (page - 1) * PAGE_SIZE
    with connect() as db:
        # A consistent snapshot across counts and rows, including on PostgreSQL.
        if hasattr(db, 'connection'):
            db.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        else:
            db.execute('BEGIN')
        counts = {name: db.execute(f'SELECT COUNT(*) FROM simulation_{name}').fetchone()[0]
                  for name in ('users', 'listings', 'purchases', 'batches')}
        last = db.execute('SELECT created FROM simulation_batches ORDER BY created DESC LIMIT 1').fetchone()
        users = db.execute('SELECT u.id,u.name,u.email,s.batch_id,b.created FROM users u '
                           'JOIN simulation_users s ON s.user_id=u.id JOIN simulation_batches b ON b.batch_id=s.batch_id '
                           'ORDER BY u.id DESC LIMIT ? OFFSET ?', (PAGE_SIZE, offset)).fetchall()
        listings = db.execute('SELECT l.id,l.seller_id,l.title,l.cents,l.stock,s.batch_id FROM listings l '
                              'JOIN simulation_listings s ON s.listing_id=l.id '
                              'ORDER BY l.id DESC LIMIT ? OFFSET ?', (PAGE_SIZE, offset)).fetchall()
        purchases = db.execute('SELECT p.id,p.buyer_id,p.seller_id,p.listing_id,p.quantity,p.unit_cents,'
                               'p.shipping_cents,p.total_cents,p.status,p.created,s.batch_id FROM purchases p '
                               'JOIN simulation_purchases s ON s.purchase_id=p.id '
                               'ORDER BY p.id DESC LIMIT ? OFFSET ?', (PAGE_SIZE, offset)).fetchall()
    content = ('<section class="simulation-heading"><small>LABORATÓRIO / DADOS FICTÍCIOS</small>'
               '<h1>Simulação do marketplace</h1><p>5 usuários, 5 produtos e 5 transações por lote. '
               'Intervalo padrão: 10 segundos. Pagamentos simulados.</p>'
               f'<p>Último lote: <strong>{esc(last["created"]) if last else "Nenhum lote gerado"}</strong> · '
               f'Atualização automática a cada 10 segundos. <a href="/simulation?page={page}">Atualizar agora</a></p></section>')
    content += '<div class="simulation-stats">' + ''.join(
        f'<div><span>{label}</span><strong>{counts[key]}</strong></div>'
        for key, label in (('users', 'Usuários'), ('listings', 'Produtos'), ('purchases', 'Transações'), ('batches', 'Lotes'))
    ) + '</div>'
    if not counts['batches']:
        content += ('<div class="mock" role="status"><b>Aguardando o primeiro lote</b>'
                    '<p>Os registros aparecem quando o simulador estiver em execução. '
                    'Esta página mostra os dados criados pelo simulador completo; '
                    'cadastros manuais e do gerador de usuários não entram nestas tabelas.</p></div>')
    content += table('users · Usuários', ('id', 'name', 'email', 'batch_id', 'created'),
                     [(u['id'], market.name(u['name'], u['id']), u['email'], u['batch_id'], u['created']) for u in users],
                     'SELECT id, name, email FROM users /* somente registros da simulação; nome decifrado pela aplicação */;')
    content += table('listings · Produtos', ('id', 'seller_id', 'title', 'cents', 'stock', 'batch_id'),
                     [tuple(l[key] for key in ('id', 'seller_id', 'title', 'cents', 'stock', 'batch_id')) for l in listings],
                     'SELECT id, seller_id, title, cents, stock FROM listings /* somente registros da simulação */;')
    content += table('purchases · Transações', ('id', 'buyer_id', 'seller_id', 'listing_id', 'quantity',
                                               'unit_cents', 'shipping_cents', 'total_cents', 'status', 'created', 'batch_id'),
                     [tuple(p[key] for key in ('id', 'buyer_id', 'seller_id', 'listing_id', 'quantity',
                                               'unit_cents', 'shipping_cents', 'total_cents', 'status', 'created', 'batch_id')) for p in purchases],
                     'SELECT id, buyer_id, seller_id, listing_id, quantity, total_cents, status FROM purchases /* simulação */;')
    pages = max(1, (max(counts['users'], counts['listings'], counts['purchases']) + PAGE_SIZE - 1) // PAGE_SIZE)
    content += '<nav class="sql-pagination" aria-label="Páginas dos registros">'
    if page > 1:
        content += f'<a class="pill" href="/simulation?page={min(page - 1, pages)}">← Anterior</a>'
    content += f'<span>Página {page} · {PAGE_SIZE} registros por tabela</span>'
    if page < pages:
        content += f'<a class="pill" href="/simulation?page={page + 1}">Próxima →</a>'
    content += ('</nav><p class="hint">Valores monetários em centavos; created das transações está em UTC. '
                'A página consulta apenas os registros identificados como simulação. Senhas, cartões, CPF e endereços não são exibidos.</p>')
    return content
