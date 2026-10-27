# Kali Linux — rede local da aula

Configuração preparada para **https://10.81.211.237**. Não precisa de domínio. A VM precisa manter esse IP; confirme reserva DHCP ou configuração estática com o responsável pela rede.

```mermaid
flowchart LR
  B[Navegador da turma] -->|HTTPS porta 443| N[Nginx]
  N -->|rede interna| A[Gunicorn + aplicação]
  A -->|rede interna| P[(PostgreSQL)]
  P --- V[Volume persistente]
  A --- K[Chave de criptografia separada]
```

Apenas a porta 443 do IP informado é publicada. PostgreSQL e Gunicorn não publicam portas na VM. Os campos pessoais continuam criptografados; a mudança de banco não transforma as compras em pagamentos reais.

## 1. Preparar a VM

Planeje 2 vCPUs, 2 GB de RAM e espaço para imagens e backups. Este é um ponto de partida para a aula, não um dimensionamento de carga.

Use uma instalação do Kali na VM com disco persistente. Confirme o IP da interface conectada à rede da aula:

```bash
ip -br addr
```

Os comandos abaixo usam o repositório Docker para Debian **trixie**, conforme a [documentação oficial do Kali](https://www.kali.org/docs/containers/installing-docker-on-kali/). O plugin Compose é instalado junto com o Engine, seguindo a [documentação do Docker para Debian](https://docs.docker.com/engine/install/debian/). Se o Docker já estiver instalado e `sudo docker compose version` funcionar, pule a instalação.

Prepare as ferramentas e configure a chave do repositório:

```bash
sudo apt update
sudo apt install -y python3 openssl curl ca-certificates
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

Adicione o repositório. Use `trixie` explicitamente; não substitua por `kali-rolling`:

```bash
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: trixie
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
sudo docker run --rm hello-world
```

Se houver conflito com uma instalação anterior de `docker.io`, `docker-compose` ou `podman-docker`, siga a seção de remoção de pacotes conflitantes no guia do Docker antes de instalar. Não remova volumes ou diretórios de dados para resolver um conflito de pacotes.

Os comandos Docker abaixo usam `sudo`; não é necessário adicionar seu usuário ao grupo docker.

## 2. Copiar o projeto

Copie o código do projeto para uma pasta na VM, por exemplo `~/campus-market`, usando SCP/SFTP. Inclua os arquivos ocultos de configuração necessários, como `.dockerignore`. Não copie `.venv`, `__pycache__`, `.pg-test` ou pastas `test-data-*`.

Para **manter os usuários existentes**, pare o servidor Windows e o gerador antes de copiar:

- `data/market.db` — banco SQLite atual, já atualizado pela versão atual do código.
- `.secrets/market.key` — **a mesma chave usada para criptografar esse banco**.

Copie por SSH/SFTP, não envie a chave para a turma. Preserve um backup desses dois arquivos no computador original. Se MARKET_KEY_FILE apontava para outro local, copie a chave desse local para `.secrets/market.key` na VM. Se começar sem dados anteriores, não é preciso copiar banco nem chave.

## 3. Gerar configuração e certificado

Dentro da pasta do projeto, na VM:

```bash
sudo python3 deploy/prepare.py --ip 10.81.211.237
sudo docker compose config --quiet
sudo docker compose build
sudo docker compose up -d --wait db
```

O preparador cria `.env`, senhas aleatórias do banco, a chave de criptografia se não existir e um certificado TLS para o IP, válido por 30 dias. Preserva arquivos existentes e não substitui a chave de um banco importado. O arquivo `.env` deve conter:

```dotenv
MARKET_ORIGIN=https://10.81.211.237
BIND_IP=10.81.211.237
```

O diretório `.secrets` fica acessível somente ao root no host. Os arquivos montados nos containers têm permissões adequadas para cada serviço; a aplicação executa como UID 10001. As senhas não ficam no Dockerfile nem no Git. Os secrets do Compose são arquivos locais montados, não um cofre externo.

## 4. Importar o SQLite (se houver dados anteriores)

Execute **antes de iniciar a aplicação**:

```bash
sudo docker compose run --rm --no-deps --user 0 \
  -v "$PWD/data:/import:ro" app \
  python migrate_database.py --sqlite /import/market.db
```

O processo lê a origem sem alterá-la, verifica a chave e os registros criptografados, importa contas/perfis/anúncios/pedidos/eventos e ajusta as sequências de IDs. Exibe apenas contagens. Usa uma transação e recusa um destino já populado. Sessões e checkouts temporários não são copiados; todos precisam entrar novamente.

O comando usa root apenas para ler a cópia local durante a importação; a conexão ao PostgreSQL continua sendo a do usuário restrito `campus_app`. A aplicação normal não executa como root.

Se algo falhar, não apague banco nem chave: confira se o destino está vazio, se o SQLite foi atualizado e se a chave é a original. Uma nova implantação sem dados antigos pode pular esta etapa.

## 5. Iniciar a loja

```bash
sudo docker compose up -d
sudo docker compose ps
```

Abra **https://10.81.211.237**. O certificado é autoassinado: configure sua confiança nos computadores da aula após conferir a impressão digital SHA-256 por um canal confiável com o professor. Distribua apenas `deploy/tls/fullchain.pem`. Nunca distribua `privkey.pem` nem `market.key`.

Para verificar o servidor na própria VM sem desativar TLS:

```bash
sudo curl --cacert deploy/tls/fullchain.pem https://10.81.211.237/style.css
```

Se mudar o IP, atualize `.env` e forneça um novo certificado com o IP correto. O preparador preserva o certificado existente; não o renova silenciosamente. Para renovar, forneça o novo par `fullchain.pem`/`privkey.pem`, confira permissões e execute `sudo docker compose restart web`.

O bind limita a publicação à interface desse IP, mas não decide quais redes conseguem roteá-lo. Confirme a restrição à rede da aula no roteador/firewall. Portas publicadas pelo Docker podem contornar regras UFW; use regras compatíveis com o Docker, sem bloquear seu SSH por engano. Veja as [limitações oficiais de firewall](https://docs.docker.com/engine/install/debian/#firewall-limitations).

## 6. Consultar o banco

```bash
sudo docker compose exec db psql -U postgres -d campus
```

No psql:

```sql
\dt
SELECT COUNT(*) FROM users;
SELECT COUNT(*) FROM profiles;
SELECT id, title, stock FROM listings ORDER BY id;
SELECT id, quantity, total_cents, status FROM purchases ORDER BY id;
\q
```

`users.name`, `profiles.data` e `purchases.delivery` aparecem criptografados. Isso é esperado. A aplicação não usa o superusuário: usa `campus_app`, sem privilégios de criar bancos, criar roles ou administrar o servidor. Ele pode criar e alterar suas próprias tabelas para executar as migrações; numa operação maior, separe também as credenciais de migração.

## Simulação completa a cada 10 segundos

O script `simulate_market.py` cria **5 usuários, 5 produtos e 5 transações de compra por lote**, com o primeiro lote imediato e os próximos a cada dez segundos. Usa diretamente o PostgreSQL da aplicação, com a mesma chave, criptografia e regra de estoque/compra. Os usuários têm dados fictícios e senhas aleatórias não exibidas. Cada conta anuncia um produto e compra o de outra conta. Os pedidos ficam com pagamento simulado aprovado, sem cobrança real.

Encerre o gerador anterior `generate_users.py` se ele estiver rodando, para que somente o novo simulador acrescente contas. Copie os arquivos atualizados do projeto para a VM e, na pasta do projeto, execute:

```bash
sudo sh deploy/start-simulation.sh
sudo docker compose logs -f --tail=20 simulator
```

O script atualiza as imagens, inicia os serviços, confirma um lote dentro do próprio container da aplicação e exibe as contagens do banco que alimenta o painel. Se houver falha, mostra o estado dos containers e os logs. Não execute `python3 simulate_market.py` diretamente no host Kali: sem as variáveis do container, ele usa o SQLite local, enquanto o site usa PostgreSQL. O comando Compose equivalente para iniciar os serviços é `sudo docker compose --profile simulation up -d --build --wait`.

Se a página abrir sem registros, execute `sudo sh deploy/start-simulation.sh` na mesma pasta de implantação usada pelo site. Os dados criados por `generate_users.py` não aparecem no painel de simulação completa. Para diagnosticar um serviço que não gera dados:

```bash
sudo docker compose --profile simulation ps -a
sudo docker compose logs --tail=30 simulator
```

Ctrl+C sai dos logs. O serviço `simulator` continua em segundo plano, após fechar o SSH e após reiniciar a VM quando o Docker iniciar. Para parar:

```bash
sudo docker compose --profile simulation stop simulator
```

Para gerar apenas o lote do intervalo atual, com a aplicação atualizada em execução:

```bash
sudo docker compose exec app python simulate_market.py --once
```

Entre com uma conta criada manualmente e abra **https://10.81.211.237/simulation**, ou clique em **Simulação** no menu. A página mostra tabelas de usuários, produtos e compras, com IDs, relacionamentos, estoque, valores em centavos e estados das transações. Há contadores acumulados, paginação de 25 linhas por tabela e atualização automática a cada dez segundos. Mostra apenas registros identificados como simulação; não mostra senhas, hashes, CPF, cartões nem endereços. Não é um editor SQL: as consultas são fixas e somente de leitura.

Cada lote é salvo inteiro em uma única transação. Falhas revertem o lote; o serviço tenta novamente no próximo intervalo. Uma identificação persistente impede gerar duas vezes o mesmo intervalo de dez segundos após reinício ou se duas instâncias forem iniciadas. Se um lote demorar mais que dez segundos, o próximo começa ao terminar o anterior, sem sobreposição. As tabelas `simulation_*` identificam esses registros e entram nos backups normais. O gerador não começa ao abrir a loja: o perfil `simulation` precisa ser iniciado explicitamente. O novo simulador não precisa de `MARKET_REGISTER_LIMIT=120`; se somente ele for utilizado, a configuração padrão de cadastro pode ser mantida.

## 7. Gerador de 10 usuários a cada 30 segundos (opcional)

Na VM, acrescente ou ajuste esta variável no `.env` (preserve as outras linhas):

```dotenv
MARKET_REGISTER_LIMIT=120
```

Recrie a aplicação com o código atualizado e essa configuração:

```bash
sudo docker compose up -d --build app
```

Isso permite até 120 tentativas de cadastro por IP a cada cinco minutos, suficiente para os lotes da simulação. O limite de login continua em 10 tentativas por cinco minutos. Sem a variável, o cadastro também mantém o limite padrão de 10. Ao terminar a atividade, remova essa linha do `.env` e recrie a aplicação para restaurar o limite padrão.

No Windows, com o certificado público copiado para `certificado-vm.pem`:

```powershell
.\.venv\Scripts\python.exe generate_users.py --url https://10.81.211.237 --ca certificado-vm.pem
```

O gerador cadastra o primeiro lote de 10 imediatamente e os próximos a cada 30 segundos, continuamente. Continua usando a rota normal de cadastro, sem acesso direto ao PostgreSQL. Ctrl+C encerra; `--count 25` limita o total a 25 contas. `--batch-size` e `--interval` ajustam o tamanho e o intervalo dos lotes. Não é iniciado automaticamente no servidor. Ele apenas cadastra contas fictícias; não realiza compras.

Para executar o gerador na própria VM Kali, a biblioteca padrão do Python é suficiente:

```bash
sudo python3 generate_users.py --url https://10.81.211.237 --ca deploy/tls/fullchain.pem
```

Para continuar rodando depois de fechar o terminal SSH, execute na pasta do projeto:

```bash
sudo sh -c 'nohup python3 -u generate_users.py --url https://10.81.211.237 --ca deploy/tls/fullchain.pem > generate-users.log 2>&1 < /dev/null & echo $! > generate-users.pid'
```

Inicie apenas uma instância. Para acompanhar e depois encerrar esse processo:

```bash
sudo tail -f generate-users.log
# Ctrl+C sai da visualização do log; o gerador continua rodando.
sudo sh -c 'kill "$(cat generate-users.pid)"'
```

Falhas temporárias de conexão, HTTP 429 e HTTP 5xx são repetidas após o intervalo configurado (ou após `Retry-After` em segundos, se maior). Erros de certificado ou cadastro inválido encerram o gerador. Se um lote demorar mais que 30 segundos, o próximo começa ao terminar o anterior, sem sobreposição. O modo `nohup` continua após sair do SSH, mas não reinicia automaticamente após reboot da VM.

## 8. Backup e restauração

Para criar um dump consistente:

```bash
sudo sh deploy/backup.sh
```

O arquivo fica em `backups/`. Guarde-o fora da VM. Faça também backup **separado** da `.secrets/market.key`; o dump sozinho não permite ler os dados criptografados. As senhas do banco também devem ser guardadas para recuperar a configuração.

Restaure em uma **implantação nova**, com PostgreSQL inicializado mas a aplicação ainda parada, usando a chave original antes de iniciar o app:

```bash
sudo docker compose up -d --wait db
sudo docker compose exec -T db pg_restore -U postgres -d campus \
  --exit-on-error --single-transaction < backups/SEU-BACKUP.dump
sudo docker compose up -d
```

O restore não usa `--clean`: falha se houver tabelas conflitantes, evitando apagar uma base em uso. Teste a restauração e o login antes de depender do backup. `docker compose down` preserva o volume; **não use `down -v`**, pois remove o banco persistente.

## Operação e limites

```bash
sudo docker compose logs --tail=100 app db web
sudo docker compose restart app
sudo docker compose exec app python app.py --events
```

Nginx termina HTTPS, limita requisições e sobrescreve os cabeçalhos de proxy. A aplicação confia somente no IP fixo do Nginx para identificar clientes. Gunicorn roda com um processo e oito threads: mantenha um processo enquanto os limites da aplicação forem em memória. Para escalar, mova os limites para armazenamento compartilhado. A integração do proxy segue a [documentação do Gunicorn](https://gunicorn.org/deploy/).

Não use `python app.py --host 0.0.0.0` como servidor da VM. O `http.server` continua disponível apenas como modo de desenvolvimento; a implantação usa a entrada WSGI no Gunicorn.

As imagens PostgreSQL 17 e Nginx stable recebem atualizações de patch pelas tags; atualize e valide deliberadamente (`docker compose pull` e rebuild) após backup. A configuração usa uma rede Docker `172.30.44.0/24`; se ela conflitar com uma rede existente, ajuste Compose, IP de proxy e Gunicorn juntos.

Esta é uma implantação persistente de laboratório, não uma certificação de produção para dados ou pagamentos reais. Continua sem MFA, recuperação/verificação de e-mail, pagamento real, transportadora e limitação distribuída. Não há auditoria independente nem teste de carga. Use dados sintéticos e mantenha o escopo local da aula.
