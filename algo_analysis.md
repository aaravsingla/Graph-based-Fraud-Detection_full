# Synapse — Algorithm Selection via Synthetic Fraud Graph

**Date:** 2026-06-30
**Status:** In progress — synthetic data generator complete, Neptune loading pipeline being debugged
**Location:** `batchjobs/csv_export/synthetic/` (generator), `batchjobs/data/synthetic/` (output)

---

## 1. What Synapse Is

Synapse is a fraud-detection graph pipeline for TBO (a B2B travel booking platform). It ETLs data from three sources — SQL Server (agencies, members, bookings, ledgers), DynamoDB (Customer360 operational data), and OpenSearch (auth events, devices, IPs, step-up events) — into Amazon Neptune as a property graph.

The graph has **19 vertex labels** (Member, Agency, Device, IpAddress, AuthEvent, StepUpEvent, Hotel, HotelItinerary, Ledger, Address, etc.) and **27 edge labels** (BELONGS_TO, LOGGED_IN, ACCESSED_FROM, RELATED_TO, CREATED_BOOKING, HAD_AUTH_EVENT, TRIGGERED_RULE, etc.).

Schema docs: `docs/diagrams/02-graph-schema.md` and `docs/diagrams/03-full-graph-schema.md`.

---

## 2. The Goal: Find the Best Algorithm Pipeline for Fraud Detection

TBO faces 10 specific fraud types. We need to determine which graph algorithms (and in what combination/sequence) detect them most effectively. The three algorithms originally planned were Louvain, PageRank, and Degree Centrality (via Neptune Analytics). Research showed these are suboptimal — the recommended stack is:

### Recommended algorithm pipeline (to be validated)

```
Pass 1 — WCC (Weakly Connected Components)
    Find the outer boundary of every connected fraud cluster.
    Coarse first pass: "who is reachable from whom via behavioral edges?"

Pass 2 — Louvain (Community Detection)
    Within suspicious WCC components, find internal community structure.
    Identifies tight subgroups within larger clusters.

Pass 3 — Node Similarity (Jaccard)
    On Agency nodes — find agencies that share Device/IP neighborhoods
    WITHOUT any explicit RELATED_TO edge between them.
    Catches hidden collusion where no relationship was declared.

Pass 4 — Betweenness Centrality
    Within flagged communities, find the broker/linchpin members.
    These are structurally between clusters — ringleaders, shared coordinators.

Pass 5 — Degree Centrality
    On Device and IpAddress vertices.
    Flags super-shared proxy infrastructure (devices/IPs used by many members).
```

Additionally, Personalized PageRank (PPR) would be run as a Gremlin traversal in the portal drill-down (not batch), and GNN (RGCN/GraphSAGE via Neptune ML + GraphStorm) is the Phase 3/4 longer-term upgrade.

### Why not just run standard Louvain + PageRank + Degree

- **Louvain alone** on the full heterogeneous graph rediscovers the org chart (dense `BELONGS_TO`/`OWNS_LEDGER` edges dominate modularity). Must run on a behavioral-edge-only projection.
- **Standard PageRank** gives a global rank with no investigative anchor — an analyst can't act on "this device has rank 0.0034." Betweenness finds the brokers between communities (actionable). PPR anchored to a specific entity is better for drill-down.
- **Degree** is useful but trivially computable — it's kept but shouldn't be the primary tool.
- **WCC** is missing from the original plan but is the most reliable first-pass for ring detection.
- **Node Similarity** catches the specific TBO pattern of agencies hiding collusion by not declaring relationships.

---

## 3. Why Synthetic Data Is Necessary

### The data problem

The real Neptune graph has a rich entity backbone (~99k vertices: 13.5k members, 12k agencies, 40k addresses, 15k ledgers) but an **almost-empty behavioral layer** (~850 login/device/IP edges, ~100 step-up events). This is because the OpenSearch login-history export only captured a small sample.

Without behavioral edges, graph algorithms have nothing to work with — Louvain, WCC, Degree all operate on edges. And critically, there are **no labeled fraud cases** in the real data, so there is no way to measure precision/recall of any algorithm.

### What synthetic data provides

1. **Dense behavioral structure** — enough edges (~1M) for algorithms to produce meaningful, differentiable results.
2. **Labeled ground truth** — planted fraud rings with known members, agencies, devices, and IPs, at three difficulty levels (easy/medium/hard).
3. **Legitimate decoys** — false-positive bait (office NAT, VPN travelers, head-office chains) so pipelines can be evaluated on precision, not just recall.
4. **A scoring oracle** — `ground_truth.json` maps each planted ring to its expected detection algorithm, enabling automated F1 scoring.

### Methodological constraint

Synthetic data validates that the **pipeline mechanically recovers known patterns** (Job A). It does NOT by itself prove the pipeline works on real TBO fraud (Job B). Final algorithm selection and thresholds must be confirmed against real labeled fraud cases once available. Treat synthetic results as "the machinery works" and real labels as the final judge.

---

## 4. The Synthetic Generator Architecture

Located at `batchjobs/csv_export/synthetic/` — deliberately separate from the export jobs.

### 4-layer generation model

```
Layer 0 — Real entities (frozen)
    Load real sql-members, sql-agencies, sql-ledgers from final/ exports.
    These are the legitimate substrate. Never regenerated.

Layer 1 — Legitimate behavioral baseline (legitimate.py)
    ~9000 active members get normal device/IP/login/booking/step-up activity.
    All scoped within their own agency (no cross-agency sharing).
    Realistic failed-login rate, human auth latency, normal booking volume.

Layer 2 — Legitimate decoys (decoys.py)
    False-positive bait:
    - office_nat: many members, ONE agency, behind a single IP
    - vpn_traveler: multi-country logins days apart via VPN
    - head_office_chain: clean acyclic A→B→C hierarchy
    - busy_legit_agent: high-volume but established agent

Layer 3a — Structural fraud (fraud_structural.py) — Group A
    Tests graph algorithms (WCC/Louvain/Degree/Jaccard/Betweenness):
    #1  shared_device_unrelated — agencies sharing devices with no relationship
    #10 proxy_ip_cluster — datacenter/VPN IPs with threat intel, cross-agency
    #7  collusion_ring_circular — circular RELATED_TO A→B→C→A
    #4  credential_stuffing — one IP/device, many failed logins, then a success
    #9  ghost_agency — new agency, single member, near-limit booking

Layer 3b — Temporal/property fraud (fraud_temporal.py) — Group B
    Tests Gremlin pattern queries:
    #2  impossible_travel — same member, distant countries within minutes
    #3  stepup_reverse_engineering — repeated forgot-password + probing
    #5  bot_scripted_auth — sub-1.5s auth latency sequences
    #6  account_takeover_downgrade — TOTP→SMS→MAGIC_LINK on new device
    #8  ledger_manipulation — rapid UPDATED_LEDGER cycles on real ledger
```

### Key files

| File | Role |
|------|------|
| `config.py` | Presets (`dev`/`full`), counts, ID ranges |
| `schema.py` | Neptune CSV headers, ID helpers, row builders |
| `entities.py` | Loads real member/agency/ledger backbone |
| `world.py` | Accumulator: IDs, buckets, dedup, ground truth |
| `legitimate.py` | Layer 1 |
| `decoys.py` | Layer 2 |
| `fraud_structural.py` | Layer 3a |
| `fraud_temporal.py` | Layer 3b |
| `writer.py` | Flush → CSVs + ground_truth.json |
| `generate.py` | CLI orchestrator |
| `upload.py` | S3 uploader with freshness guarantees |
| `load.py` | Batched Neptune loader with per-file progress |

### Running

```bash
cd batchjobs/csv_export
python -m synthetic.generate --preset full      # ~262k vertices, ~1.1M edges, 67 rings, 26 decoys
python -m synthetic.upload --purge-previous      # upload to S3, delete old runs
# Then load into Neptune (see Section 6)
```

---

## 5. Problems Encountered and Their Fixes

### Problem 1 — CRLF line endings (FIXED)

**Symptom:** 170,938 insertErrors on the first load attempt, 0 parseErrors.
**Root cause:** `common/csv_writer.py` opened files with `newline="\n"` instead of `newline=""` + `lineterminator="\n"`. Python's csv module still wrote `\r\n`. Neptune treated `\r` as part of the last field value, failing typed-column parsing (`"false\r"` is not a valid Bool).
**Fix:** Changed `_write_single()` to use `open(path, "w", newline="")` + `csv.writer(f, lineterminator="\n")`.

### Problem 2 — SYN-SYN- double prefix on stepup IDs (FIXED)

**Symptom:** `stepupevent:SYN-SYN-<uuid>` — edges couldn't find their target vertices.
**Root cause:** `world.new_session()` returns `SYN-<uuid>`, and callers in `legitimate.py` and `fraud_temporal.py` prepended another `SYN-` via `f"SYN-{world.new_session()}"`.
**Fix:** Removed the manual `SYN-` prefix in both files.

### Problem 3 — Synthetic edges anchor to real backbone vertices that don't exist (DIAGNOSED)

**Symptom:** When synthetic data was loaded into an empty Neptune DB, ~100% of fraud/booking edges failed with FROM_OR_TO_VERTEX_ARE_MISSING.
**Root cause:** Synthetic edges reference `member:<real_id>` and `agency:<real_id>` vertices that the synthetic layer never writes itself — they come from the real backbone `final/` export.
**Fix:** Load real backbone FIRST (Phase A), then synthetic layer on top (Phase B). Two sequential `neptune_load.py` invocations.

### Problem 4 — Stale orphan files from dev run mixed with full run (FIXED)

**Symptom:** Neptune loaded both old single-file CSVs and new chunked part-NNN files.
**Root cause:** The `dev` preset wrote single files; the `full` preset wrote chunked files; old single files weren't cleaned up.
**Fix:** `writer.py` now purges all `syn-*.csv` before writing. `upload.py` has an orphan guard that aborts if both exist.

### Problem 5 — Multiple concurrent load processes (FIXED procedurally)

**Symptom:** 7 background processes writing to Neptune simultaneously, causing duplicates, schema conflicts, and queue poisoning.
**Root cause:** During debugging, multiple loaders were started without stopping previous ones.
**Fix:** Procedural — always check `list_processes` and the Neptune load queue before submitting. Never run parallel loads.

### Problem 6 — `neptune_load.py` poll timeout (FIXED)

**Symptom:** `ReadTimeoutError` on status poll while Neptune was under bulk-load pressure.
**Root cause:** `timeout=30` too short. Fixed to `timeout=120`.

### Problem 7 — Neptune property type conflicts (NEEDS FIX — see Section 6)

**Symptom:** 333,486 insertErrors on synthetic vertex load, specifically on `syn-auth_events` and `syn-stepup_events`.
**Root cause:** Neptune enforces property types GLOBALLY across all vertex labels. The real backbone registered `memberId:Long` and `agencyId:Long` (from Member/Agency vertices), but synthetic `AuthEvent` vertices declare `memberId:String` and `agencyId:String` — conflict.
**4 conflicts found:**

| Property | Real type (wins) | Synthetic type (rejected) | File to fix |
|---|---|---|---|
| `memberId` | Long | String | `schema.py` AUTH_EVENT_HEADER |
| `agencyId` | Long | String | `schema.py` AUTH_EVENT_HEADER |
| `isPrimaryAgent` | Bool | String | `schema.py` STEPUP_EVENT_HEADER |
| `creditLimit` | Double | String | `schema.py` GHOST_BALANCE_HEADER |

### Problem 8 — UPDATED_LEDGER edges pointing to fabricated ledger IDs (FIXED)

**Symptom:** 35 edges with `~to = ledger:SYNLED-15585-0` — no such vertex exists.
**Root cause:** The generator made up ledger IDs instead of using real ones.
**Fix:** `entities.py` now loads real ledger IDs from `sql-ledgers-*.csv`. `fraud_temporal.py` picks a random real ledger for each manipulation ring.

### Problem 9 — `opensearch-edges-auth.csv` and `opensearch-edges-auth-v2.csv` in S3 (CANNOT FIX — no delete permission)

**Symptom:** 3,967 insertErrors every time the real backbone's edge phase runs.
**Root cause:** These files contain edges referencing `stepupevent:SYN-*` vertices from an old generator run and KMS `authevent:AYABe*` IDs that don't exist. A clean replacement (`opensearch-edges-auth-fixed.csv`) was uploaded but the broken originals can't be deleted (IAM doesn't have `s3:DeleteObject`).
**Impact:** Harmless — the 3,967 errors are always the same known rows. The synthetic layer provides its own complete auth edges. Neptune's `failOnError=FALSE` means all other edges load fine.

---

## 6. Current State and Remaining Steps

### What's done
- [x] Synthetic generator built and tested (`synthetic/` package)
- [x] Full dataset generated locally (262k vertices, 1.1M edges, 67 fraud rings, 26 decoys)
- [x] Data uploaded to S3 (`s3://dev-login-graph-data/neptune-csv-export/synthetic/20260629T112913Z/`)
- [x] Real backbone loaded into Neptune (720k vertices, ~109k edges, 0 vertex errors)
- [x] CRLF fix, SYN-SYN fix, ledger-id fix all applied
- [x] `ground_truth.json` scoring oracle generated

### What needs to be done NOW (before next load attempt)

**Fix the 4 property type conflicts in `synthetic/schema.py`:**

```python
# AUTH_EVENT_HEADER: change these two
"memberId:String"  →  "memberId:Long"
"agencyId:String"  →  "agencyId:Long"

# STEPUP_EVENT_HEADER: change this one
"isPrimaryAgent:String"  →  "isPrimaryAgent:Bool"

# GHOST_BALANCE_HEADER: change this one
"creditLimit:String"  →  "creditLimit:Double"
```

Then:
1. Regenerate: `python -m synthetic.generate --preset full`
2. Re-upload: `python -m synthetic.upload --purge-previous`
3. Wipe Neptune: `g.V().drop().iterate()` in Neptune workbench
4. Load backbone: `neptune_load.py --source s3://.../20260624T140021Z/final`
5. Load synthetic: `neptune_load.py --source s3://.../synthetic/<new-run-id>`

### What needs to be done AFTER successful load

1. **Build the scoring harness** — a script that:
   - Runs each candidate algorithm pipeline on the loaded graph (via Neptune Analytics or Gremlin)
   - Compares flagged entities against `ground_truth.json`
   - Computes precision/recall/F1 per fraud type AND per difficulty level
   - Reports which pipeline degrades most gracefully on `hard` rings while staying clean on decoys

2. **Algorithm experiments to run:**
   - WCC → Louvain → Betweenness → Degree (full pipeline)
   - WCC → Louvain → Degree (without Betweenness — is it needed?)
   - Louvain alone (baseline)
   - WCC alone (baseline)
   - Node Similarity on agencies (standalone — does it catch hidden collusion?)
   - Vary Louvain edge subsets: behavioral-only vs all edges

3. **Threshold tuning:**
   - Degree threshold on Device: at what inDegree do you flag? The decoys (office NAT) tell you the lower bound.
   - Louvain community size: is a community of 2 agencies suspicious or just normal?
   - WCC component size: filter by multi-agency components only?

4. **Document findings** and present to domain experts for validation before proceeding to real-data confirmation.

---

## 7. Infrastructure Reference

| Component | Location |
|---|---|
| Neptune cluster | `neptune-dev-cluster` (eu-west-1) |
| Neptune endpoint | `https://neptune-dev-cluster.cluster-ck1qxluvekbm.eu-west-1.neptune.amazonaws.com:8182` |
| Neptune IAM role | `arn:aws:iam::696292622193:role/TTL-custom-rdstos3Policy` |
| S3 bucket | `dev-login-graph-data` |
| Real backbone S3 | `s3://dev-login-graph-data/neptune-csv-export/20260624T140021Z/final/` |
| Synthetic data S3 | `s3://dev-login-graph-data/neptune-csv-export/synthetic/20260629T112913Z/` |
| `.env` | `batchjobs/csv_export/.env` — temporary STS creds that expire every few hours |
| Generator | `batchjobs/csv_export/synthetic/` |
| Loader | `batchjobs/csv_export/neptune_load.py` (two-phase: vertices → edges) |
| Batched loader | `batchjobs/csv_export/synthetic/load.py` (per-file progress, resume support) |
| Ground truth | `batchjobs/data/synthetic/ground_truth.json` |

### Key operational notes

- `neptune_load.py` does NOT auto-load `.env`. Either `set -a; source .env; set +a` before running, or pass `--endpoint`, `--iam-role`, `--region` explicitly.
- The `.env:11: command not found: Driver` warning is harmless (zsh interprets `SQL_DRIVER=ODBC Driver 18...` as a command due to spaces).
- Neptune's bulk loader runs server-side (S3 → Neptune via IAM role). Local machine RAM is irrelevant.
- Always check `neptune loader queue` and `background processes` before submitting loads. Never run concurrent loads.
- The 3,967 real-backbone edge errors are permanent (can't delete broken S3 files) and harmless (synthetic layer supersedes them).

---

## 8. Graph Schema (Condensed)

### Vertex labels: 19
Core: Member, Agency, Address, Ledger
Fraud signal: Device, IpAddress, HotelItinerary, Hotel
Auth/event: AuthEvent, StepUpEvent, StepUpRule
Customer360: AgencyBalance, SalesHead, SalesCoordinator, EmailPreference, Role, Permission, Persona, Promo

### Edge labels: 27
Core: BELONGS_TO, RELATED_TO, OWNS_LEDGER, CREATED_LEDGER, UPDATED_LEDGER, REGISTERED_AT, HEADQUARTERED_AT
Fraud: LOGGED_IN, ACCESSED_FROM, CREATED_BOOKING, BOOKING_FOR, BOOKED_WITH_DEVICE, BOOKED_FROM_IP, BOOKED_AT_HOTEL
Auth: HAD_AUTH_EVENT, AUTH_USED_DEVICE, AUTH_FROM_IP, HAD_STEP_UP, STEP_UP_USED_DEVICE, STEP_UP_FROM_IP, TRIGGERED_RULE
Customer360: HAS_BALANCE, MANAGED_BY_SALESHEAD, MANAGED_BY_COORDINATOR, HAS_EMAIL_PREF, HAS_ROLE, ROLE_PERMITS, CONSUMED_PROMO, IS_PRIMARY_AGENT_FOR, etc.

### Key structural patterns for fraud detection
- `Member → LOGGED_IN → Device ← LOGGED_IN ← Member` (shared device = fraud bridge)
- `Member → ACCESSED_FROM → IpAddress ← ACCESSED_FROM ← Member` (shared IP)
- `Agency → RELATED_TO → Agency → RELATED_TO → Agency → RELATED_TO → Agency` (cycle = collusion ring)
- `Member → HAD_STEP_UP → StepUpEvent → TRIGGERED_RULE → StepUpRule` (rule gaming)

---

## 9. The Fraud Types (TBO-Specific)

| # | Fraud type | Detection method | Algorithm |
|---|---|---|---|
| 1 | Unrelated agencies sharing devices/IPs | Graph structure | WCC, Louvain, Jaccard, Degree |
| 2 | Impossible travel (country switching) | Time-ordered path | Gremlin query |
| 3 | Step-up reverse engineering | Repeated probe pattern | Gremlin query |
| 4 | Credential stuffing / brute force | High-degree failed-auth IP | Degree + filter |
| 5 | Bot / scripted auth | Auth latency < 1.5s | Property filter + Degree |
| 6 | Account takeover via MFA downgrade | Challenge progression | Gremlin path query |
| 7 | Agency collusion ring (circular ownership) | RELATED_TO cycles | Louvain + cycle detection |
| 8 | Ledger manipulation | Rapid balance cycling | Property filter + PageRank |
| 9 | Ghost agency (synthetic identity) | New agency, near-limit booking | Louvain isolation + anomaly |
| 10 | Proxy/VPN IP clustering | Threat-flagged IPs, cross-agency | Degree + threat-intel filter |

---

## 10. Expected Outcome

After the load succeeds and the scoring harness runs, we expect to produce:

1. **A ranked pipeline recommendation** — which algorithm combination gives the best F1 across all fraud types and difficulty levels.
2. **Threshold settings** — concrete numbers (e.g., "flag Device with inDegree > 5 from 2+ agencies") validated against decoys.
3. **A clear separation** between Group A (structural fraud → batch algorithms) and Group B (temporal fraud → Gremlin queries in the portal).
4. **Documentation** of which fraud types each algorithm covers and which are pure query patterns.
5. **Input for the next phase** — real labeled data requirements to confirm the synthetic findings on production data.
