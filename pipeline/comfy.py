"""comfy.py — drive a node's ComfyUI from ceres over SSH, stdlib only.

ComfyUI's port is loopback-bound on the nodes and the public front door wants a client cert, so
scripts reach it the same way clusterctl reaches everything else: an SSH tunnel with the cluster key.

    with Comfy("urania") as c:
        name = c.upload(Path("ref.png"), subfolder="cat")
        pid = c.submit(graph)                 # API-format graph (dict of node id -> node)
        for img in c.wait(pid): c.fetch(img, dest)

to_ui() turns the same API graph into a litegraph workflow for the Workflows menu, so what a
script runs and what Colby opens by hand are one graph.
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def inventory():
    env = {}
    for line in (REPO / "cluster/inventory.env").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env


def publish(files, subdir):
    """Copy local files into urania's studio output/<subdir>/, so they show up in the /outputs/
    gallery (studio profile, same client cert as ComfyUI). Returns the gallery URL. Everything Colby
    reviews goes here, never to a local folder he would have to open."""
    inv = inventory()
    host, dest = inv["URANIA_MGMT"], f"/opt/clio/comfyui-docker/studio/output/{subdir}"
    ssh = ["-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none", "-i", inv["SSH_KEY"]]
    subprocess.run(["ssh", *ssh, f"{inv['SSH_USER']}@{host}", f"mkdir -p {dest}"], check=True)
    subprocess.run(["scp", "-q", *ssh, *map(str, files), f"{inv['SSH_USER']}@{host}:{dest}/"], check=True)
    return f"https://{host}/outputs/{subdir}/"


_awake = None


def keep_awake():
    """Hold off idle sleep on the Mac while this process runs. ceres sleeps after 1 minute idle,
    which froze every long run and dropped its tunnels (2026-10-18: 13-minute stalls on 20-second
    jobs). `caffeinate -w` releases itself when this process exits; the Mac's settings are untouched."""
    global _awake
    if _awake is None and shutil.which("caffeinate"):
        # -s: no system sleep while on AC power. -i alone is not enough: away from the desk the
        # Mac sits in screen-off "dark wake" maintenance windows, where idle-sleep assertions lapse.
        _awake = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])


class Comfy:
    def __init__(self, node="urania"):
        inv = inventory()
        self.host = inv[f"{node.upper()}_MGMT"]
        self.user = inv["SSH_USER"]
        self.key = inv["SSH_KEY"]
        self.remote_port = int(inv.get("COMFYUI_PORT", 8188))
        self.node = node
        self.proc = None

    # ---- tunnel ----
    def __enter__(self):
        keep_awake()
        # the free-port probe can race another process for the port; a fresh port fixes it
        for attempt in range(3):
            try:
                return self._open()
            except RuntimeError:
                if attempt == 2:
                    raise
                time.sleep(1)

    def _open(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.proc = subprocess.Popen(
            # A dedicated connection, never ~/.ssh/config's shared ControlMaster: through a mux
            # master, ssh hands the forward over and exits 0, and every tunnel then dies with
            # that master (both nodes' runs reset at once on 2026-10-18).
            ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
             "-o", "ControlMaster=no", "-o", "ControlPath=none", "-i", self.key,
             "-L", f"{self.port}:127.0.0.1:{self.remote_port}", f"{self.user}@{self.host}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().decode().strip() or "no message"
                raise RuntimeError(f"ssh tunnel to {self.host} exited {self.proc.returncode}: {err}")
            try:
                self.get("/system_stats")
                return self
            except OSError:
                time.sleep(0.1)
        # Close this attempt's tunnel before the caller retries, or it outlives the script.
        self.proc.terminate()
        raise RuntimeError(f"ComfyUI on {self.node} not answering (is it in studio mode?)")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()

    # ---- HTTP ----
    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path, raw=False):
        with urllib.request.urlopen(self._url(path), timeout=30) as r:
            data = r.read()
        return data if raw else json.loads(data)

    def post(self, path, body):
        req = urllib.request.Request(self._url(path), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"POST {path} -> {e.code}: {e.read().decode()[:2000]}") from None

    def object_info(self):
        if not hasattr(self, "_oi"):
            self._oi = self.get("/object_info")
        return self._oi

    def choices(self, node_type, input_name):
        spec = self.object_info()[node_type]["input"]
        spec = {**spec.get("required", {}), **spec.get("optional", {})}[input_name]
        return spec[1]["options"] if spec[0] == "COMBO" else spec[0]

    # ---- jobs ----
    def upload(self, path: Path, subfolder="", name=None):
        """Upload into input/<subfolder>/; returns the name LoadImage wants."""
        name = name or path.name
        boundary = uuid.uuid4().hex
        parts = []
        for field, value in (("subfolder", subfolder), ("overwrite", "true"), ("type", "input")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
                     f'Content-Type: image/png\r\n\r\n'.encode() + path.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(self._url("/upload/image"), data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
        return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]

    def submit(self, graph):
        return self.post("/prompt", {"prompt": graph, "client_id": "clio-pipeline"})["prompt_id"]

    def wait(self, prompt_id, poll=2.0, timeout=3600):
        """Block until the job finishes; return its output files [{filename, subfolder, type}]."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                h = self.get(f"/history/{prompt_id}")
            except OSError:
                # The job runs on the node regardless; only our tunnel dropped (seen 2026-10-18:
                # both nodes' runs reset at once while ComfyUI stayed up). Reconnect and keep waiting.
                time.sleep(poll)
                if self.proc and self.proc.poll() is None:
                    self.proc.terminate()
                self.__enter__()
                continue
            if prompt_id in h:
                entry = h[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    raise RuntimeError(f"job {prompt_id} failed: {json.dumps(msgs)[:2000]}")
                # images, and any other saved file (SaveGLB reports under "3d")
                return [f for out in entry.get("outputs", {}).values() for files in out.values()
                        if isinstance(files, list) for f in files if isinstance(f, dict) and "filename" in f]
            time.sleep(poll)
        raise TimeoutError(prompt_id)

    def fetch(self, img, dest: Path):
        q = urllib.parse.urlencode({k: img[k] for k in ("filename", "subfolder", "type")})
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get(f"/view?{q}", raw=True))
        return dest


# ---- API graph -> litegraph workflow (for the Workflows menu) ----

SEED_INPUTS = {"seed", "noise_seed"}


def _is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def to_ui(graph, object_info, titles=None):
    """Emit a litegraph workflow from an API graph. Input order and widget-vs-socket come from
    object_info: a spec whose type is a list or "COMBO" or a primitive is a widget unless linked."""
    titles = titles or {}
    ids = {nid: i + 1 for i, nid in enumerate(graph)}
    links, nodes = [], []
    out_links = {}  # (node, slot) -> [link ids]

    # column = longest path from a source, so the layout reads left to right
    depth = {}
    def d(nid):
        if nid not in depth:
            ups = [d(v[0]) for v in graph[nid]["inputs"].values() if _is_link(v)]
            depth[nid] = 1 + max(ups, default=-1)
        return depth[nid]
    for nid in graph:
        d(nid)

    placed = {}
    for nid, node in graph.items():
        spec = object_info[node["class_type"]]
        inspec = {**spec["input"].get("required", {}), **spec["input"].get("optional", {})}
        inputs, widgets = [], []
        for name, s in inspec.items():
            v = node["inputs"].get(name)
            typ = s[0]
            is_widget_type = isinstance(typ, list) or typ in ("COMBO", "INT", "FLOAT", "STRING", "BOOLEAN")
            if _is_link(v):
                lid = len(links) + 1
                src, slot = v
                otype = object_info[graph[src]["class_type"]]["output"][slot]
                links.append([lid, ids[src], slot, ids[nid], len(inputs), otype])
                out_links.setdefault((src, slot), []).append(lid)
                entry = {"name": name, "type": otype, "link": lid}
                if is_widget_type:
                    entry["widget"] = {"name": name}
                inputs.append(entry)
            elif is_widget_type:
                widgets.append(v if v is not None else (s[1].get("default") if len(s) > 1 else None))
                if name in SEED_INPUTS:
                    widgets.append("fixed")
                if node["class_type"] == "LoadImage" and name == "image":
                    widgets.append("image")
            else:
                inputs.append({"name": name, "type": typ, "link": None})
        col = depth[nid]
        row = placed.get(col, 0)
        placed[col] = row + 1
        nodes.append({
            "id": ids[nid], "type": node["class_type"], "pos": [60 + col * 380, 60 + row * 260],
            "size": [340, 200], "flags": {}, "order": 0, "mode": 0,
            "title": titles.get(nid, node.get("_meta", {}).get("title", node["class_type"])),
            "inputs": inputs, "properties": {"Node name for S&R": node["class_type"]},
            "widgets_values": widgets,
        })
    # outputs need every link, so they are filled once all inputs are wired
    by_id = {ids[nid]: nid for nid in graph}
    for n in nodes:
        nid = by_id[n["id"]]
        spec = object_info[n["type"]]
        n["outputs"] = [{"name": nm, "type": t, "slot_index": i, "links": out_links.get((nid, i), [])}
                        for i, (nm, t) in enumerate(zip(spec["output_name"], spec["output"]))]
    order = sorted(graph, key=lambda k: depth[k])
    for i, nid in enumerate(order):
        next(n for n in nodes if n["id"] == ids[nid])["order"] = i
    return {"last_node_id": len(nodes), "last_link_id": len(links), "nodes": nodes, "links": links,
            "groups": [], "config": {}, "extra": {}, "version": 0.4}
