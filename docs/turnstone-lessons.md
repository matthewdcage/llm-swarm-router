# Lessons from Turnstone: assumptions and practical gains

Source: [turnstonelabs/turnstone](https://github.com/turnstonelabs/turnstone)
(README, `docs/judge.md`, `docs/console.md`, `run.sh`, as of the versions
fetched 2026-07-22). Turnstone is an agent-tool harness, not a model router —
see [turnstone-integration.md](turnstone-integration.md) for how the two
projects actually compose. This doc is narrower: for each of the three ideas
flagged as "worth learning from," what's actually confirmed vs assumed, and
whether adopting it is worth the change given netllm's own constraints
(**non-breaking**: no behavior change for existing users without opt-in;
**non-drifting**: doesn't fork netllm's routing model into two incompatible
mental models).

## 1. `turnstone-doctor` — richer doctor/status output

**What I could actually verify:** the README lists `turnstone-doctor` as
"LLM-backed cluster diagnostics" in its component table. That's it — there is
no `docs/doctor.md` or `docs/operations/doctor.md` in the repo; I looked and
it isn't there. `docs/judge.md` and `docs/console.md` describe *related*
systems (the tool-call risk judge, and the cluster dashboard's REST API) but
neither documents what `turnstone-doctor` actually inspects or how it forms a
diagnosis.

**Assumption flag:** I do not know whether `turnstone-doctor` runs an LLM
over structured diagnostic data (e.g. "here are 40 nodes' health JSON, what's
wrong") or whether it's LLM-*assisted* triage layered on deterministic checks
like netllm's. The name and one-line description are the only evidence.
Anything past that is inference, not observation — flag it as such if it
comes up again.

**What netllm's doctor actually does today**
(`packages/netllm-cli/src/netllm_cli/main.py:1444`): a fixed sequence of
deterministic checks — config file exists, LAN-open-without-token, gateway
not advertising, mDNS/zeroconf missing, global CLI off PATH, no local
providers online, Anthropic backend configured without an API key, LAN
listen without a resolvable LAN IP, port conflicts (including menubar
supervision awareness). Each check appends a plain `(problem, fix)` tuple.
It's exhaustive for known failure shapes and instant (no network round-trip,
no model call).

**Practical gain, qualified:** an LLM-graded diagnostic step is a plausible
next tier for the failure modes the current rule table *doesn't* enumerate —
e.g. "backend responds 200 but returns garbage completions," "swarm peers
see each other but routing never picks the LAN peer," things a single
`(problem, fix)` rule can't easily encode because the diagnosis needs
correlating several signals (logs + health + config) rather than checking
one condition. That's a real gap; the current doctor can only catch what
someone already anticipated as a rule.

**Non-breaking / non-drifting path:** do **not** replace the deterministic
tier — it's free, synchronous, and someone might run `doctor --json` in a
script expecting deterministic output. If this is pursued, it should be:
- opt-in (`netllm doctor --deep` or similar), off by default
- additive: deterministic issues list stays untouched; an LLM tier appends
  advisory notes in a separate section, mirroring how Turnstone's own judge
  is explicitly "advisory" (`docs/judge.md`: "the user always makes the final
  decision")
- routed through the existing agent (`OPENAI_BASE_URL`-style self-call), not
  a new provider integration, since netllm already has model routing

**Verdict:** worth a scoped follow-up, but only after someone reads
Turnstone's actual `turnstone-doctor` source (not just the README line) to
confirm what it does — right now this is "an interesting name" more than "a
studied design," and building against a one-line description risks solving
the wrong problem.

## 2. Rendezvous (HRW) hashing for routing

**What I could actually verify:** `docs/console.md`'s architecture section
and the README both describe the console→node routing scheme in prose:
"[Console] picks the target node for each workstream via rendezvous (HRW)
hashing over the live service registry — pure function of `(ws_id,
live_nodes)`, no stored bucket state, deterministic across readers. A node
join or drop only re-routes the keys that score highest on the affected
node." I did not read Turnstone's Python source for the actual HRW
implementation — this claim comes from documentation prose, not code
inspection. Standard HRW (highest random weight) hashing has exactly this
property by construction, so the claim is credible, but it's still
unverified against Turnstone's own code.

**What netllm actually does today** (`packages/netllm-core/src/netllm_core/pool.py`):
- `round_robin` and `least_load`-tiebreak use `self._round_robin_idx`, an
  **in-process mutable counter** — not stateless, and not shared across
  gateway restarts or between multiple gateway processes if you ever ran more
  than one.
- `batch_shard` uses `shard_index()` → `_stable_shard_index()`
  (`pool.py:606-618`), which hashes the shard key with SHA-256 and takes
  **`hash % len(candidates)`** — plain modulo hashing, not rendezvous
  hashing.
- `local_spillover` (the swarm default) doesn't hash at all — it's a live
  in-flight-count comparison between the best local backend and the best
  remote peer, re-evaluated every request. This one is already effectively
  stateless and doesn't have the problem HRW solves.

**The actual gap, precisely stated:** modulo hashing (`hash % N`) is what
breaks under membership change, not the `local_spillover`/`round_robin`
strategies in general. When `N` (candidate count) changes — a peer joins,
drops, or a backend flips healthy/unhealthy — `hash % N` remaps *most* keys
to a different index, even ones that had nothing to do with the change. HRW
only remaps the keys whose highest-scoring node was the one that
joined/left. This matters specifically for `batch_shard`, which exists to
give the *same* shard key a *stable* backend across requests (that's the
point of sharding); today, every peer health flap potentially reshuffles
every shard's assignment.

**Practical gain, qualified:** real, but narrow. It only matters if
`batch_shard` is used in an environment where backend membership changes
somewhat often (peers joining/leaving a LAN swarm, backends flapping
healthy/unhealthy) *and* shard stability across those changes is actually
relied on. If `batch_shard` is mostly used in stable, single-session
contexts (one gateway, backends rarely changing membership), the current
modulo hash is simpler and the churn this would fix rarely happens in
practice. This needs a usage check before treating it as high-value: does
anything currently depend on `batch_shard` stability surviving a peer
join/drop? If not, this is a latent-bug fix with low urgency, not a
user-visible improvement.

**Non-breaking / non-drifting path:** HRW is a drop-in replacement for
`_stable_shard_index`'s hash-to-index mapping — same function signature
(`shard_key`, `candidates` → chosen backend), same call site
(`pool.py:580-582`), no config surface or strategy-name change needed. It is
*not* a good fit for `round_robin`/`least_load`'s `_round_robin_idx` — those
are deliberately not key-stable (round-robin's whole point is to cycle), so
swapping them for HRW would change their semantics, not just their
statelessness. Scope any change to `batch_shard` only:
1. Replace `_stable_shard_index`'s modulo step with an HRW scoring loop
   (`max(candidates, key=lambda b: hrw_score(shard_key, b.id))`) — a few
   lines, no new dependency (HRW needs only a hash function, already
   imported via `hashlib`).
2. Add a unit test asserting the "only affected keys move" property directly
   (assign N keys, remove one candidate, assert `<= keys/N` reassignments) —
   this is the actual behavior being bought, so it should be the thing
   tested, not just "shard_index returns an int."
3. Leave `round_robin`/`least_load`/`local_spillover` untouched.

**Verdict:** legitimate, scoped, low-risk improvement to `batch_shard`
specifically — but confirm real usage depends on shard stability across
membership changes before spending the cycle; otherwise it's correctness
polish, not a felt gain.

## 3. One-line installer pattern (`curl | bash` distro autodetection)

**What I could actually verify:** read `run.sh` directly (not just
described) — ~150 of its lines fetched. Confirmed behavior: detects
distro/package-manager family from `/etc/os-release` `ID`/`ID_LIKE` (falls
back to checking which package manager binary exists), detects WSL via
`/proc/version` or `$WSL_DISTRO_NAME`, explicitly explains *why* it doesn't
just delegate to `get.docker.com` (that upstream script keys off `$ID` alone
and rejects derivatives like Mint/Pop!_OS/Nobara/AlmaLinux even though
`ID_LIKE` makes the family obvious — so Turnstone adds its own Docker CE repo
step per family), traps unhandled errors with an actionable message instead
of a bare stack trace, and is safe to re-run (updates the checkout, keeps an
existing `.env`).

**Comparison, honestly stated:** netllm's install path
(`docs/linux-install.md`, `docs/windows-install.md`) is fundamentally
different in kind, not just polish — netllm installs a **Python package +
CLI** via `uv`/pip/deb/rpm, not a Docker Compose stack. Turnstone's script
solves "get Docker running correctly across five distro families," which
isn't netllm's problem: netllm has no Docker Compose deployment mode
described in `AGENTS.md`, so most of `run.sh`'s substance (Docker CE repo
fallback, compose plugin check, port allocation for Caddy/Postgres) has no
netllm analogue to apply it to.

**What *does* transfer, scoped to what netllm's installers actually need:**
- **The distro-family fallback pattern itself** — checking `ID_LIKE` and
  falling back to "which package manager binary exists" rather than trusting
  `ID` alone — is directly applicable if `docs/linux-install.md`'s install
  path also branches on distro ID today. Worth checking whether it has the
  same blind spot Turnstone explicitly worked around (rejecting
  derivatives).
- **`trap ... ERR` with an actionable, re-runnable failure message** and
  **idempotent re-run** (updates instead of erroring "already exists") are
  general shell-script hygiene, applicable to any of netllm's install
  scripts regardless of what they install.
- **Everything Docker-specific does not transfer** — there's no
  Docker Compose stack in netllm to autodetect ports or generate secrets
  for.

**Non-breaking / non-drifting path:** this is documentation/script hygiene,
not a design decision — there's no risk of "drift" here since it doesn't
touch routing or config semantics. If pursued: audit
`docs/linux-install.md`'s actual install script (not this doc) for whether
it already handles `ID_LIKE` derivatives and idempotent re-runs; if it does,
there's nothing to take from this comparison beyond confirmation. If it
doesn't, borrow the `ID_LIKE`-fallback + `trap ERR` + re-run-safe patterns
directly — they're self-contained shell idioms, not architecture.

**Verdict:** lowest-effort, lowest-risk item of the three, but also the
narrowest in scope — most of what makes `run.sh` interesting is solving a
problem (multi-family Docker installs) netllm doesn't have.

## Summary

| Idea | Verified how | Real netllm gap? | Effort to adopt | Risk |
|---|---|---|---|---|
| LLM-tier doctor | README line only, no source read | Yes — correlated/ambiguous failures the rule table can't encode | Medium (new opt-in tier, self-call through existing routing) | Low if strictly additive/opt-in |
| HRW for `batch_shard` | Doc prose, not Turnstone's source | Yes, but narrow — only if shard stability across membership churn is actually relied on | Small (one function swap + a property test) | Low, scope-contained to `batch_shard` |
| Installer autodetection | Read `run.sh` directly | Mostly no — solves a Docker-stack problem netllm doesn't have; only the shell idioms transfer | Small, if `docs/linux-install.md`'s script lacks the idioms | None — doc/script only |

None of these require touching netllm's config schema, routing strategy
names, or public API surface. The HRW change is the only one with a concrete
code target (`pool.py:615`); the other two are "read the source first, then
decide" rather than "implement now."
