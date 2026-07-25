"""Background filesystem watcher that keeps each ingested repo's code graph in
sync. File events within one debounce window are batched into changed/deleted
path sets (see _PendingChanges) and reindex_paths() re-parses+re-embeds only
those files, merging them against the existing graph to resolve cross-file
edges -- not a full repo re-parse/re-embed on every save. reindex() (full)
is kept for ingest_codebase's first-time parse and as a manual fallback.
reindex_paths() is serialized per (user_id, repo_id) the same way reindex()
always was, so a slow embed call from one debounced fire can't overlap with
the next."""

import datetime
import os
import threading

from qdrant_client.models import FieldCondition, Filter, MatchValue, PointIdsList, PointStruct
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.graph.code_graph_store import GraphStore
from app.graph.code_parser import LANGUAGE_CONFIGS, _IGNORED_DIRS, parse_files, parse_repo, resolve_edges
from app.clients.qdrant_store import CODE_SYMBOL_EMBEDDINGS, symbol_point_id

_DEFAULT_DEBOUNCE_SECONDS = 2.0


def _is_relevant_change(path: str) -> bool:
    # parse_repo/parse_files only read recognized source extensions and skip
    # _IGNORED_DIRS, so events elsewhere (.git/index churn, __pycache__,
    # node_modules, non-source file saves) can't change the parsed graph --
    # scheduling a reindex for them just burns work for nothing.
    parts = path.split(os.sep)
    if any(part in _IGNORED_DIRS or part.startswith(".") for part in parts[:-1]):
        return False
    return os.path.splitext(path)[1] in LANGUAGE_CONFIGS


class _PendingChanges:
    """Accumulates changed/deleted file paths across one debounce window. A
    path can be marked changed then deleted (or vice versa) before the timer
    fires -- each mark clears the path from the other set, so it only ever
    ends up in the one that reflects its latest known state."""

    def __init__(self):
        self.changed: set[str] = set()
        self.deleted: set[str] = set()

    def mark_changed(self, path: str) -> None:
        self.deleted.discard(path)
        self.changed.add(path)

    def mark_deleted(self, path: str) -> None:
        self.changed.discard(path)
        self.deleted.add(path)

    def take(self) -> tuple[set[str], set[str]]:
        changed, deleted = self.changed, self.deleted
        self.changed, self.deleted = set(), set()
        return changed, deleted


class _RepoChangeHandler(FileSystemEventHandler):
    def __init__(self, pending: _PendingChanges, on_change, debounce_seconds: float):
        self._pending = pending
        self._on_change = on_change
        self._debounce_seconds = debounce_seconds
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._on_change)
            self._timer.daemon = True
            self._timer.start()

    def on_created(self, event) -> None:
        if _is_relevant_change(event.src_path):
            self._pending.mark_changed(event.src_path)
            self._schedule()

    def on_modified(self, event) -> None:
        if _is_relevant_change(event.src_path):
            self._pending.mark_changed(event.src_path)
            self._schedule()

    def on_deleted(self, event) -> None:
        if _is_relevant_change(event.src_path):
            self._pending.mark_deleted(event.src_path)
            self._schedule()

    def on_moved(self, event) -> None:
        scheduled = False
        if _is_relevant_change(event.src_path):
            self._pending.mark_deleted(event.src_path)
            scheduled = True
        if _is_relevant_change(event.dest_path):
            self._pending.mark_changed(event.dest_path)
            scheduled = True
        if scheduled:
            self._schedule()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class RepoWatcherManager:
    def __init__(
        self, graph_store: GraphStore, debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        qdrant_client=None, embedder=None, qdrant_client_resolver=None,
    ):
        self._graph_store = graph_store
        self._debounce_seconds = debounce_seconds
        self._qdrant_client = qdrant_client
        self._embedder = embedder
        # DEPLOY_MODE=local wires this to open a per-repo embedded Qdrant client keyed by
        # local_path (see get_repo_qdrant_client), instead of every repo sharing one
        # collection that grows unbounded. Takes precedence over qdrant_client when set.
        self._qdrant_client_resolver = qdrant_client_resolver
        self._observer: Observer | None = None
        self._watches: dict[tuple[str, str], tuple[object, _RepoChangeHandler]] = {}
        # One lock per (user_id, repo_id) so concurrent reindex triggers for the
        # same repo (e.g. a second debounced fire landing while a slow embed
        # call from the first is still running) serialize instead of racing on
        # the same Qdrant/graph-store writes.
        self._reindex_locks: dict[tuple[str, str], threading.Lock] = {}

    def _ensure_observer(self) -> Observer:
        # Started lazily on first watch() rather than in __init__ so a manager
        # that's constructed but never used to watch a repo (e.g. built up
        # front, watched conditionally) doesn't leak an OS thread.
        if self._observer is None:
            self._observer = Observer()
            self._observer.start()
        return self._observer

    def _resolve_qdrant_client(self, local_path: str):
        return self._qdrant_client_resolver(local_path) if self._qdrant_client_resolver else self._qdrant_client

    def reindex(self, user_id: str, repo_id: str, local_path: str) -> None:
        key = (user_id, repo_id)
        lock = self._reindex_locks.setdefault(key, threading.Lock())
        with lock:
            symbols, edges = parse_repo(repo_id, local_path)
            self._graph_store.replace_repo_graph(
                {
                    "user_id": user_id, "repo_id": repo_id, "source": local_path,
                    "local_path": local_path, "last_indexed_at": datetime.datetime.utcnow().isoformat(),
                },
                [
                    {"id": s.id, "repo_id": repo_id, "user_id": user_id, "kind": s.kind, "name": s.name,
                     "file_path": s.file_path, "start_line": s.start_line, "end_line": s.end_line,
                     "language": s.language, "content_hash": s.content_hash}
                    for s in symbols
                ],
                [{"source": e.source, "target": e.target, "type": e.type} for e in edges],
            )
            client = self._resolve_qdrant_client(local_path)
            if client is not None and self._embedder is not None:
                self._replace_symbol_embeddings(client, user_id, repo_id, symbols)

    def _replace_symbol_embeddings(self, client, user_id: str, repo_id: str, symbols) -> None:
        # Mirrors replace_repo_graph's atomic-replace semantics: drop this
        # repo's previous vectors before writing the new set, or every full
        # reindex would pile up a fresh duplicate copy on top of the last one
        # forever.
        client.delete(
            collection_name=CODE_SYMBOL_EMBEDDINGS,
            points_selector=Filter(
                must=[
                    FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                    FieldCondition(key="repo_id", match=MatchValue(value=repo_id)),
                ]
            ),
        )
        if not symbols:
            return
        vectors = self._embedder.embed_batch([f"{s.kind} {s.name}" for s in symbols])
        client.upsert(
            collection_name=CODE_SYMBOL_EMBEDDINGS,
            points=[
                PointStruct(
                    id=symbol_point_id(s.id), vector=vector,
                    payload={
                        "symbol_id": s.id, "user_id": user_id, "repo_id": repo_id,
                        "name": s.name, "kind": s.kind, "file_path": s.file_path,
                    },
                )
                for s, vector in zip(symbols, vectors)
            ],
            wait=True,
        )

    def reindex_paths(
        self, user_id: str, repo_id: str, local_path: str, changed_paths: set[str], deleted_paths: set[str],
    ) -> None:
        key = (user_id, repo_id)
        lock = self._reindex_locks.setdefault(key, threading.Lock())
        with lock:
            # A path can be stale by the time the debounce timer fires (e.g.
            # created then deleted within one window) -- if it's not on disk
            # anymore, treat it as deleted rather than trying to parse it.
            existing_paths = {p for p in changed_paths if os.path.isfile(p)}
            deleted_paths = deleted_paths | (changed_paths - existing_paths)

            new_symbols, resolved_defines, pending_calls, pending_imports, pending_inherits = parse_files(
                repo_id, list(existing_paths)
            )

            baseline_nodes, _ = self._graph_store.get_subgraph(user_id, repo_id)
            # get_subgraph also returns mentioning TextEntity nodes, which have no
            # file_path -- only CodeSymbol-shaped nodes matter for the index/diff below.
            code_baseline_nodes = [n for n in baseline_nodes if "file_path" in n]
            stale_file_paths = existing_paths | deleted_paths
            kept_baseline = [n for n in code_baseline_nodes if n["file_path"] not in stale_file_paths]
            stale_baseline_by_id = {n["id"]: n for n in code_baseline_nodes if n["file_path"] in stale_file_paths}

            index_symbols = kept_baseline + [
                {"id": s.id, "name": s.name, "kind": s.kind, "file_path": s.file_path} for s in new_symbols
            ]
            new_edges = resolve_edges(
                index_symbols, resolved_defines, pending_calls, pending_imports, pending_inherits
            )

            self._graph_store.replace_files_in_repo(
                {
                    "user_id": user_id, "repo_id": repo_id, "source": local_path,
                    "local_path": local_path, "last_indexed_at": datetime.datetime.utcnow().isoformat(),
                },
                list(stale_file_paths),
                [
                    {"id": s.id, "repo_id": repo_id, "user_id": user_id, "kind": s.kind, "name": s.name,
                     "file_path": s.file_path, "start_line": s.start_line, "end_line": s.end_line,
                     "language": s.language, "content_hash": s.content_hash}
                    for s in new_symbols
                ],
                [{"source": e.source, "target": e.target, "type": e.type} for e in new_edges],
            )

            client = self._resolve_qdrant_client(local_path)
            if client is not None and self._embedder is not None:
                self._update_symbol_embeddings(client, user_id, repo_id, new_symbols, stale_baseline_by_id)

    def _update_symbol_embeddings(self, client, user_id: str, repo_id: str, new_symbols, stale_baseline_by_id) -> None:
        new_ids = {s.id for s in new_symbols}
        to_embed = [
            s for s in new_symbols
            if s.id not in stale_baseline_by_id or stale_baseline_by_id[s.id].get("content_hash") != s.content_hash
        ]
        removed_ids = [sid for sid in stale_baseline_by_id if sid not in new_ids]

        if removed_ids:
            client.delete(
                collection_name=CODE_SYMBOL_EMBEDDINGS,
                points_selector=PointIdsList(points=[symbol_point_id(sid) for sid in removed_ids]),
            )
        if not to_embed:
            return
        vectors = self._embedder.embed_batch([f"{s.kind} {s.name}" for s in to_embed])
        client.upsert(
            collection_name=CODE_SYMBOL_EMBEDDINGS,
            points=[
                PointStruct(
                    id=symbol_point_id(s.id), vector=vector,
                    payload={
                        "symbol_id": s.id, "user_id": user_id, "repo_id": repo_id,
                        "name": s.name, "kind": s.kind, "file_path": s.file_path,
                    },
                )
                for s, vector in zip(to_embed, vectors)
            ],
            wait=True,
        )

    def watch(self, user_id: str, repo_id: str, local_path: str) -> None:
        key = (user_id, repo_id)
        if key in self._watches:
            return
        pending = _PendingChanges()

        def on_fire():
            changed, deleted = pending.take()
            self.reindex_paths(user_id, repo_id, local_path, changed, deleted)

        handler = _RepoChangeHandler(pending, on_fire, self._debounce_seconds)
        watch = self._ensure_observer().schedule(handler, local_path, recursive=True)
        self._watches[key] = (watch, handler)

    def unwatch(self, user_id: str, repo_id: str) -> None:
        entry = self._watches.pop((user_id, repo_id), None)
        if entry is not None:
            watch, handler = entry
            handler.cancel()  # drop any debounce timer already scheduled, or it'd still fire after unwatch
            if self._observer is not None:
                self._observer.unschedule(watch)

    def watched_repos(self) -> set[tuple[str, str]]:
        return set(self._watches.keys())

    def resume_all(self) -> None:
        for repo in self._graph_store.list_repos():
            if os.path.isdir(repo["local_path"]):
                self.watch(repo["user_id"], repo["repo_id"], repo["local_path"])

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
