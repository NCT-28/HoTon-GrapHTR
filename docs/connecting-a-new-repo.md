# Connecting a new repo to a running graphtr server

For the case where a hoton-graphtr server is **already deployed** (Docker
compose, `docker-graphtr/`) and you want a *different* repo to use it as an
MCP client — not spin up its own server. See the main [README](../README.md#using-graphtr-in-another-project)
for the self-hosting (`install.sh --run`) case instead.

## 1. Register the consumer repo as an MCP client

From this repo (`HoTon-GrapHTR`), once per consumer repo:

```bash
python3 scripts/init_graphtr_skills.py /path/to/other-project
```

Copies the `graphtr` and `graphtr-knowledge` skills into the target project,
rewriting `graphtr`'s script paths to invoke this repo's
`scripts/query.py`/`build_viewer.py` directly (the target doesn't get its own
checkout), and bundling `graphtr-knowledge`'s scripts under the target's own
skill dir.

Then, from the consumer repo:

```bash
cd /path/to/other-project
claude mcp add --transport http hoton-graphtr <server-url>/mcp -s local
```

`<server-url>` is wherever the Docker-deployed server is reachable from the
consumer repo — `http://localhost:8030` only works if consumer and server
share a host; otherwise use the server's actual host/IP and make sure the
port is reachable (firewall, security group, etc).

## 2. Filesystem note — the part that actually breaks

`ingest_codebase(source=...)` resolves `source` on the **server process's**
filesystem (`app/mcp_server.py::ingest_codebase_impl` → `resolve_repo_source`),
not the machine the Claude session runs on. Registering the MCP connection
does not make the consumer repo visible to the server.

For a Docker deploy, the server container only sees paths under its
`code-repos` bind mount (`docker-hoton-graphtr-1` container,
`docker-graphtr/docker-compose.yml`):

```bash
rsync -a --exclude .git --exclude node_modules --exclude target \
  /path/to/other-project/ <host-code-repos-dir>/other-project/
```

(`<host-code-repos-dir>` is `${CODE_REPOS_DIR_HOST:-./code-repos}` from
`docker-graphtr/docker-compose.yml`, on the machine running the compose
stack.) Then pass the in-container path as `source`:

```
/data/code-repos/other-project
```

Git URLs are not supported — `ingest_codebase` rejects `http(s)://` sources
outright (`mcp_server.py`); there's no upload-over-MCP path either. If the
server and consumer repo happen to be on the same host (just not started via
`install.sh --run` from inside it), skip the rsync and pass the repo's real
path as-is.

## 3. First use

In a Claude session on the consumer repo, the `graphtr` skill's Bootstrap
step:

```
ingest_codebase(source="/data/code-repos/other-project")
```

writes `graphtr-out/` into the *server-visible* copy of the repo (the synced
copy under `code-repos/`, not the consumer's working tree). Query results
(`graphtr-out/graph.json`, `graphtr.html`) live there too — pull them back
if you want them in the working tree.

**Refresh** after code changes: rsync again, then re-run the same
`ingest_codebase` call — it reparses from scratch and overwrites
`graphtr-out/`. No watcher, no incremental reindex; every call mints a fresh
`repo_id`, which is expected.

## Common mistakes

- Passing the consumer's host path (`/Users/...`) instead of the
  server-visible `/data/code-repos/...` path — the server can't see it.
- Forgetting to re-rsync before re-running `ingest_codebase` after local
  edits — the server still parses the stale synced copy.
- Passing a git URL — not supported; sync the repo in and pass its
  server-visible path instead.
