"""Execucao de comandos externos (infiniband-diags / iproute) com timeout.

Modo "fixtures": em vez de executar, le a saida de <dir>/cmd/<comando>.txt,
permitindo testar sem hardware InfiniBand.
"""

import os
import re
import shutil
import subprocess

EXTRA_PATH = "/usr/sbin:/sbin:/usr/bin:/bin"


def fixture_name(args):
    return re.sub(r"[^A-Za-z0-9_.,=+-]", "_", "_".join(args)) + ".txt"


class Runner(object):
    def __init__(self, timeout=30, fixtures=None):
        self.timeout = timeout
        self.fixtures = fixtures
        self.calls = []  # historico (diagnostico / testes)

    def which(self, tool):
        if self.fixtures:
            d = os.path.join(self.fixtures, "cmd")
            return any(f.startswith(tool + "_") or f == tool + ".txt" for f in os.listdir(d)) if os.path.isdir(d) else False
        path = os.environ.get("PATH", "") + ":" + EXTRA_PATH
        return shutil.which(tool, path=path)

    def run(self, args):
        """Retorna (returncode, stdout). Nunca levanta excecao."""
        self.calls.append(list(args))
        if self.fixtures:
            p = os.path.join(self.fixtures, "cmd", fixture_name(args))
            if not os.path.isfile(p):
                return 127, ""
            with open(p) as fh:
                return 0, fh.read()
        exe = self.which(args[0])
        if not exe:
            return 127, ""
        try:
            proc = subprocess.run(
                [exe] + list(args[1:]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=self.timeout,
            )
            return proc.returncode, proc.stdout
        except subprocess.TimeoutExpired:
            return 124, ""
        except OSError:
            return 126, ""


def parse_dotted(text):
    """Converte saida no estilo 'Chave:.......valor' (smpquery/perfquery) em dict."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\s*([^:]+?):\.*(.*)$", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out
