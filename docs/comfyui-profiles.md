# ComfyUI profiles: studio and private

*Set up 2026-10-18.* ComfyUI on each node runs as one of two **profiles**: the same container and
the same model files, but different directories and a different front door. The goal is
**separation, not secrecy from yourself**: nothing personal should be able to show up while working
on, or showing someone, the animation project.

| | **studio** (default) | **private** |
|---|---|---|
| For | the cat animation project, anything shareable | personal work |
| URL | `https://<node>.cgk.sh/` | `https://<node>.cgk.sh:8443/` |
| Client cert | any cluster cert | **only `private-*` certs** (others get 403) |
| Loopback port | `127.0.0.1:8188` (what `pipeline/` tunnels to) | `127.0.0.1:8189` |
| input / output / user | `comfyui-docker/studio/…` | `comfyui-docker/basedir/…` (the original layout) |
| Models | curated: `cluster/comfyui/studio-models.txt` | the full store |
| Assets index | `studio/user/comfyui.db` | `basedir/user/comfyui.db` |

Only one profile runs at a time per node: same unified-memory reason as llm vs comfy. The door for
the profile that is not running answers with the "ComfyUI is not answering" page.

## How to switch

Run these on ceres. Switching takes about a minute; the job queue is not carried over, so let
running jobs finish first.

```bash
# What is each node running right now? (prints studio, private, llm or off)
clusterctl mode urania
clusterctl mode clio

# Private work on urania  ->  open https://urania.cgk.sh:8443/
clusterctl mode urania private

# Back to the animation work  ->  open https://urania.cgk.sh/
clusterctl mode urania studio

# Same for clio (this also stops the GPT-OSS model server if it was running)
clusterctl mode clio private
clusterctl mode clio studio

# Free the node entirely
clusterctl mode urania off
```

The profile sticks: after a reboot ComfyUI stays off, and the next `clusterctl comfyui up` starts
each node in whatever profile it was last switched to. Before showing anyone your screen, run `clusterctl mode <node>` to check
that both nodes say `studio`.

Other everyday commands:

```bash
clusterctl comfyui sync          # after editing cluster/comfyui/studio-models.txt
clusterctl logs comfyui urania   # if a page says ComfyUI is not answering
```

## Every place a file can surface, and how each is closed

| Surface | Studio sees |
|---|---|
| `/outputs/` gallery | `studio/output/` only. nginx on :443 never points at `basedir/output` |
| Load Image dropdowns | `studio/input/` only (NFS-shared from urania, like `basedir/input`) |
| Workflows menu | `studio/user/default/workflows/` only |
| Model pickers | the curated symlink tree only. The full store is mounted read-only at `/store/models` so the links resolve, but ComfyUI never scans it |
| Assets index | its own database (`--database-url`). The default sits inside the shared `run/ComfyUI/` checkout, which would have mixed both |
| Browser tabs/history | :443 and :8443 are different origins, so ComfyUI's saved open tabs and per-site storage never cross |

Still shared, and fine to be: `custom_nodes/`, the Python venv (`run/`), `temp/`.

## Adding a model to the studio

Add its path (globs allowed, relative to `basedir/models/`) to `cluster/comfyui/studio-models.txt`,
then `clusterctl comfyui sync`. The script rebuilds the tree from scratch, so deleting a line removes
the model, and it warns about any line that matches nothing. Download new models into the shared
store as before (`basedir/models/…`), not through the studio's Manager: a Manager download there
lands as a real file in `studio/models/` on that node only.

## Private certificate

`clio-pki client private-<device>` issues one (ceres has `private-ceres`). Import the `.p12`, then
pin it to the :8443 door only, so the browser keeps offering the everyday cert to the studio:

```bash
security set-identity-preference -s "https://clio.cgk.sh:8443/"   -c private-ceres
security set-identity-preference -s "https://urania.cgk.sh:8443/" -c private-ceres
```

The gate is `map $ssl_client_s_dn $clio_private_client` in `conf.d/10-websocket-upgrade.conf`,
checked inside each :8443 location (a server-level `return` would also catch the internal
redirect to the error page and loop).

## Files

- `cluster/comfyui/{studio,private}.yaml`: compose overrides layered on the upstream file
- `cluster/comfyui/studio-models.txt`: the studio's model allow-list
- `scripts/bootstrap/32-comfyui-profiles.sh`: studio dirs, model tree, `clio-comfyui.service`
- `/opt/clio/comfyui-profile.env` on each node: the selected profile (not synced from the repo)
- `cluster/nginx/comfyui.conf.tmpl` + `comfyui-{common,proxy}.conf`: the two doors

## Migration record (2026-10-18)

Moved into the studio: `output/cat/` (urania 81 files, clio 44), `input/cat/` and `input/3d/`, and the
`cat_model_sheet` workflow on both nodes. Each node's old assets index (`run/ComfyUI/user/comfyui.db`)
moved to `basedir/user/` and became the private profile's. Everything else stayed in place as the
private profile. Nothing was deleted.
