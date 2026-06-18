# Using the local models (day-to-day)

Everything points at **one endpoint** — the gateway on clio — which speaks **both** the
Anthropic API and the OpenAI API. You never talk to the Sparks directly.

## The endpoint + models
- **Anthropic API:** `http://clio:4000`            (for Claude Code → `ANTHROPIC_BASE_URL`)
- **OpenAI API:** `http://clio:4000/v1`             (for VS Code / aider / scripts)
- **Auth:** the gateway key (`LITELLM_MASTER_KEY` in `/opt/clio/gateway/gateway.env`; also written
  into `~/.clio/claude-code.env` by `clusterctl use`).
- **Model names:**
  - `local-coder` — Qwen3-Coder-30B (fast daily coder; on urania)
  - `local-big` — GPT-OSS-120B (heavier reasoning; on clio)
  - `claude-opus-4-8` / `claude-sonnet-4-6` — real Anthropic API (only if you put an API key in
    `gateway.env`; otherwise use your Max plan directly — see Claude Code below)

> **Reaching it:** use `http://10.10.10.149:4000` if the bare name `clio` doesn't resolve (VPN/mDNS),
> or `http://clio.cgk.sh:4000` once the DNS NS-flip is live. Same port either way.

## Claude Code — WITHOUT breaking your main session
Your normal `claude` talks to the real Anthropic API (your Max plan) — keep it that way. To use the
**local** models, run a *separate* invocation with the env set inline (add to `~/.zshrc`):
```bash
clio-claude() {
  ANTHROPIC_BASE_URL="http://10.10.10.149:4000" \
  ANTHROPIC_AUTH_TOKEN="<GATEWAY_KEY>" \
  ANTHROPIC_MODEL="local-coder" \
  ANTHROPIC_SMALL_FAST_MODEL="local-coder" \
  claude "$@"
}
```
Now: `claude` = real Claude (this session, unchanged); `clio-claude` = your Sparks. Swap
`local-coder`→`local-big` for the bigger model. (`clusterctl use <model>` writes the same env into
`~/.clio/claude-code.env` if you'd rather `source` it in a dedicated shell.)

**Reality check:** a 30B local model driving Claude Code's *agentic* tool-use is hit-or-miss vs. real
Claude — great for quick edits, explanations, and cheap iteration; for hard multi-step/agentic work,
just use your normal `claude`. That's the intended "spillover."

## VS Code
**Continue.dev** (`~/.continue/config.yaml`) — the cleanest fit (OpenAI-compatible):
```yaml
models:
  - name: clio-coder
    provider: openai
    model: local-coder
    apiBase: http://10.10.10.149:4000/v1
    apiKey: <GATEWAY_KEY>
  - name: clio-big
    provider: openai
    model: local-big
    apiBase: http://10.10.10.149:4000/v1
    apiKey: <GATEWAY_KEY>
```
**Cline / Roo Code** (agentic): pick provider **"OpenAI Compatible"**, Base URL
`http://10.10.10.149:4000/v1`, API key = gateway key, Model ID `local-coder` (or `local-big`).
(GitHub Copilot can't be pointed at a custom endpoint.)

## aider / generic OpenAI / curl
```bash
aider --model openai/local-coder \
      --openai-api-base http://10.10.10.149:4000/v1 --openai-api-key <GATEWAY_KEY>

curl http://10.10.10.149:4000/v1/chat/completions \
  -H "Authorization: Bearer <GATEWAY_KEY>" -H "content-type: application/json" \
  -d '{"model":"local-coder","messages":[{"role":"user","content":"hello"}]}'
```

## Switching what's served vs. switching what you ask for
- **What each Spark serves** = `clusterctl serve <profile>` / the split (`urania=coder`, `clio=big`).
  You rarely touch this.
- **Which model your client asks for** = just change the `model` field (`local-coder` ↔ `local-big`)
  or the Continue dropdown. The endpoint never changes.

## Expectations
- `local-coder`: ~30–50 tok/s, good for everyday coding; weaker than real Claude at long agentic chains.
- `local-big` (GPT-OSS-120B): stronger reasoning, somewhat slower; best for "think harder" one-shots.
- Both are bandwidth-bound on the Sparks — fine interactively, not RTX-fast. Spill to real Claude
  (your `claude`) for the heavy lifts.
