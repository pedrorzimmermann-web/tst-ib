# ibmon — monitor de links InfiniBand (Mellanox/NVIDIA) para HPC

Verifica os links IB de cada nó, monta a representação lógica **host → HCA/porta → switch → switch**,
acompanha **TX/RX** e detecta **erros, PortXmitWait (congestionamento), links degradados/caídos**
e outros eventos. Feito para **RHEL 8.10**, sem dependências fora da biblioteca padrão do Python 3.6.

```
node01 [local]  @ Rack A01 U12
  ├─ mlx5_0/1  guid 0x0c42a10300b2c3d5  lid 5  ACTIVE ib0 (10.10.0.1/16)
  │      └──> leaf01 [porta 1] 4xHDR
  └─ mlx5_1/1  guid 0x0c42a10300b2c3e5  lid 6  ACTIVE ib1 (10.11.0.1/16)
         └──> leaf02 [porta 1] 4xHDR
Switches
  leaf01  guid 0x248a070300f0a1b0  lid 1  portas 40  links 4 (hosts 2, switches 2)
      porta 35  ──> spine01 [porta 1] 4xHDR
      porta 36  ──> leaf02 [porta 36] 4xHDR
```

## Requisitos (RHEL 8.10)

```bash
dnf install -y python36 infiniband-diags iproute
```

- `/usr/bin/python3` (pacote `python36`). Opcional: `graphviz` para gerar SVG a partir do `export -f dot`.
- **Leitura de contadores locais (sysfs) não exige root.** `smpquery`, `perfquery` e `ibnetdiscover`
  exigem acesso a `/dev/infiniband/umad*` (root).

## Instalação

```bash
sudo ./install.sh            # copia para /opt/ibmon, cria /etc/ibmon/ibmon.conf e as unidades systemd
```

```bash
sudo ./install.sh --enable   # idem, e já habilita os timers
```

Também roda direto da árvore do projeto: `./bin/ibmon <comando>`.

## Uso

| Comando | O que faz | Custo |
|---|---|---|
| `ibmon doctor` | Confere sysfs, HCAs, ferramentas e permissões | — |
| `ibmon discover` | Inventário local (HCAs, GUIDs, portas, LIDs, PCI, IPoIB/IPs) + switch vizinho de cada porta | ~3 MADs/porta, **só se algo mudou** |
| `ibmon discover --fabric` | Topologia completa (`ibnetdiscover`): switches e interligações | alto; grava **só se o hash mudou** |
| `ibmon check` | Lê contadores, calcula TX/RX, XmitWait/s, deltas de erro e gera eventos | milissegundos (sysfs) |
| `ibmon check -w 5` | Modo contínuo para acompanhar ao vivo | |
| `ibmon status` | Última coleta por porta | só banco |
| `ibmon topo` | Representação lógica host → switch → switch | só banco |
| `ibmon events [-a]` | Eventos abertos (ou todos) | só banco |
| `ibmon export -f json\|dot\|prom` | Grafo (contrato da GUI), Graphviz ou métricas Prometheus | só banco |
| `ibmon prune [--vacuum]` | Aplica a retenção de eventos | |

Códigos de saída no padrão Nagios: `0` OK, `1` WARNING, `2` CRITICAL, `3` UNKNOWN. Isso permite
plugar o `ibmon check` direto em Nagios/Icinga/Zabbix.

Fluxo típico:

```bash
sudo ibmon discover --fabric && sudo ibmon check && sleep 60 && sudo ibmon check && ibmon topo && ibmon status
```

## Testes sem hardware (simulação)

```bash
python3 -m unittest discover -s tests -v
```

Ambiente simulado interativo (sysfs + saídas de `smpquery`/`perfquery`/`ibnetdiscover`/`ip`):

```bash
python3 tools/mkdemo.py create /tmp/ibdemo
```

```bash
I="./bin/ibmon --fixtures /tmp/ibdemo --db /tmp/ibdemo/ibmon.db"
$I discover --fabric; $I check
python3 tools/mkdemo.py traffic /tmp/ibdemo --errors --congestion   # ou --down mlx5_1
$I check; $I topo; $I events
```

## O que é monitorado

**Estado do link** (a cada `check`, via sysfs): porta não `ACTIVE` ou estado físico diferente de `LinkUp`
→ CRITICAL; largura degradada (ex.: `1X` em vez de `4X`) → WARNING; taxa abaixo de `expected_rate_gbps` → WARNING.

**Contadores** (limiar `aviso,crítico` sobre o **delta entre duas coletas**; com sufixo `_per_sec`, sobre a taxa):

| Contador | Indica |
|---|---|
| `symbol_error`, `link_error_recovery`, `local_link_integrity_errors` | cabo/transceptor/porta com problema físico |
| `link_downed` | o link caiu (flap) |
| `port_rcv_errors`, `port_rcv_remote_physical_errors` | pacotes corrompidos recebidos |
| `port_xmit_discards` | descartes (congestionamento severo, rota ou destino inválido) |
| `port_xmit_wait` (`port_xmit_wait_per_sec`) | **congestionamento**: ticks com dados para enviar e sem crédito |
| `excessive_buffer_overrun_errors`, `*_constraint_errors`, `VL15_dropped` | buffer, particionamento, SMP |
| `hw_counters` mlx5: `out_of_buffer`, `local_ack_timeout_err`, `rnr_nak_retry_err`, `packet_seq_err`… | problemas de RDMA vistos pela HCA |

- **Saturação**: os contadores de erro do IB **não dão a volta**: param no valor máximo (ex.: 65535).
  O ibmon detecta isso e avisa que o contador parou de medir (zerar com `perfquery -R`).
- **Reset de contadores** (valor menor que o anterior) é tratado sem gerar falso positivo.
- **Lado do switch**: com `peer_counters = yes`, o `check` também lê (via `perfquery`) a porta do switch
  ligada a cada HCA local. Muitos defeitos só aparecem do lado do switch.
- **Topologia**: um link que some entre duas varreduras `--fabric` gera o evento `link_removed`, que é
  fechado sozinho quando o link volta.
- **TX/RX**: `port_xmit_data`/`port_rcv_data` × 4 bytes ÷ intervalo.

Os limiares padrão são um ponto de partida. Ajuste em `/etc/ibmon/ibmon.conf` depois de observar a
linha de base do seu cluster (principalmente `port_xmit_wait_per_sec`, que depende da carga).

## Otimização: o que é coletado, quando e o que é gravado

- **Coleta periódica barata**: `check` lê apenas o sysfs (sem tráfego na fabric). Comandos externos
  só rodam quando necessário.
- **Inventário incremental**: é calculado um *fingerprint* de cada porta (GUID, estado, LID, SM LID, taxa).
  O inventário e a consulta de vizinhos só são refeitos quando ele muda ou após `inventory_ttl`.
- **Fabric por hash**: o `ibnetdiscover` roda no máximo a cada `fabric_ttl` (timer de 1 h + TTL de 6 h).
  Se o hash da topologia não mudou, nada é gravado.
- **Escrita condicional**: toda gravação compara antes de escrever; linhas iguais não são tocadas.
- **Sem séries históricas**: para cada porta guarda-se apenas o **último snapshot** dos contadores,
  usado para calcular os deltas. O histórico, se desejado, fica a cargo do Prometheus/Grafana (ver `gui/`).
- **Eventos deduplicados**: um único registro aberto por porta+tipo, com contador e `last_seen`.
  O registro é fechado quando o problema some e expurgado após `events_retention_days` ou acima de
  `events_max_rows`.
- O banco fica na ordem de dezenas de KB por nó (SQLite em modo WAL).

### Dados armazenados (`/var/lib/ibmon/ibmon.db`)

| Tabela | Campos |
|---|---|
| `nodes` | GUID, tipo (CA/SW), nome, hostname, descrição, nº de portas, LID, sys GUID, posição (rack/U), local? |
| `ports` | GUID do nó, nº da porta, GUID da porta, HCA (`mlx5_0`), PCI (posição física), LID, estado, estado físico, taxa, interfaces IPoIB, IPs |
| `links` | origem (GUID, porta) ↔ destino (GUID, porta), velocidade, fonte (local/fabric) |
| `counter_state` | último snapshot de contadores e taxas por porta (sobrescrito) |
| `events` | alertas deduplicados |
| `meta` | fingerprints, hashes e timestamps |

A quantidade de links, os switches envolvidos e as interligações entre switches são derivados de `links` + `nodes`
(ver `ibmon topo` e `ibmon export -f json`).

## Agendamento (systemd)

- `ibmon-check.timer`: a cada 60 s → `ibmon check --quiet` (só imprime/loga quando há alerta; vai para o journal).
- `ibmon-discover.timer`: a cada 1 h → `ibmon discover --auto-fabric` (o `ibnetdiscover` só roda se `fabric_ttl` expirou) + `prune`.

```bash
journalctl -u ibmon-check -f
```

## Estrutura

```
bin/ibmon                 launcher
ibmon/
  sysfs.py                coleta local (/sys/class/infiniband, /sys/class/net)
  ibtools.py              smpquery (vizinho), perfquery (contadores remotos), ibnetdiscover (topologia)
  core.py                 inventário incremental, descoberta da fabric, check
  analyze.py              deltas, taxas, limiares, saturação, estado do link
  store.py                SQLite mínimo com escrita condicional e eventos deduplicados
  render.py / export.py   saída de terminal; JSON/DOT/Prometheus
  config.py / cli.py      configuração INI e linha de comando
contrib/                  ibmon.conf.example, unidades systemd
tools/mkdemo.py           gerador do ambiente simulado
tests/                    testes unitários e de fluxo (sem hardware)
gui/                      estrutura e referências da interface gráfica (não operacional)
```

## Próximos passos sugeridos
- Validar em um nó real: `ibmon doctor`, depois comparar `ibmon topo` com `iblinkinfo`, e `ibmon check -w 10` com `perfquery -x`.
- Calibrar os limiares com a linha de base real.
- Multi-nó: distribuir o `check` em todos os nós e centralizar o `discover --fabric` (ver `gui/README.md`).
