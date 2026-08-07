# Access control: HTTPS + mutual TLS

ComfyUI is reachable only at **https://clio.cgk.sh/** and **https://urania.cgk.sh/**, and only from
a device holding a client certificate signed by the cluster CA. Port 8188 is bound to loopback and
is not reachable from anywhere else, including the other node. Last updated 2026-08-07.

## Why this shape

ComfyUI has **no authentication of its own**, and custom nodes execute arbitrary Python. An exposed
instance is a remote-code-execution box — the Apr-2026 botnet swept the internet for exactly this.
So the design is defence in depth, in two independent layers:

1. **ComfyUI does not listen on the network at all.** Docker publishes it as `127.0.0.1:8188:8188`,
   so even with nginx stopped or misconfigured there is nothing on the LAN to reach. Verified: from
   ceres *and* from the other node, `http://<node>:8188` refuses.
2. **nginx terminates TLS and demands a client certificate** (`ssl_verify_client on`) before it will
   proxy a single byte. No certificate → HTTP 400 at the TLS layer, request never sees ComfyUI.

The server block is `default_server` on both 80 and 443, so hitting the box by bare IP lands in the
same mutual-TLS gate — there is no unauthenticated side entrance.

## Private CA, not Let's Encrypt

Deliberate. Let's Encrypt would need DNS-01 (the nodes are RFC1918, so HTTP-01 cannot reach them),
which means certbot plus DNSimple API credentials on each node — on boxes whose `apt` is wedged —
plus a 90-day renewal treadmill for an internal-only service. And mutual TLS requires a CA we
control *regardless*, to sign and validate client certs. One private CA does both jobs and nothing
expires unexpectedly.

Trade-off accepted: browsers show a warning until you trust the CA once per device. That is a
one-time step, and it is the same step that makes the client certificate meaningful.

| | Location | Notes |
|---|---|---|
| CA key + cert | `~/.clio/ca/` on **ceres** | mode 0700; **never** copied to a node |
| Server certs | `~/.clio/ca/certs/<fqdn>.crt` → node `/etc/clio-pki/server.crt` | 825 days, SANs cover FQDN + short name + LAN IP + 127.0.0.1 |
| CA cert on nodes | `/etc/clio-pki/ca.crt` | only used to *verify clients* |
| Client bundles | `~/.clio/ca/clients/<name>.p12` | password in `<name>.p12.password` |

The CA private key never leaves ceres. A node holding it would let an attacker who owns the node
mint client certificates for the other one.

## Enrolling a device

```bash
clio-pki client colby-macbook        # prints the bundle path and its password
```

**macOS** — three steps, and the third is the one people miss:
```bash
# 1. import the bundle — double-click the .p12 and enter the printed password
# 2. trust the CA
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.clio/ca/ca.crt
# 3. bind the identity to the sites, or the browser will not offer it
security set-identity-preference -s "https://clio.cgk.sh"   -c "<name>"
security set-identity-preference -s "https://urania.cgk.sh" -c "<name>"
```
Then **fully quit and reopen the browser** — Chrome and Safari cache the "no certificate" outcome
per origin, so a reload alone can keep showing the denied page after you have fixed it.

Steps 1 and 2 alone are not enough. Without an identity preference macOS may simply never present
the certificate, and the symptom is indistinguishable from having no certificate at all.

### If the browser says ERR_ADDRESS_UNREACHABLE

That is **not** a certificate problem — the request never reached the node, so nothing appears in
its access log. Seen 2026-08-07: Chrome had auto-updated 150 → 151 **on disk while the old process
kept running**, and the stale instance could not reach the LAN at all, while `curl` from a terminal
worked fine the whole time.

The fix is a *genuine* full quit. Chrome keeps background processes alive, so closing the windows
is not enough — Cmd-Q, then confirm:

```bash
pgrep -fl "Google Chrome"    # must print nothing before you relaunch
```

Two things that look like the cause and are not: a dozen enabled "Google Chrome" entries under
System Settings → Privacy & Security → **Local Network** is normal (one per updated binary), and
`chrome://net-internals/#dns` showing the right address only proves resolution, not reachability.
The quickest discriminator is to open the UniFi UI or any other LAN address — if that fails too,
the browser has no LAN access and the cluster is not involved.

*Do not be alarmed by this:* `security find-identity -v -p ssl-client` reports **0 valid
identities** even when everything works, and `security verify-cert -c <client>.crt -p ssl -C` says
`Invalid Extended Key Usage for policy` on a certificate whose EKU is exactly `clientAuth`. Both are
quirks of the `security` CLI. The authoritative checks are `security find-identity -v` (which does
list the identity) and the server's own access log.

**iOS/iPadOS** — AirDrop or mail both `ca.crt` and the `.p12` to the device, install each under
Settings → Profile Downloaded, then **Settings → General → About → Certificate Trust Settings** and
enable full trust for "cgk.sh Internal CA". (That last step is easy to miss and the only reason it
usually doesn't work first try.)

**curl / scripts**
```bash
curl --cacert ~/.clio/ca/ca.crt \
     --cert ~/.clio/ca/certs/ceres.crt --key ~/.clio/ca/private/ceres.key \
     https://clio.cgk.sh/system_stats
```

## Error pages

nginx serves pages styled to match https://cgk.sh (dark `#161B21` header band, Baloo Bhaina 2
headings, `#0000a0` links) instead of its stock white walls. Sources: `cluster/nginx/errors/`.

| nginx code | Served as | Page |
|---|---|---|
| 496 (no client cert), 495 (bad client cert) | **400** | "You are not allowed." — points at this file and `clio-pki client <name>` |
| 502 / 503 / 504 | as-is | "ComfyUI is not answering." — names the likely cause (node is in `llm` mode) and the `clusterctl mode` fix |
| 400 403 404 405 408 413 500 | as-is | generic |
| 497 (plain HTTP on the TLS port) | 301 | redirect to `https://<fqdn>` |

Two details that are load-bearing rather than cosmetic:
- **The CSS is inlined into each page.** A linked stylesheet would be a second request needing its
  own client certificate — so the "you are not allowed" page would render unstyled, precisely when
  it is the only thing a visitor can see.
- **HTML comments are stripped at install time.** The sources explain which nginx codes map where;
  that detail should not be handed to an unauthenticated caller.

`=400` on the `error_page` directive keeps the status identical to nginx's default, so nothing that
scripts against these hosts sees a behaviour change — only the body text differs.

## Operating it

**Diagnosing a denied page.** The access log on the node says which half is wrong — this is the
first thing to look at, not the last:
```bash
ssh clio.cgk.sh 'sudo tail /var/log/nginx/comfyui.access.log'
#   verify=NONE      -> the browser offered no certificate (enrolment/identity-preference problem)
#   verify=FAILED:.. -> it offered one this host will not accept (wrong CA, expired)
#   verify=SUCCESS   -> mutual TLS is fine; any error is beyond the gate
```
nginx only reports the distinction in its *error* log at `info` level, which is far too noisy to
leave enabled, so the access log carries `$ssl_client_verify` instead.

```bash
clio-pki list                     # everything issued, and when it expires
clio-pki client <name>            # enroll a new device
clio-pki server clio              # re-issue a node cert (e.g. renewal)
clusterctl nginx deploy [node]    # push certs + config, reload nginx
clusterctl nginx status           # nginx state, cert expiry, 8188 binding
```

Renewal is `clio-pki server <node> && clusterctl nginx deploy <node>` — the certs are 825 days, so
set a reminder rather than trusting memory.

## Revocation — read before you need it

There is deliberately **no CRL**. An nginx `ssl_crl` must be regenerated and redeployed to take
effect, and an *expired* CRL rejects every client at once — a well-known way to lock yourself out of
your own cluster. With a handful of devices the honest answer is:

- Device retired but not hostile → `clio-pki revoke <name>` (drops local material) and move on.
- **Device lost or compromised** → the certificate on it still works. You must rotate: move
  `~/.clio/ca` aside, `clio-pki init`, re-issue both server certs and every client cert,
  `clusterctl nginx deploy`, and re-trust the new CA on each remaining device.

Keep the client list short so that rotation stays cheap.

## What is *not* covered

- **SSH** is unchanged — key-based, and a separate path onto the boxes. This work does not harden it.
- **The LiteLLM gateway on :4000 is still plain HTTP on the LAN**, with only its master key in
  front. The nginx config is structured to take more services; putting the gateway behind the same
  mutual-TLS door is the obvious next step, but it would mean updating `~/.clio/claude-code.env` and
  every client that talks to it.
- **ComfyUI-Distributed across the two nodes would now fail** — each node's 8188 is loopback-only.
  Re-enabling it needs a deliberate exception (e.g. publish on the fabric IP, not `0.0.0.0`).
- The nodes trust each other over the fabric (NFS export, `no_root_squash`). Owning one node gets
  you the other's model store.

## Verifying the posture

```bash
for h in clio urania; do
  curl -s -m 6 -o /dev/null -w "$h 8188 (want 000): %{http_code}\n" http://$h.cgk.sh:8188/system_stats
  curl -s -m 8 --cacert ~/.clio/ca/ca.crt -o /dev/null -w "$h no-cert (want 400): %{http_code}\n" https://$h.cgk.sh/system_stats
  curl -s -m 8 --cacert ~/.clio/ca/ca.crt --cert ~/.clio/ca/certs/ceres.crt --key ~/.clio/ca/private/ceres.key \
       -o /dev/null -w "$h with-cert (want 200): %{http_code}\n" https://$h.cgk.sh/system_stats
done
```
