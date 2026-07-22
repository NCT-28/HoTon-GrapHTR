"""Background filesystem watcher that keeps each ingested repo's code graph in
sync. On any change under a watched repo root, the whole repo is re-parsed
and its graph replaced (simpler and more robust than incremental per-file
diffing; adequate for the repo sizes this phase targets). File events are
debounced so a burst of changes triggers one re-index."""

import datetime
import os
import threading

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.code_graph_store import GraphStore
from app.code_parser import parse_repo

_DEFAULT_DEBOUNCE_SECONDS = 2.0


class _RepoChangeHandler(FileSystemEventHandler):
    def __init__(self, on_change, debounce_seconds: float):
        self._on_change = on_change
        self._debounce_seconds = debounce_seconds
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule(self, _event=None):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._on_change)
            self._timer.daemon = True
            self._timer.start()

    on_created = _schedule
    on_modified = _schedule
    on_deleted = _schedule
    on_moved = _schedule


class RepoWatcherManager:
    def __init__(self, graph_store: GraphStore, debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS):
        self._graph_store = graph_store
        self._debounce_seconds = debounce_seconds
        self._observer = Observer()
        self._observer.start()
        self._watches: dict[tuple[str, str], object] = {}

    def reindex(self, user_id: str, repo_id: str, local_path: str) -> None:
        symbols, edges = parse_repo(local_path)
        self._graph_store.delete_repo(user_id, repo_id)
        self._graph_store.upsert_repo({
            "user_id": user_id, "repo_id": repo_id, "source": local_path,
            "local_path": local_path, "last_indexed_at": datetime.datetime.utcnow().isoformat(),
        })
        self._graph_store.upsert_symbols([
            {"id": s.id, "repo_id": repo_id, "user_id": user_id, "kind": s.kind, "name": s.name,
             "file_path": s.file_path, "start_line": s.start_line, "end_line": s.end_line, "language": s.language}
            for s in symbols
        ])
        self._graph_store.upsert_code_edges(
            [{"source": e.source, "target": e.target, "type": e.type} for e in edges]
        )

    def watch(self, user_id: str, repo_id: str, local_path: str) -> None:
        key = (user_id, repo_id)
        if key in self._watches:
            return
        handler = _RepoChangeHandler(lambda: self.reindex(user_id, repo_id, local_path), self._debounce_seconds)
        watch = self._observer.schedule(handler, local_path, recursive=True)
        self._watches[key] = watch

    def unwatch(self, user_id: str, repo_id: str) -> None:
        watch = self._watches.pop((user_id, repo_id), None)
        if watch is not None:
            self._observer.unschedule(watch)

    def watched_repos(self) -> set[tuple[str, str]]:
        return set(self._watches.keys())

    def resume_all(self) -> None:
        for repo in self._graph_store.list_repos():
            if os.path.isdir(repo["local_path"]):
                self.watch(repo["user_id"], repo["repo_id"], repo["local_path"])

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
