#!/bin/sh
set -u

if [ -n "${PROJECT_DIR:-}" ]; then
    cd "$PROJECT_DIR" || exit 1
else
    SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
    cd "$SCRIPT_DIR/.." || exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    printf 'Docker não foi encontrado. Execute este script no servidor Kali Linux.\n' >&2
    exit 1
fi

if ! sudo -v; then
    exit 1
fi

trap 'printf "\nMonitor encerrado.\n"; exit 0' INT TERM

while :; do
    printf '\033[2J\033[H'
    printf 'Usuários, produtos e transações da loja\n'
    date '+Atualizado em %H:%M:%S'
    if ! sudo docker compose exec -T db psql -U postgres -d campus \
        -c 'SELECT (SELECT COUNT(*) FROM users) AS total_usuarios,
                   (SELECT COUNT(*) FROM listings) AS total_produtos,
                   (SELECT COUNT(*) FROM purchases) AS total_transacoes;'; then
        printf 'Não foi possível consultar o banco; tentando novamente em 10 segundos.\n'
    fi
    printf '\nPainel web: /simulation (entre com sua conta).\n'
    printf 'Atualização a cada 10 segundos. Ctrl+C encerra.\n'
    sleep 10
done
