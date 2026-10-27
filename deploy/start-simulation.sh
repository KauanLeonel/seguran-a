#!/bin/sh
# Run on the Kali VM, from any directory, with sudo sh deploy/start-simulation.sh.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/.."

if ! command -v docker >/dev/null 2>&1; then
    printf 'Docker não encontrado. Execute este script na VM Kali.\n' >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    printf 'Execute com sudo sh deploy/start-simulation.sh.\n' >&2
    exit 1
fi

printf 'Atualizando a aplicação e iniciando o simulador...\n'
if ! docker compose --profile simulation up -d --build --wait; then
    docker compose --profile simulation ps -a || true
    docker compose logs --tail=30 app simulator || true
    exit 1
fi

printf '\nConfirmando um lote no mesmo banco usado pelo site...\n'
if ! docker compose exec -T app python simulate_market.py --once; then
    docker compose logs --tail=30 simulator || true
    exit 1
fi

docker compose exec -T app python - <<'PY'
import os
import app

with app.connect() as db:
    counts = [db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
              for table in ('simulation_users', 'simulation_listings', 'simulation_purchases')]
print('Registros que aparecem no painel: usuários={}, produtos={}, transações={}'.format(*counts))
if min(counts) < 5:
    raise SystemExit('O lote não foi confirmado no banco do site. Confira os logs do simulador.')
print('Entre na loja e abra: ' + os.environ['MARKET_ORIGIN'].rstrip('/') + '/simulation')
PY

printf '\nO simulador continuará em segundo plano, com lotes a cada 10 segundos.\n'
printf 'Logs: sudo docker compose logs -f --tail=30 simulator\n'
