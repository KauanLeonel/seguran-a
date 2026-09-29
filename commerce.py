"""Marketplace domain: owner-scoped data, atomic inventory and delivery workflow."""
import html
import re
import secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, parse_qs

from vault import Vault

TEST_CARDS = {'4242424242424242': 'Visa', '5555555555554444': 'Mastercard'}
PROFILE_FIELDS = [('cpf', 'CPF (11 dígitos)', 14), ('phone', 'Telefone com DDD', 20), ('street', 'Rua', 100), ('number', 'Número', 12), ('district', 'Bairro', 60), ('city', 'Cidade', 60), ('state', 'UF', 2), ('zip', 'CEP', 9)]
STATES = {'paid': 'Pagamento aprovado', 'shipped': 'Em transporte', 'delivered': 'Entregue'}
esc = lambda value: html.escape(str(value), quote=True)
money = lambda cents: f'R$ {cents / 100:.2f}'.replace('.', ',')


def field(name, label, value='', kind='text', maximum=120, required=True):
    return f'<label>{label}<input type="{kind}" name="{name}" maxlength="{maximum}" value="{esc(value)}" {"required" if required else ""}></label>'


def profile_fields(values=None, saved_card=False):
    values = values or {}
    fields = ''.join(field(key, label, values.get(key, ''), maximum=maximum) for key, label, maximum in PROFILE_FIELDS)
    fields += field('card', 'Substituir cartão (opcional)' if saved_card else 'Cartão de teste', maximum=19, required=not saved_card)
    fields += field('expiry', 'Validade (MM/AAAA)', values.get('expiry', ''), maximum=7)
    return fields + '<p class="hint">Use apenas dados fictícios. Cartões de teste: 4242 4242 4242 4242 ou 5555 5555 5555 4444. Não há cobrança.</p>'


def validate_profile(get, previous=None):
    from datetime import date
    p = dict(previous or {})
    for key, minimum, maximum in [('street', 2, 100), ('number', 1, 12), ('district', 2, 60), ('city', 2, 60)]:
        value = get(key).strip()
        if not minimum <= len(value) <= maximum or any(ord(c)<32 for c in value):
            raise ValueError('Preencha o endereço completo.')
        p[key] = value
    for key, pattern in [('cpf', r'[0-9]{11}'), ('phone', r'[0-9]{10,11}'), ('zip', r'[0-9]{8}')]:
        value = re.sub(r'[.()\s-]', '', get(key))
        if not re.fullmatch(pattern, value):
            raise ValueError('Confira CPF, telefone e CEP. Todos são obrigatórios.')
        p[key] = value
    p['state'] = get('state').upper()
    if p['state'] not in 'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split():
        raise ValueError('UF inválida.')
    card = re.sub(r'[ -]', '', get('card')) or p.get('card', '')
    if card not in TEST_CARDS:
        raise ValueError('Informe um dos cartões de teste indicados no formulário.')
    p['card'] = card
    expiry = get('expiry')
    if not re.fullmatch(r'(0[1-9]|1[0-2])/[0-9]{4}', expiry) or (int(expiry[3:]), int(expiry[:2])) < (date.today().year, date.today().month):
        raise ValueError('Validade do cartão inválida ou expirada.')
    p['expiry'] = expiry
    return p


class Commerce:
    def __init__(self, connect, key_path, products):
        self.connect = connect
        with connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS vault_meta (id INTEGER PRIMARY KEY CHECK(id=1), proof TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS profiles (user_id INTEGER PRIMARY KEY REFERENCES users(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS listings (
              id INTEGER PRIMARY KEY, seller_id INTEGER REFERENCES users(id), title TEXT NOT NULL,
              description TEXT NOT NULL, cents INTEGER NOT NULL CHECK(cents>0),
              stock INTEGER NOT NULL CHECK(stock>=0), icon TEXT NOT NULL DEFAULT '📦');
            CREATE TABLE IF NOT EXISTS checkouts (
              token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              listing_id INTEGER NOT NULL REFERENCES listings(id), expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS purchases (
              id INTEGER PRIMARY KEY, buyer_id INTEGER NOT NULL REFERENCES users(id),
              seller_id INTEGER REFERENCES users(id), listing_id INTEGER NOT NULL REFERENCES listings(id),
              title TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
              unit_cents INTEGER NOT NULL, shipping_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL,
              delivery TEXT NOT NULL, card_last4 TEXT NOT NULL, status TEXT NOT NULL,
              tracking TEXT NOT NULL, checkout_token TEXT UNIQUE NOT NULL,
              created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            ''')
            proof = db.execute('SELECT proof FROM vault_meta WHERE id=1').fetchone()
            self.vault = Vault(key_path, allow_create=proof is None)
            if proof:
                if self.vault.open(proof['proof'], 'key-check') != 'campus-market':
                    raise RuntimeError('Chave incompatível com este banco.')
            else:
                db.execute('INSERT INTO vault_meta VALUES(1,?)', (self.vault.seal('campus-market', 'key-check'),))
                # One-time migration: existing accounts keep passwords and IDs.
                for user in db.execute('SELECT id,name FROM users').fetchall():
                    db.execute('UPDATE users SET name=? WHERE id=?', (self.vault.seal(user['name'], f'name:{user["id"]}'), user['id']))
            for pid, title, description, cents, icon in products:
                db.execute('INSERT OR IGNORE INTO listings(id,title,description,cents,stock,icon) VALUES(?,?,?,?,?,?)', (pid, title, description, cents, 30, icon))
            if hasattr(db, 'sync_identity'):
                db.sync_identity('listings')
            # Existing accounts without a profile must complete it manually.
            for user in db.execute('SELECT id FROM users WHERE id NOT IN (SELECT user_id FROM profiles)').fetchall():
                self.create_profile(db, user['id'])
            db.execute('CREATE TABLE IF NOT EXISTS commerce_migrations (version INTEGER PRIMARY KEY)')
            if not db.execute('SELECT 1 FROM commerce_migrations WHERE version=2').fetchone():
                for row in db.execute('SELECT user_id,data FROM profiles').fetchall():
                    uid = row['user_id']
                    old_default = {'cpf': '00000000000', 'phone': '11000000000', 'street': 'Rua Exemplo', 'number': str(uid), 'district': 'Bairro Modelo', 'city': 'Cidade Exemplo', 'state': 'SP', 'zip': '00000000', 'card': '4242424242424242', 'expiry': '12/2099'}
                    if self.vault.open(row['data'], f'profile:{uid}') == old_default:
                        db.execute('UPDATE profiles SET data=? WHERE user_id=?', (self.vault.seal(self.empty_profile(), f'profile:{uid}'), uid))
                db.execute('INSERT INTO commerce_migrations VALUES(2)')

    def name(self, encrypted, uid):
        return self.vault.open(encrypted, f'name:{uid}')

    @staticmethod
    def empty_profile():
        return {key: '' for key in [*(f[0] for f in PROFILE_FIELDS), 'card', 'expiry']}

    def create_profile(self, db, uid, data=None):
        data = data if data is not None else self.empty_profile()
        db.execute('INSERT INTO profiles VALUES(?,?)', (uid, self.vault.seal(data, f'profile:{uid}')))

    def profile(self, db, uid):
        row = db.execute('SELECT data FROM profiles WHERE user_id=?', (uid,)).fetchone()
        return self.vault.open(row['data'], f'profile:{uid}')

    def catalog(self):
        with self.connect() as db:
            return db.execute('SELECT * FROM listings WHERE stock>0 ORDER BY id DESC LIMIT 100').fetchall()

    def get(self, h, s):
        parts = urlsplit(h.path)
        if parts.path not in ('/profile', '/sell', '/sales', '/checkout', '/order'):
            return False
        if not s['user_id']:
            h.reply(303, location='/login')
            return True
        uid = s['user_id']
        query = parse_qs(parts.query)
        ident = query.get('id', [''])[0]
        if parts.path in ('/checkout', '/order') and not re.fullmatch(r'[0-9]{1,10}', ident):
            h.reply(404, 'Página não encontrada.')
            return True
        with self.connect() as db:
            if parts.path == '/profile':
                p = self.profile(db, uid)
                user = db.execute('SELECT name FROM users WHERE id=?', (uid,)).fetchone()
                fields = field('name', 'Nome completo', self.name(user['name'], uid), maximum=60)
                if p['card']:
                    fields += '<div class="mock"><b>Cartão •••• ' + esc(p['card'][-4:]) + '</b></div>'
                fields += profile_fields(p, saved_card=bool(p['card']))
                fields += field('password', 'Confirme sua senha para salvar', kind='password', maximum=128)
                content = '<section class="panel"><h1>Dados pessoais</h1><p>Seu contato, endereço de entrega e pagamento.</p>' + h.form('/profile', fields, 'Salvar dados', s) + '</section>'
            elif parts.path == '/sell':
                fields = field('title', 'Nome do produto', maximum=80) + '<label>Descrição<textarea name="description" maxlength="600" required></textarea></label>'
                fields += field('price', 'Preço em reais', '99,90', maximum=12) + field('stock', 'Quantidade em estoque', '1', kind='number', maximum=4)
                content = '<section class="panel"><h1>Anuncie seu produto</h1><p>Seu anúncio ficará disponível no catálogo.</p>' + h.form('/sell', fields, 'Publicar anúncio', s) + '</section>'
            elif parts.path == '/sales':
                listings = db.execute('SELECT * FROM listings WHERE seller_id=? ORDER BY id DESC LIMIT 100', (uid,)).fetchall()
                orders = db.execute('SELECT * FROM purchases WHERE seller_id=? ORDER BY id DESC LIMIT 100', (uid,)).fetchall()
                content = '<section class="panel wide"><h1>Minhas vendas</h1><a class="button" href="/sell">Anunciar produto</a><h2>Meus anúncios</h2><ul>'
                content += ''.join(f'<li>{esc(p["title"])} · {money(p["cents"])} · {p["stock"]} em estoque</li>' for p in listings) or '<li>Nenhum anúncio publicado.</li>'
                content += '</ul><h2>Pedidos recebidos</h2>' + self.order_list(orders) + '</section>'
            elif parts.path == '/checkout':
                p = self.profile(db, uid)
                if not all(p.values()):
                    h.reply(303, location='/profile')
                    return True
                product = db.execute('SELECT * FROM listings WHERE id=? AND stock>0', (ident,)).fetchone()
                if not product or product['seller_id'] == uid:
                    h.reply(400, 'Produto indisponível para compra.')
                    return True
                import time
                db.execute('DELETE FROM checkouts WHERE expires<?', (time.time(),))
                if db.execute('SELECT COUNT(*) FROM checkouts WHERE user_id=?', (uid,)).fetchone()[0] >= 30:
                    h.reply(429, 'Você tem muitos pedidos abertos. Aguarde alguns minutos.')
                    return True
                token = secrets.token_urlsafe(32)
                db.execute('INSERT INTO checkouts VALUES(?,?,?,?)', (token, uid, product['id'], time.time()+900))
                p = self.profile(db, uid)
                fields = f'<input type="hidden" name="checkout" value="{token}">'
                fields += '<label>Quantidade<input type="number" name="quantity" value="1" min="1" max="10" required></label>'
                fields += '<label>Entrega<select name="shipping"><option value="standard">Padrão · R$ 15,00 · 5 a 8 dias úteis</option><option value="express">Expressa · R$ 30,00 · 2 a 3 dias úteis</option></select></label>'
                content = f'<section class="panel"><small>FINALIZAR COMPRA</small><h1>{esc(product["title"])}</h1><p>{money(product["cents"])} por unidade</p><div class="mock"><b>Entregar em</b><p>{self.address(p)}</p><p>Cartão •••• {esc(p["card"][-4:])}</p><a href="/profile">Alterar dados →</a></div><p>O total é o preço unitário × quantidade + frete escolhido. Entrega e pagamento simulados; nenhum valor será cobrado.</p>' + h.form('/buy', fields, 'Confirmar pedido', s) + '</section>'
            else:
                order = db.execute('SELECT * FROM purchases WHERE id=? AND (buyer_id=? OR seller_id=?)', (ident, uid, uid)).fetchone()
                if not order:
                    h.reply(404, 'Pedido não encontrado.')
                    return True
                delivery = self.vault.open(order['delivery'], f'order:{order["checkout_token"]}')
                content = f'<section class="panel"><small>PEDIDO #{order["id"]}</small><h1>{esc(order["title"])}</h1><p>{order["quantity"]} unidade(s) · Total {money(order["total_cents"])}, incluindo frete de {money(order["shipping_cents"])}</p><div class="mock"><b>{STATES[order["status"]]}</b><p>Código interno: {esc(order["tracking"])}</p><p>{esc(delivery["name"])}<br>{self.address(delivery)}<br>Telefone: {esc(delivery["phone"])}</p></div>'
                if order['buyer_id'] == uid:
                    content += f'<p>Cartão •••• {esc(order["card_last4"])}</p>'
                action = 'ship' if order['seller_id'] == uid and order['status'] == 'paid' else 'receive' if order['buyer_id'] == uid and order['status'] == 'shipped' else None
                if action:
                    content += h.form('/delivery', f'<input type="hidden" name="order" value="{order["id"]}"><input type="hidden" name="action" value="{action}">', 'Marcar como enviado' if action == 'ship' else 'Confirmar recebimento', s)
                content += '</section>'
        h.reply(200, h.page('Minha loja', content, s))
        return True

    def address(self, p):
        return esc(f'{p["street"]}, {p["number"]} — {p["district"]}, {p["city"]}/{p["state"]} · CEP {p["zip"]}')

    def order_list(self, orders):
        return '<ul>' + (''.join(f'<li><a href="/order?id={o["id"]}">Pedido #{o["id"]} · {esc(o["title"])} · {STATES[o["status"]]} →</a><p>{money(o["total_cents"])}</p></li>' for o in orders) or '<li>Seus pedidos aparecerão aqui.</li>') + '</ul>'

    def post(self, h, s, get, verify_password):
        if h.path not in ('/profile', '/sell', '/buy', '/delivery'):
            return False
        uid = s['user_id']
        try:
            with self.connect() as db:
                if h.path == '/profile':
                    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
                    if not verify_password(user, get('password')):
                        raise ValueError('Senha incorreta. Seus dados não foram alterados.')
                    p = validate_profile(get, self.profile(db, uid))
                    name = get('name').strip()
                    if not 1 <= len(name) <= 60:
                        raise ValueError('Nome inválido.')
                    db.execute('UPDATE profiles SET data=? WHERE user_id=?', (self.vault.seal(p, f'profile:{uid}'), uid))
                    db.execute('UPDATE users SET name=? WHERE id=?', (self.vault.seal(name, f'name:{uid}'), uid))
                    destination = '/account'
                elif h.path == '/sell':
                    title, description = get('title').strip(), get('description').strip()
                    if not 3 <= len(title) <= 80 or not 3 <= len(description) <= 600:
                        raise ValueError('Informe nome (3–80 caracteres) e descrição (3–600 caracteres).')
                    price = get('price').replace(',', '.')
                    if not re.fullmatch(r'[0-9]{1,6}(\.[0-9]{1,2})?', price) or not re.fullmatch(r'[0-9]{1,3}', get('stock')):
                        raise ValueError('Preço ou quantidade inválidos.')
                    cents, stock = int(Decimal(price)*100), int(get('stock'))
                    if not 100 <= cents <= 10000000 or not 1 <= stock <= 999:
                        raise ValueError('Preço deve ser de R$ 1 a R$ 100.000; estoque de 1 a 999.')
                    db.execute('INSERT INTO listings(seller_id,title,description,cents,stock) VALUES(?,?,?,?,?)', (uid, title, description, cents, stock))
                    destination = '/sales'
                elif h.path == '/buy':
                    import time
                    db.execute('BEGIN IMMEDIATE')
                    previous = db.execute('SELECT id FROM purchases WHERE checkout_token=? AND buyer_id=?', (get('checkout'), uid)).fetchone()
                    if previous:
                        destination = f'/order?id={previous["id"]}'
                    else:
                        checkout = db.execute('SELECT * FROM checkouts WHERE token=? AND user_id=? AND expires>?', (get('checkout'), uid, time.time())).fetchone()
                        if not checkout:
                            raise ValueError('Compra expirada. Escolha o produto novamente no catálogo.')
                        product = db.execute('SELECT * FROM listings WHERE id=?', (checkout['listing_id'],)).fetchone()
                        if product['seller_id'] == uid:
                            raise ValueError('Você não pode comprar seu próprio anúncio.')
                        if not re.fullmatch(r'[0-9]{1,2}', get('quantity')) or not 1 <= int(get('quantity')) <= 10:
                            raise ValueError('Quantidade deve ser de 1 a 10.')
                        quantity = int(get('quantity'))
                        shipping = {'standard': 1500, 'express': 3000}.get(get('shipping'))
                        if shipping is None:
                            raise ValueError('Escolha uma entrega válida.')
                        changed = db.execute('UPDATE listings SET stock=stock-? WHERE id=? AND stock>=?', (quantity, product['id'], quantity)).rowcount
                        if not changed:
                            raise ValueError('Estoque insuficiente. Escolha outra quantidade.')
                        p = self.profile(db, uid)
                        if not all(p.values()):
                            raise ValueError('Preencha seus dados pessoais antes de comprar.')
                        from datetime import date
                        if (int(p['expiry'][3:]), int(p['expiry'][:2])) < (date.today().year, date.today().month):
                            raise ValueError('Cartão expirado. Atualize os dados no perfil.')
                        user = db.execute('SELECT name FROM users WHERE id=?', (uid,)).fetchone()
                        delivery = {k: p[k] for k in ('phone', 'street', 'number', 'district', 'city', 'state', 'zip')}
                        delivery['name'] = self.name(user['name'], uid)
                        total = quantity*product['cents'] + shipping
                        cur = db.execute('INSERT INTO purchases(buyer_id,seller_id,listing_id,title,quantity,unit_cents,shipping_cents,total_cents,delivery,card_last4,status,tracking,checkout_token) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (uid, product['seller_id'], product['id'], product['title'], quantity, product['cents'], shipping, total,
                             self.vault.seal(delivery, f'order:{checkout["token"]}'), p['card'][-4:], 'paid' if product['seller_id'] else 'shipped', 'CM'+secrets.token_hex(6).upper(), checkout['token']))
                        destination = f'/order?id={cur.lastrowid}'
                else:
                    if get('action') == 'ship':
                        changed = db.execute("UPDATE purchases SET status='shipped' WHERE id=? AND seller_id=? AND status='paid'", (get('order'), uid)).rowcount
                    elif get('action') == 'receive':
                        changed = db.execute("UPDATE purchases SET status='delivered' WHERE id=? AND buyer_id=? AND status='shipped'", (get('order'), uid)).rowcount
                    else:
                        changed = 0
                    if not changed:
                        h.reply(409, 'Não foi possível alterar esta entrega. Confira sua permissão e o estado do pedido.')
                        return True
                    destination = '/order?id=' + str(int(get('order')))
            h.reply(303, location=destination)
        except (ValueError, InvalidOperation) as error:
            h.reply(400, str(error))
        return True
