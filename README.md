# Correlação de erros InfiniBand: `ibqueryerrors -r` × infiniband-exporter

Scripts curtos de comandos (bash + awk + grep, nada para instalar) para descobrir quais portas
de switch e quais hosts acumulam erros/WARNINGs, confirmar se as duas fontes concordam e
cruzar com os jobs. Cada script pode ser **executado** (`bash scripts/02-extrair.sh`) ou ter
os comandos **copiados e colados** no terminal.

Este repositório contém só lógica e dados **fictícios** (`exemplo/`). Nenhum dado real.

## Passo a passo

```bash
S=~/ib-scripts/scripts          # onde estão estes scripts no cluster
```

| Passo | Comando | Onde rodar | O que faz |
|---|---|---|---|
| 1 | `bash $S/01-coleta.sh [porta]` | nó mestre | cria `~/ibbase/AAAAMMDD-HHMM/` com `ibqe.txt` e `exporter.txt` |
| 2 | `bash $S/02-extrair.sh` | dentro da pasta da coleta | gera `ibqe_portas.txt`, `exp_portas.txt` e `ofensores.tsv` (piores primeiro) |
| 3 | `bash $S/03-comparar.sh` | idem | portas nas duas fontes, na outra ponta do link, só em uma |
| 4 | `bash $S/04-delta.sh ../PASTA_ANTERIOR` | idem | o que **cresceu** desde a coleta anterior (`delta.tsv`) |
| 5 | `bash $S/05-jobs.sh [N] [desde]` | idem | `squeue`/`sacct` dos hosts ofensores |
| 6 | `bash $S/06-diagnostico.sh` | idem | troubleshooting quando algo volta vazio |
| 7 | `bash $S/07-categorias.sh [N] [categoria]` | idem | erros do `ibqueryerrors` agrupados por categoria: resumo, top N por categoria (qtd, node, link) e nodes em mais categorias |

Fluxo diário:

```bash
bash $S/01-coleta.sh                      # mostra a pasta criada
cd ~/ibbase/<pasta criada>
bash $S/02-extrair.sh
bash $S/03-comparar.sh
bash $S/04-delta.sh ../<coleta de ontem>
bash $S/05-jobs.sh 20
```

Os contadores são **cumulativos**: o número absoluto mostra o histórico; o que importa para
correlacionar com atividade é o **delta** do passo 4. Não zere contadores
(`ibqueryerrors -k -K`, `perfquery -R`) entre coletas.

## Arquivos gerados (dentro da pasta da coleta)

| Arquivo | Conteúdo |
|---|---|
| `ibqe_portas.txt` | `0x<guid 16 hex>:<porta>` com erro no ibqueryerrors |
| `exp_portas.txt` | idem, no exporter |
| `ofensores.tsv` | `chave  host  contador  valor  chave_remota` (exporter, ordenado pelo valor) |
| `comuns.txt` / `so_ibqe.txt` / `so_exp.txt` | resultado da comparação |
| `outra_ponta.txt` | portas de HCA do ibqueryerrors que casaram com a porta do switch no exporter |
| `delta.tsv` | `delta  chave  host  contador  antes -> agora` |
| `ibqe_erros.tsv` | `categoria  qtd  node  link  chave` (do `07`, uma linha por contador com erro) |

Consultas rápidas:

```bash
grep -P '\tnode01\t' ofensores.tsv            # tudo de um host
grep '^0x0000aa0000000001:' ofensores.tsv     # todas as portas de um switch
cut -f3 ofensores.tsv | sort | uniq -c        # quantas portas por tipo de contador
```

No `07`, o **node** é o lado do link cujo nome parece HCA (`HCA`/`mlx` no nome); se nenhum
parecer, é o lado remoto. Porta sem "Link info" aparece como `-> ?[?]`.

```bash
bash $S/07-categorias.sh 20                      # top 20 de cada categoria
bash $S/07-categorias.sh 50 SymbolErrorCounter   # só uma categoria
grep -P '\tnode01\t' ibqe_erros.tsv               # todas as categorias de um node
```

## Testar sem o cluster

```bash
cd exemplo/20261118-0900 && bash ../../scripts/02-extrair.sh
cd ../20261119-0900     && bash ../../scripts/02-extrair.sh
bash ../../scripts/03-comparar.sh
bash ../../scripts/04-delta.sh ../20261118-0900
```

## Ajustes

- **Porta do exporter**: argumento do `01-coleta.sh` (padrão 9683).
- **Contadores considerados**: variável `FILTRO` do `02-extrair.sh`, ex.
  `FILTRO='symbolerror|rcverror' bash $S/02-extrair.sh`.
- **Labels do exporter**: o `02` aceita `local_guid`/`local_port` (versões atuais) e
  `guid`/`port` (antigas). Se o seu usar outro nome, o `06-diagnostico.sh` mostra quais existem.
- **GUID**: normalizado para minúsculas, com `0x` e 16 dígitos nos dois lados.
- **PBS em vez de Slurm**: troque o final do `05-jobs.sh` por `qstat -n1` (comentário no script).

## Como interpretar

- **Só `linkdownedcounter` sobe**: reboot do nó, reset do HCA ou flap. Compare com
  `ssh <host> last reboot | head`. Valores parecidos em várias portas do mesmo switch
  costumam ser manutenção/reboot em lote.
- **`symbolerrorcounter` / `portrcverrors` também sobem**: suspeite de cabo/transceiver.
- **Contador cresce durante um job, sem reboot no meio**: é o caso a correlacionar com a
  atividade HPC (passo 5).

## Problemas já vistos

| Sintoma | Causa | Correção |
|---|---|---|
| `comm -12` vazio | `exp_portas.txt` com linhas inteiras de métrica: o `sed` procurava `guid=`/`port=` e o exporter usa `local_guid`/`local_port` | o `02` já trata os dois |
| `comm -12` vazio | espaço, `\r` ou ordenação diferente entre os arquivos | `LC_ALL=C sort` nos dois lados; `cat -A` no `06` |
| porta só no ibqueryerrors | erro contado na ponta do HCA, exporter mostra a ponta do switch | `03` casa pela `chave_remota` ("outra ponta") |
| poucas portas no ibqueryerrors | ele só mostra o que passou do limiar | normal; o exporter mostra tudo > 0 |
