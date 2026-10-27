"""Gera 5 usuários, 5 produtos e 5 compras simuladas a cada 10 segundos."""
import argparse
import time

from simulation import create_batch


def run(interval=10, once=False, create=create_batch, sleep=time.sleep):
    if interval < 1:
        raise ValueError('O intervalo deve ser positivo.')
    completed = 0
    while True:
        started = time.monotonic()
        # Shared time slots prevent duplicate lots after restart or double start.
        batch_id = f'sim-{interval}-{int(time.time() // interval)}'
        try:
            result = create(batch_id)
        except Exception as error:
            if once:
                raise
            # Database errors can contain values: log the type, never credentials.
            print(f'Lote revertido ({type(error).__name__}); nova tentativa no próximo intervalo.', flush=True)
        else:
            if result:
                completed += 1
                print(f'{result["created"]} | {batch_id} | 5 usuários, 5 produtos, 5 transações | '
                      f'Lotes criados nesta execução: {completed}', flush=True)
            else:
                print(f'{batch_id} já existe; nenhum registro duplicado.', flush=True)
        if once:
            return completed
        sleep(max(0, interval - (time.monotonic() - started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=int, default=10, help='Intervalo em segundos (padrão: 10)')
    parser.add_argument('--once', action='store_true', help='Executar apenas o lote do intervalo atual')
    args = parser.parse_args()
    if args.interval < 1:
        parser.error('O intervalo deve ser positivo.')
    try:
        import app
        app.init()
        print(f'Simulador iniciado: primeiro lote agora; próximos a cada {args.interval}s. Ctrl+C encerra.', flush=True)
        run(args.interval, args.once)
    except KeyboardInterrupt:
        print('\nSimulador encerrado.', flush=True)
    except Exception as error:
        parser.exit(1, f'Simulação não concluída ({type(error).__name__}). Confira conexão, dependências e chave do banco.\n')


if __name__ == '__main__':
    main()
