"""ESBOCO (nao operacional) da API HTTP para a interface grafica.

Status: apenas estrutura/referencias. Nao ha servidor configurado nem dependencias
instaladas. Para ativar no futuro, escolha um framework e implemente os TODOs.

Opcoes de referencia (RHEL 8.10):
  - Flask      https://flask.palletsprojects.com/       (python3-flask via EPEL / pip)
  - FastAPI    https://fastapi.tiangolo.com/            (requer Python >= 3.8 -> python39 do AppStream)
  - stdlib     http.server (apenas leitura, sem dependencias)
Publicar atras de nginx/httpd com TLS e autenticacao (ex.: mod_auth_gssapi/Kerberos).

Fonte de dados: o mesmo SQLite do ibmon, aberto SOMENTE LEITURA
    sqlite3.connect("file:/var/lib/ibmon/ibmon.db?mode=ro", uri=True)
ou os mesmos dados de 'ibmon export -f json' (contrato: gui/schema/graph.schema.json).

Endpoints previstos:
  GET /api/graph            -> ibmon.export.graph(store)          (nos + links + severidade)
  GET /api/status           -> ultimo snapshot por porta (counter_state)
  GET /api/events?open=1    -> store.events(...)
  GET /api/node/<guid>      -> detalhes de um no (portas, IPs, posicao)
  GET /metrics              -> ibmon.export.prometheus(store)   (alternativa ao textfile)
"""


def create_app(db_path="/var/lib/ibmon/ibmon.db"):  # pragma: no cover - esboco
    # TODO: instanciar framework, abrir Store em modo somente leitura e registrar rotas acima.
    raise NotImplementedError("API grafica ainda nao implementada - ver gui/README.md")
