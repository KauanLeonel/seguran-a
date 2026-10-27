# Campus Market

Marketplace para a aula de segurança: contas, dados pessoais, anúncios de vendedores, estoque, compra, frete e acompanhamento de entrega. A interface é de uma loja; pagamentos e entregas são simulados.

## Servidor Kali Linux e PostgreSQL

A implantação para **Kali Linux / https://10.81.211.237** está em [deploy/KALI.md](deploy/KALI.md): PostgreSQL persistente, Gunicorn, Nginx com HTTPS, migração do SQLite e backup. O modo local abaixo continua usando SQLite em `data/market.db`; o ambiente da VM usa PostgreSQL com `MARKET_DATABASE=postgres`.

## Iniciar no Windows

Requer Python 3.12 ou superior. Na primeira instalação:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

O ambiente deste projeto já recebeu a dependência `cryptography`. Para abrir a loja:

```powershell
.\start.cmd
```

Abra **http://localhost:8000**. Alternativa: `.\.venv\Scripts\python.exe app.py`. Não é necessário ativar o ambiente no PowerShell. Reinicie após alterar o código e atualize com `Ctrl+Shift+R`.

## Como apresentar a atividade

1. Crie duas contas preenchendo manualmente nome, e-mail, senha (12 a 128 caracteres), CPF, telefone, endereço completo, cartão de teste e validade. Todos os campos começam vazios e são obrigatórios. O cadastro volta à página inicial já conectado. Use dois navegadores ou uma janela anônima para comprador e vendedor.
2. Em **Minha conta → Dados pessoais e endereço**, confira nome, CPF completo, telefone, endereço e cartão mascarado. Altere os dados usando a senha para confirmar.
3. Como vendedor, abra **Minhas vendas → Anunciar produto**. Informe nome, descrição, preço e estoque. O anúncio aparece no catálogo.
4. Como comprador, escolha produto, quantidade e entrega. Frete padrão: R$ 15; expresso: R$ 30. Preço, total e estoque são controlados pelo servidor.
5. O vendedor abre o pedido em **Minhas vendas** e marca como enviado. O comprador abre o pedido em **Minha conta** e confirma o recebimento.

O pedido passa por **Pagamento aprovado → Em transporte → Entregue**. Os quatro produtos iniciais pertencem à loja e entram automaticamente em transporte. Os códigos de rastreio são internos e os prazos ilustrativos. Não há envio físico, cobrança, estorno ou comunicação com serviços externos.

## Dados sensíveis da base

O usuário preenche seu perfil no cadastro, sem dados pessoais predefinidos. O perfil pode ser editado depois com confirmação da senha:

| Campo | Armazenamento e acesso |
| --- | --- |
| Nome completo | Criptografado; visível ao titular e, no pedido, ao vendedor responsável |
| CPF | Completo e criptografado; mascarado no resumo, completo no perfil do titular |
| Telefone e endereço | Criptografados; visíveis ao titular e ao vendedor do respectivo pedido |
| Cartão | Número de teste completo e validade criptografados; exibição mascarada; sem CVV |
| Senha | Hash scrypt com salt individual, nunca criptografia reversível |
| E-mail | Em claro para identificação da conta; use endereço inventado |

A validação verifica formato e tamanho; não verifica identidade, existência de endereço nem dígitos verificadores de CPF. Para a aula, digite dados inventados. O gerador opcional usa CPF (`00000000000`), telefone (`11000000000`) e CEP (`00000000`) deliberadamente fictícios. Esses valores não são preenchidos automaticamente no formulário. Nunca use dados pessoais reais.

## Simular 5 usuários, 5 produtos e 5 transações a cada 10 segundos

Com a loja aberta, execute em outro terminal no Windows:

```powershell
.\.venv\Scripts\python.exe simulate_market.py
```

O primeiro lote é imediato; os próximos começam a cada 10 segundos até Ctrl+C. Cada lote cria cinco contas com perfil fictício, cinco produtos (um por conta) e cinco compras entre contas diferentes, com pagamento simulado aprovado. Cada produto começa com cinco unidades; a compra consome uma. Produtos e pedidos aparecem também nas telas normais da loja.

Entre com uma conta criada manualmente e abra **http://localhost:8000/simulation**, ou use **Simulação** no menu. A página mostra três tabelas com colunas semelhantes às do SQL (`users`, `listings`, `purchases`), contadores acumulados e 25 registros por tabela em cada página. Atualiza automaticamente a cada dez segundos. Mostra apenas os registros desse simulador; senhas, hashes, CPF, cartões e endereços não são exibidos. Os nomes fictícios são decifrados apenas para exibição. Os nomes, perfis e entregas permanecem criptografados no banco.

Para gerar somente o lote do intervalo atual:

```powershell
.\.venv\Scripts\python.exe simulate_market.py --once
```

O script usa diretamente o mesmo banco e a mesma chave da aplicação. No Windows, usa o SQLite local por padrão; no Kali, execute no container configurado com PostgreSQL, seguindo [deploy/KALI.md](deploy/KALI.md#simulação-completa-a-cada-10-segundos). Não precisa aumentar o limite de cadastros HTTP. Cada lote é uma transação atômica e tem identificação persistente por intervalo para evitar duplicação após reinício ou início duplicado. Uma falha reverte o lote inteiro e o modo contínuo tenta novamente após o intervalo. `--interval` permite outro intervalo em segundos. Encerre o gerador anterior de usuários se quiser manter somente este ritmo de cinco contas por dez segundos. Se um lote demorar mais que o intervalo, o próximo começa ao terminar o anterior, sem sobreposição.

## Gerar apenas usuários (opcional: 10 a cada 30 segundos)

O gerador é separado: iniciar a loja não cria usuários. Para permitir esse ritmo no modo Windows, inicie a loja no PowerShell com o limite de cadastro do laboratório (120 tentativas por IP a cada cinco minutos):

```powershell
$env:MARKET_REGISTER_LIMIT = '120'
.\start.cmd
```

Se a loja já estiver aberta, encerre-a com Ctrl+C e inicie novamente nesse terminal. O limite de login continua em 10 tentativas por IP a cada cinco minutos. Sem a variável, o cadastro mantém o limite padrão de 10 tentativas por cinco minutos. No Kali/Docker, configure a variável conforme a seção do gerador opcional em [deploy/KALI.md](deploy/KALI.md).

Com a loja aberta, execute em **outro terminal**:

```powershell
.\.venv\Scripts\python.exe generate_users.py
```

Ele inicia o primeiro lote de **10 cadastros imediatamente** e inicia os próximos a cada **30 segundos**, até você pressionar **Ctrl+C**. Os cadastros de cada lote são sequenciais. Se o servidor levar mais que o intervalo, o próximo lote começa ao terminar o anterior, sem sobrepor lotes. Para alterar o ritmo ou limitar a quantidade total:

```powershell
.\.venv\Scripts\python.exe generate_users.py --batch-size 10 --interval 30 --count 25
```

Nesse exemplo, serão dois lotes de 10 e um de 5. Sem `--count`, ou com `--count 0`, roda continuamente. Para o ritmo antigo, use `--batch-size 1 --interval 60`.

Para HTTPS na rede local:

```powershell
.\.venv\Scripts\python.exe generate_users.py --url https://192.168.1.10:8443 --ca lab.crt --count 10
```

O script preenche todos os dados fictícios e faz cadastro por HTTP usando sessão, Origin e CSRF, sem escrever diretamente no banco. Gera e-mails únicos `@example.test` e senhas aleatórias, que não são exibidas nem salvas em arquivo. Esses usuários servem para povoar a base; crie suas contas manualmente se precisar entrar nelas. O terminal mostra horário, e-mail e contagens por lote. Falhas temporárias de conexão, HTTP 429 e HTTP 5xx são repetidas após o intervalo (ou o `Retry-After` em segundos, se maior). Erros de certificado, formulário ou outras recusas encerram o script com mensagem. A verificação do certificado permanece ativa. O gerador apenas cadastra contas; não simula navegação nem compras.

Cartões aceitos: **4242 4242 4242 4242** e **5555 5555 5555 4444**, exclusivamente para simulação. Outros números são recusados. Não há campo de CVV. O pedido salva apenas os quatro últimos dígitos e uma cópia criptografada dos dados necessários à entrega, preservada mesmo se o perfil mudar depois.

## Criptografia e chave

AES-256-GCM da biblioteca `cryptography`, com nonce aleatório e contexto associado ao usuário ou pedido, protege confidencialidade e integridade e impede copiar um perfil criptografado para outro usuário sem falha de autenticação. Isso segue a preferência por modos autenticados da [OWASP Cryptographic Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html).

- Banco: `data/market.db`.
- Chave padrão: `.secrets/market.key`, gerada na primeira inicialização e excluída do Git.
- Para guardar fora do projeto, configure **antes da primeira inicialização** `$env:MARKET_KEY_FILE = 'C:\caminho-protegido\market.key'`.
- Se mudar o caminho depois, copie a chave existente com segurança para o novo local. Não gere uma chave nova para dados já criptografados.
- Faça backup separado do banco e da chave. Se a chave sumir ou não corresponder ao banco, a aplicação falha sem substituí-la automaticamente.
- Restrinja a leitura da chave à conta do Windows que executa a aplicação. O programa não configura ACLs nem um gerenciador de segredos automaticamente.

Separar arquivos não protege contra comprometimento da conta do sistema operacional. Quem obtiver o banco **e** a chave poderá descriptografar os dados. E-mail, anúncios, preços, estados e relacionamentos dos pedidos continuam em claro. O arquivo SQLite inteiro não é criptografado.

## Controles OWASP aplicados

| Área | Implementação |
| --- | --- |
| Controle de acesso | Consultas de pedidos filtram comprador/vendedor autenticado; usuário não escolhe proprietário; perfil sempre vinculado à sessão |
| Mínimo acesso | Vendedor só recebe contato e endereço dos próprios pedidos; não recebe CPF nem cartão do comprador |
| Senhas | scrypt N=2^17, r=8, p=1; salt aleatório; no máximo dois cálculos simultâneos |
| Sessões | Identificadores aleatórios; hash no banco; expiração absoluta de 30 minutos; troca ao autenticar; revogação no logout |
| Cookies | HttpOnly, SameSite=Strict e Secure no HTTPS |
| CSRF | Token ligado à sessão e validação exata de Origin em todos os POSTs |
| XSS e SQL injection | Escape de conteúdo no HTML, CSP sem JavaScript e SQL parametrizado |
| Alteração de dados sensíveis | Reautenticação por senha, limitada por usuário; sem senha em logs ou HTML de retorno |
| Integridade da compra | Transação para estoque e pedido; token de checkout por usuário, expiração de 15 minutos e proteção contra duplicação no reenvio |
| Entrega | Só o vendedor envia; só o comprador confirma; transições validadas no servidor |
| Abuso | Limites de autenticação, requisições, operações comerciais, checkouts ativos, corpo de formulário e conexões simultâneas |
| Transporte | TLS obrigatório na rede; HTTP apenas no loopback |
| Dados em disco | AES-GCM nos campos pessoais; chave separada; secure_delete do SQLite habilitado |

Referências: [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), [Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) e [Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html). São controles selecionados para a atividade, não certificação OWASP ou PCI DSS.

## Migração das contas antigas

Ao iniciar, a aplicação mantém contas e pedidos anteriores e criptografa nomes existentes. Contas sem perfil recebem campos vazios. Nesta atualização, uma migração única limpa somente os perfis que correspondem exatamente ao conjunto de valores automáticos da versão anterior; perfis diferentes são preservados. Os endereços históricos dos pedidos não são alterados. Contas com perfil incompleto precisam preenchê-lo antes de comprar.

Senhas antigas continuam funcionando e recebem o custo novo de scrypt no próximo login. Pedidos da primeira versão aparecem como anteriores, sem entrega registrada. A migração é idempotente; não apague o banco para atualizar.

Backups antigos podem continuar contendo nomes em claro. O secure_delete não apaga backups nem garante remoção física de dados em SSDs.

## Rede local com HTTPS

Com OpenSSL instalado, substitua o IP nos comandos:

```powershell
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -keyout lab.key -out lab.crt -days 2 -subj "/CN=192.168.1.10" -addext "subjectAltName=IP:192.168.1.10"
.\start.cmd --host 0.0.0.0 --port 8443 --origin https://192.168.1.10:8443 --cert lab.crt --key lab.key
```

Distribua apenas o certificado público lab.crt. Confirme sua impressão digital por canal confiável e configure a confiança nos computadores da aula com o professor. Não compartilhe lab.key nem .secrets/market.key. Libere no firewall somente a porta para a sub-rede da aula. Remova a confiança no certificado ao terminar.

## Testes e eventos

```powershell
.\.venv\Scripts\python.exe -m unittest -v
.\start.cmd --events
```

Os testes cobrem cadastro e login, e-mail duplicado, migração e upgrade de senha, isolamento de pedidos, reautenticação, CSRF, origem, escape de HTML, SQL injection, criptografia vinculada ao registro, chave ausente, estoque concorrente, preço adulterado, compra repetida, checkout expirado e permissões de entrega.

Os eventos locais registram origens e CSRF recusados, limite de autenticação e rotas inexistentes, limitados a 1.000 registros. Não armazenam corpos de formulário, senhas ou cookies. Não são uma auditoria completa das operações de negócio. Um IP não identifica necessariamente uma pessoa.

## Solução de problemas

- **E-mail já utilizado:** entre com a conta existente ou use outro e-mail. Contas não são sobrescritas.
- **Origem recusada:** use exatamente a URL configurada, reinicie o servidor e abra novamente o formulário. Referrer-Policy: same-origin preserva a origem dos formulários locais.
- **Formulário expirado:** reabra o formulário; sessões duram 30 minutos e checkouts 15 minutos.
- **Aviso do DevTools:** a rota /.well-known/appspecific/com.chrome.devtools.json retorna 404 sem criar sessão ou evento. Isso não impede cadastro.
- **ModuleNotFoundError:** use o Python da .venv e instale requirements.txt nela.

## Limites

O modo de desenvolvimento usa http.server; a implantação Kali Linux usa Gunicorn atrás do Nginx. O projeto é destinado ao laboratório autorizado. Não publique na internet nem use cartões reais. Não há recuperação de senha, MFA, verificação de e-mail, transportadora, antifraude, pagamento real ou defesa completa contra negação de serviço. Limites em memória reiniciam com o processo. Execute em máquina virtual isolada e dentro do escopo combinado com a turma. Não há contra-ataque a visitantes.
