# Interface gráfica — estrutura e referências (NÃO operacional)

Esta pasta só prepara o terreno. Nada aqui é instalado, iniciado ou configurado pelo `install.sh`.

| Caminho | Conteúdo | Situação |
|---|---|---|
| `schema/graph.schema.json` | Contrato de dados entre o núcleo e a GUI (`ibmon export -f json`) | **pronto** (gerado pelo núcleo) |
| `api/app.py` | Esboço da API HTTP (endpoints previstos, opções de framework) | esboço, `NotImplementedError` |
| `web/index.html` | Esqueleto da página do grafo, com referências de bibliotecas | esboço, sem JS |
| `grafana/` | Caminho alternativo: Prometheus textfile → Grafana (Node Graph) | referência |

## Princípios
- A GUI **só lê**: o banco SQLite (modo `ro`) ou o JSON exportado. Toda coleta continua no `ibmon check`/`discover`,
  então a GUI não gera MADs na fabric nem tempo extra de coleta.
- O núcleo já entrega o que a GUI precisa: nós (GUID, tipo, hostname, LID, posição), portas (GUID, LID,
  estado, taxa, interfaces IPoIB e IPs), arestas origem/destino com tipo (`host-switch` / `switch-switch`),
  severidade e TX/RX por ponta.
- Visualização prévia **já disponível** sem GUI: `ibmon export -f dot | dot -Tsvg > fabric.svg`
  (requer `graphviz`, opcional).

## Opções de evolução
1. **Grafana + Prometheus** (menos código): ver `grafana/README.md`.
2. **Página própria** (Flask/FastAPI + Cytoscape.js/vis-network): ver `api/app.py` e `web/index.html`.
3. **Multi-nó**: cada nó roda `ibmon check`; uma instância central roda `discover --fabric` e agrega
   (ex.: cada nó publica `export -f json`, ou métricas via Prometheus). O banco já usa GUIDs como chave,
   então os dados de vários nós podem ser mesclados sem conflito.
