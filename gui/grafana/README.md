# Grafana (referência — não configurado)

Caminho sugerido, sem desenvolvimento de frontend próprio:

1. Definir `prom_textfile` em `/etc/ibmon/ibmon.conf`, por exemplo
   `/var/lib/node_exporter/textfile_collector/ibmon.prom`. O `ibmon check` regrava o arquivo
   de forma atômica a cada execução, sem guardar histórico localmente.
2. node_exporter com `--collector.textfile.directory=/var/lib/node_exporter/textfile_collector`
   - https://github.com/prometheus/node_exporter#textfile-collector
3. Prometheus coleta os nós; o Grafana consulta o Prometheus.
   - Painel *Time series*: `ibmon_port_bytes_per_second`, `ibmon_port_xmit_wait_per_second`
   - Painel *Stat/Alert*: `increase(ibmon_port_counter{counter="symbol_error"}[15m]) > 0`
   - Painel *Node Graph* (topologia): https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/node-graph/
     — pode ser alimentado pela API `GET /api/graph` (gui/api) via plugin Infinity/JSON.

Coloque aqui, no futuro, os dashboards exportados (`*.json`).

Métricas expostas: ver `ibmon export -f prom`.
