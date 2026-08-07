# Shared ComfyUI model store (NFS over RDMA)

One copy of the ComfyUI models, visible on both Sparks. urania owns the store; clio mounts it across
the 200G fabric. Drop a checkpoint on either node and it is immediately usable from both — no
copying, no drift, no double disk. Set up by `scripts/bootstrap/35-comfyui-share.sh`.
Last updated 2026-08-07.

## What is shared and what is not

| Path | Shared? | Why |
|---|---|---|
| `basedir/models/` | **yes** — NFS from urania | the whole point; ~90 GB of checkpoints/LoRAs/GGUFs |
| `basedir/input/` | **yes** — NFS from urania | upload a still on one node, drive it from the other |
| `basedir/output/` | no, node-local | each node writes its own results; sharing invites clobbering |
| `basedir/user/` (workflows) | no, node-local | small JSON; sync with `scripts/sync-comfyui-from-urania.sh` |
| `basedir/custom_nodes/` | no, node-local | **deliberate** — each node has its own venv, and nodes need per-node `pip install` of their requirements. Sharing the tree without sharing the venv gives you import failures. |

## Transport: why RDMA

Measured on clio, 4 GiB `dd iflag=direct` of a Wan checkpoint, urania's page cache warm:

| Transport | Throughput |
|---|---|
| NFSv4.2 / **RDMA** (port 20049) | **1.5 – 1.8 GB/s** |
| NFSv4.2 / TCP, `nconnect=16` | ~970 MB/s |
| NFSv4.2 / TCP, single connection | ~320 MB/s |

RDMA is ~1.8× the best TCP config, so it is the default. The ConnectX-7 rails already run RoCE for
NCCL; `rpcrdma` rides the same hardware. (This is *not* GPUDirect — GB10 has no GPUDirect RDMA, by
design. Data lands in host memory, which is where ComfyUI wants it anyway on a unified-memory box.)

If RDMA ever fails to come up, TCP is a drop-in fallback — swap the fstab options to
`proto=tcp,nconnect=16` and drop `port=20049`. Everything else stays the same.

## How it is wired

**urania (server)**
- `/etc/exports` → `basedir/{models,input}` exported to **`10.51.50.13` only** (clio's fabric IP).
  Never the LAN. `rw,sync,no_subtree_check,no_root_squash`.
- `/etc/nfs.conf.d/10-rdma.conf` → `rdma=y`, `rdma-port=20049`. This matters: `/proc/fs/nfsd/portlist`
  is **not** durable, so without this the RDMA listener vanishes on restart and clio's mount fails.
- `/etc/modules-load.d/rpcrdma.conf` loads the transport at boot.

**clio (client)**
- Two `/etc/fstab` entries, `vers=4.2,proto=rdma,port=20049,rsize=1048576,wsize=1048576,hard,_netdev,nofail`.
- `nofail` so a down urania never blocks clio's boot.
- `clio-comfyui.service` carries `RequiresMountsFor=…/basedir/models …/basedir/input`, so ComfyUI
  waits for the mounts instead of starting against empty directories. **This is load-bearing:**
  Docker bind-mounts `basedir` recursively at container start, so a mount that appears *later* is
  invisible inside the running container. Mount first, then start.

Both nodes run the container as uid 1000 (`colby`), so ownership lines up across the mount with no
id-mapping games.

## Installing `nfs-kernel-server` on these nodes

`apt-get install nfs-kernel-server` **will fail**. apt also tries to configure the 8 half-installed
DOCA/OFED DKMS packages, which cannot build against kernel 6.17, and aborts the whole transaction.
All of nfs-kernel-server's dependencies are already satisfied, so install the `.deb` directly:

```bash
cd /tmp && apt-get download nfs-kernel-server && sudo dpkg -i nfs-kernel-server_*.deb
```

That touches nothing else. (The broken DKMS state is pre-existing and cosmetic — see
[state.md](state.md) open items.)

## Consequences to know about

- **clio's ComfyUI now depends on urania being up.** If urania is off, the mounts fail and
  `clio-comfyui` will not start. That is the intended trade for a single copy — but it means the two
  nodes are no longer independent for image/video work.
- **A generation on clio reads model weights over the fabric.** At 1.5–1.8 GB/s a 10 GB Wan
  checkpoint costs ~6 s on a cold cache, once — after that it is in clio's page cache. Negligible
  next to a multi-minute video render.
- **Both nodes generating at once** contend for urania's NVMe and its page cache. Fine in practice;
  the models are read once per load, not streamed.
- Adding a model from clio writes across the mount to urania. That works (`no_root_squash`), and is
  the reason the export is `rw`.

## Checks

```bash
ssh urania.cgk.sh 'sudo exportfs -v; sudo cat /proc/fs/nfsd/portlist'   # server: exports + rdma line
ssh clio.cgk.sh   'mount | grep basedir'                               # client: proto=rdma
ssh clio.cgk.sh   'ls /opt/clio/comfyui-docker/basedir/models/checkpoints'
# throughput spot-check (direct I/O bypasses the client cache):
ssh clio.cgk.sh   'sudo dd if=/opt/clio/comfyui-docker/basedir/models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors \
                     of=/dev/null bs=1M count=4096 iflag=direct'
```
