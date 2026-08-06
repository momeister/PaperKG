"""Ein lebender ``cs serve``-Prozess je Code-Projekt.

Ohne Pool kostete jeder Tastendruck in der Symbolsuche einen Prozessstart plus
das Öffnen einer mehrstelligen MB-SQLite. Mit Pool zahlt man das einmal und
danach nur noch die Abfrage.

Der Pool ist ein Modul-Singleton, weil das Backend einer ist. Er räumt von selbst
auf: was länger als ``idle_seconds`` nicht gebraucht wurde, wird geschlossen —
ein offener Index hält sonst Speicher und ein Dateihandle für ein Projekt, das
die Nutzerin vor einer Stunde zugeklappt hat.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from codegraph.binary import index_path, load_config
from codegraph.rpc import CodeGraphClient

#: Nach dieser Leerlaufzeit wird ein Kind beendet.
DEFAULT_IDLE_SECONDS = 600.0

#: Mehr gleichzeitig offene Projekte als das ergeben keinen Sinn; das älteste
#: weicht. Verhindert, dass ein Skript mit hundert Projekten hundert Prozesse
#: offen hält.
DEFAULT_MAX_CLIENTS = 8


class ClientPool:
    def __init__(
        self,
        *,
        idle_seconds: float | None = None,
        max_clients: int | None = None,
        config_path: str = "config.yaml",
    ) -> None:
        # Werte aus dem ``codesearch:``-Block, sofern nicht ausdrücklich gesetzt.
        section = load_config(config_path)
        self.idle_seconds = float(
            idle_seconds
            if idle_seconds is not None
            else section.get("idle_seconds") or DEFAULT_IDLE_SECONDS
        )
        self.max_clients = max(
            1,
            int(
                max_clients
                if max_clients is not None
                else section.get("max_clients") or DEFAULT_MAX_CLIENTS
            ),
        )
        self.config_path = config_path
        self._clients: dict[str, CodeGraphClient] = {}
        self._lock = threading.Lock()

    def get(
        self,
        code_project_id: str,
        root: str | os.PathLike[str],
        *,
        db_path: str | os.PathLike[str] | None = None,
        key: str | None = None,
    ) -> CodeGraphClient:
        """Offenes Kind für dieses Projekt, notfalls ein neues.

        Ein Kind, das inzwischen gestorben ist (Absturz, Zeitablauf), wird
        stillschweigend ersetzt — der Aufrufer soll den Unterschied nicht merken.

        ``db_path`` (mit ``key``) oeffnet einen *zweiten* Index fuer dasselbe
        Projekt — die Sandbox: ein Worktree unter anderem Root bekommt einen
        eigenen ``.csdb``-Pfad (``data/codegraph/<id>__sb_<sb>/index.csdb``)
        und einen eigenen Pool-Schlüssel. Ohne ``db_path`` gilt der normale,
        pro Projekt einen Index.
        """
        cache_key = key or code_project_id
        with self._lock:
            self._evict_idle_locked()
            existing = self._clients.get(cache_key)
            if existing is not None:
                if existing.alive and Path(root).resolve() == existing.root:
                    existing.last_used = time.monotonic()
                    return existing
                existing.close()
                self._clients.pop(cache_key, None)

            while len(self._clients) >= self.max_clients:
                oldest = min(self._clients, key=lambda k: self._clients[k].last_used)
                self._clients.pop(oldest).close()

            resolved_db = (
                Path(db_path)
                if db_path is not None
                else index_path(code_project_id, self.config_path)
            )
            client = CodeGraphClient(
                root,
                str(resolved_db),
                config_path=self.config_path,
            )
            self._clients[cache_key] = client
            return client

    def drop(self, code_project_id: str) -> None:
        """Kind schließen — vor dem Löschen des Index, sonst hängt das Handle."""
        with self._lock:
            client = self._clients.pop(code_project_id, None)
        if client is not None:
            client.close()

    def close_all(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()

    def _evict_idle_locked(self) -> None:
        cutoff = time.monotonic() - self.idle_seconds
        stale = [
            key for key, client in self._clients.items() if client.last_used < cutoff
        ]
        for key in stale:
            self._clients.pop(key).close()


#: Der Pool des Backends. Router greifen darüber zu.
POOL = ClientPool()
