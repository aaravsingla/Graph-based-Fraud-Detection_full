# Algorithm Experiment Runs — Synapse Fraud Detection

## Algorithms Tested

### Structural (Graph) Algorithms

**Degree Centrality (`algo_degree.py`)** — Counts how many members from different agencies share the same device or IP. A device used by 6 members across 4 agencies is a red flag; a device used by 6 members in one office is not. The multi-agency filter is the precision lever. Directly targets shared_device_unrelated and proxy_ip_cluster fraud types.

**Node Similarity (`algo_node_similarity.py`)** — Computes Jaccard similarity between agency infrastructure neighborhoods. Two agencies sharing most of their devices/IPs without a declared RELATED_TO relationship suggests hidden coordination. Targets credential_stuffing and proxy_ip_cluster at the agency level.

**WCC (`algo_wcc.py`)** — Finds connected components in the behavioral graph (members linked through shared devices and IPs). Flags components spanning multiple agencies. A broad-net detector that catches structural fraud but at low precision.

**Louvain (`algo_louvain.py`)** — Community detection via modularity optimization. Attempts to find dense sub-communities within the behavioral graph. On our sparse, disconnected graph it produces identical output to WCC — the topology has no internal substructure for Louvain to exploit.

**Betweenness Centrality (`algo_betweenness.py`)** — Identifies "broker" nodes sitting on shortest paths between clusters. Designed to find ringleaders. Fails on our graph because disconnected components mean no cross-component paths exist, leaving scores near-uniform and unactionable.

### Behavioral (Property) Queries

**Bot Auth (`query_bot_auth.py`)** — Detects automated login scripts by measuring auth response latency (respondToAuthTime − initAuthTime). A human cannot respond to an auth challenge in under 1500ms across 5+ events. No graph structure needed — pure timestamp arithmetic on auth_event vertices.

**ATO Downgrade (`query_ato_downgrade.py`)** — Detects account takeover via MFA weakening. Looks for consecutive auth events where challenge strength decreases (TOTP → SMS → MAGIC_LINK) within a 30-minute window. The sequential downgrade pattern is the signature of an attacker progressively weakening security.

**Step-Up Probing (`query_stepup_probing.py`)** — Counts HAD_STEP_UP edges per member. Members who repeatedly trigger step-up challenges are probing the system to reverse-engineer its threshold rules. Three or more step-ups is statistically anomalous.

**Ledger Manipulation (`query_ledger_manipulation.py`)** — Detects rapid, high-magnitude UPDATED_LEDGER operations. Flags members with ≥3 updates within 120 minutes where total balance swing exceeds 10k. All three conditions together filter out legitimate high-frequency users.

**Impossible Travel (`query_impossible_travel.py`)** — Flags members accessing the system from two different countries within 60 minutes. Currently uses a blunt "any country change" check without geodistance, causing false positives on VPN/NAT decoys.

**Collusion Cycle Detection (`query_collusion_cycles.py`)** — Detects circular ownership structures between agencies (A→B→C→A). Legitimate corporate hierarchies (head-office, branch-office) are acyclic trees; fraud rings close the loop back to the origin. Loads the 36 RELATED_TO directed edges from `syn-edges-core.csv` and runs `nx.simple_cycles()` in O(V+E). The D-CHAIN decoys are linear chains with no back-edge, producing zero cycles by definition — precision is structural, not threshold-dependent. Targets collusion_ring_circular.

**Ghost Agency Detector (`query_ghost_agency.py`)** — Detects freshly-created shell agencies that carry a real credit line but have no genuine operating history. Pure property scan across the full agency population (12,099 real agencies + 10 planted ghosts). Filters on: `createdOn` recency (age < 90 days) + `creditLimit > 0` + empty `agencyFirstSignIn`. The D-BUSY decoys are all established agencies created 2012–2025 (>90 days old), so the recency filter cleanly excludes them. Targets ghost_agency — the single largest fraud category (10 rings).

---

## Complete Results Summary

### Individual Algorithm Performance (Best Config Per Algo)

| Algorithm         | Best Config                        | Recall | Precision | F1    | Decoy FPs |
|-------------------|------------------------------------|--------|-----------|-------|-----------|
| Bot Auth          | latency < 1500ms, ≥5 logins       | 23.5%  | 100%      | 0.381 | 1         |
| Degree            | dev≥3/ip≥3, ≥2 agencies           | 19.1%  | 90%       | 0.315 | 0         |
| Node Similarity   | Jaccard ≥ 0.2                      | 20.6%  | 45.2%     | 0.283 | 1         |
| Ghost Agency      | age<90d + creditLimit>0 + empty firstSignIn | 17.6% | 64.7% | 0.277 | 0         |
| WCC               | min 3 agencies                     | 17.6%  | 34.4%     | 0.233 | 0         |
| Louvain           | same as WCC (identical output)     | 17.6%  | 34.4%     | 0.233 | 0         |
| Step-Up Probing   | ≥3 step-ups                        | 11.8%  | 100%      | 0.211 | 0         |
| ATO Downgrade     | 30min window, 2 downgrades         | 8.8%   | 100%      | 0.162 | 0         |
| Collusion Cycles  | all cycles, any riskWeight         | 8.8%   | 100%      | 0.162 | 0         |
| Ledger            | 3 updates / 120min / 10k swing     | 8.8%   | 50%       | 0.150 | 0         |
| Impossible Travel | 60min window                       | 5.9%   | 70.6%     | 0.109 | 2         |
| Betweenness       | top 5%                             | 19.1%  | 9.4%      | 0.126 | 3         |

### Combined System Score (All Recommended Algos Unioned)

**60/68 rings caught, F1 = 0.742, Precision = 64.0%, Recall = 88.2%, 2 decoy FPs**

8 of 10 fraud types at 100% recall. Only shared_device_unrelated (87.5%) and impossible_travel (12.5%) remain below 100%. The 2 decoy FPs are D-VPN-002 and D-CHAIN-002.

---

## Recommendation: What to Keep and Why

### Definite Keeps ✅

**1. Bot Auth query** (`query_bot_auth.py`, latency < 1500ms, min 5 fast logins)

The single best performer at F1 = 0.381 with 100% precision. Catches 100% of bot_scripted_auth and 100% of credential_stuffing — two entirely separate fraud types. Zero graph structure needed, just a timestamp delta. Extremely cheap to run in production (one table scan on auth_events). The only false positive is D-CHAIN-002 which could be filtered with an established-history check. Non-negotiable keep.

**2. Degree Centrality** (`algo_degree.py`, device≥3/ip≥3, ≥2 agencies)

Best pure graph algorithm at F1 = 0.315 with 90% precision. The multi-agency filter is the key — it perfectly excludes single-agency NAT decoys (D-NAT-001 through D-NAT-008), which is exactly the precision problem the plan called out. Catches shared_device_unrelated at 88% and proxy_ip_cluster at 67%. Zero decoy FPs at the right threshold. Fast to compute (no iteration, just neighbor counting). Keep.

**3. ATO Downgrade query** (`query_ato_downgrade.py`, 30min window, 2 downgrades)

100% precision, 100% recall on account_takeover_downgrade with zero decoy FPs. Catches something no graph algorithm touches. The 6 ATO rings span easy/medium/hard difficulty and all 6 are caught. Cheap to compute — sequential scan on auth edges per member. Keep.

**4. Step-Up Probing query** (`query_stepup_probing.py`, min 3 step-ups)

100% precision, 66.7% recall on stepup_reverse_engineering, zero decoy FPs. Uniquely catches this fraud type which has zero recall from every graph algorithm. Also picks up 2 credential_stuffing and 1 ledger_manipulation rings as a bonus. Keep.

**5. Node Similarity** (`algo_node_similarity.py`, Jaccard ≥ 0.2)

The only structural algorithm that catches credential_stuffing (17%) and extends proxy_ip_cluster recall to 100%. It finds agency pairs sharing infrastructure without a declared relationship — a signal no other structural algo generates. At Jaccard ≥ 0.2 it has 45% precision (much better than WCC/Louvain at 34%) and only 1 decoy FP. It adds 2 rings that Degree misses. Keep.

**6. Collusion Cycle Detection** (`query_collusion_cycles.py`, all cycles, any riskWeight)

100% precision, 100% recall on collusion_ring_circular (6/6 rings caught), zero decoy FPs. Finds directed cycles in the 36-edge RELATED_TO agency graph using `nx.simple_cycles()`. The D-CHAIN decoys (6 linear head-office hierarchies) are structurally acyclic and produce zero cycles — precision is guaranteed by topology, not a tunable threshold. Detected cycles range from 3-node triangles (riskWeight=0.4) to 5-node rings (riskWeight=0.9) across HEAD_GSA and BRANCH_OFFICE relationship types. Trivial compute cost (36 edges). Non-negotiable keep — only detector for this fraud type.

**7. Ghost Agency Detector** (`query_ghost_agency.py`, age<90d + creditLimit>0 + empty firstSignIn)

100% recall on ghost_agency (10/10 rings caught), zero decoy FPs, 64.7% precision. Scans all 12,109 agency vertices and flags those created within 90 days that have a credit line but have never signed in. The D-BUSY decoys are all established agencies created 2012–2025 (well beyond 90 days), so the recency filter cleanly excludes them. The 14 non-ring agencies flagged are genuinely suspicious (recently created, never signed in, holding credit) — these would merit investigation in production regardless. Largest single fraud category (10 rings = 15% of all fraud). Keep.

---

### Drop ❌

**Louvain** — Produces identical output to WCC at modularity = 0.9994. The behavioral graph is too sparse and disconnected for Louvain to find substructure within components that WCC hasn't already found. Both catch exactly the same 12/16 rings at the same thresholds. Louvain adds computation time and a stochastic dependency for zero marginal gain. Drop it from the production pipeline.

**WCC as a standalone** — WCC's value is absorbed by Degree + NodeSim. WCC at minAg=3 catches 12 rings; Degree catches 11 of those 12 with much higher precision (90% vs 34%). The 1 ring WCC gets that Degree misses is a marginal case. If you're already running Degree, WCC adds overhead but minimal new signal. If Degree is your primary structural algo, WCC becomes redundant. Drop as standalone — it only makes sense as a preprocessing step if you need to scope Louvain (which you're dropping).

**Betweenness** — Worst precision of any algorithm (9.4% at top 5%), 3 decoy FPs. The scores are too uniform (all scores between 0.000001–0.000009) on this sparse graph — the signal-to-noise ratio is too low to be actionable. The ringleader detection it was meant to provide is already handled better by ATO and Bot Auth catching the actual members directly. Drop.

**Impossible Travel as primary signal** — Recall of 37.5% max on a 8-ring fraud type, with 2 decoy FPs (D-NAT-003, D-NAT-004 get caught because members share IPs across countries). The problem is the country code XX appearing as a destination — without a real geodistance filter, any country change within the window flags. It's a weak signal on its own. Keep only as a supplementary enrichment flag (not a standalone detector), and add a proper impossible-distance check using geodistance before deploying.

---

### Remaining Gap

**Impossible Travel (12.5% recall)** — The only fraud type still significantly underperforming. The current query uses a blunt "any country change within 60 minutes" check which catches only 1/8 rings. Needs a proper geodistance filter (and ideally IP threat-intel enrichment from Tier 2 proposal D) to become a reliable signal without triggering VPN/NAT decoy FPs.

---

### Final Recommended Production Pipeline

```
LAYER 1 — STRUCTURAL (graph algorithms, run on behavioral graph)
   ├── Degree Centrality     (device≥3/ip≥3, ≥2 agencies)
   └── Node Similarity       (Jaccard ≥ 0.2)

LAYER 2 — BEHAVIORAL (property queries, run on raw CSVs)
   ├── Bot Auth              (latency < 1500ms, ≥5 fast logins)
   ├── ATO Downgrade         (30min window, ≥2 challenge downgrades)
   ├── Step-Up Probing       (≥3 HAD_STEP_UP per member)
   └── Ledger Manipulation   (≥3 updates, 120min, swing ≥ 10k)

LAYER 3 — TARGETED DETECTORS (close the two former 0%-recall gaps)
   ├── Collusion Cycles      (nx.simple_cycles on RELATED_TO directed graph)
   └── Ghost Agency          (age<90d + creditLimit>0 + empty firstSignIn)
```

**Flag = union of all layers.**

**Combined score:** 60/68 rings (88.2% recall), F1 = 0.742, Precision = 64.0%, 2 decoy FPs out of 26. 8 of 10 fraud types at 100% recall. The remaining gaps are 1 hard shared_device ring and 7 impossible_travel rings (which need geodistance filtering to resolve).

---

## New Algorithms — Proposals & Validation

### Tier 1 — ✅ IMPLEMENTED: Closed the Two 0%-Recall Gaps

These targeted the fraud types no previous algorithm touched. Both have been implemented, run, and validated against the full ground truth including paired decoys.

---

**A. Collusion Cycle Detection** — `query_collusion_cycles.py`
**RESULT: 6/6 rings caught, 100% precision, 0 decoy false positives.**

Detects circular ownership structures between agencies (A→B→C→A). Legitimate hierarchies are acyclic trees; fraud rings close the loop. Runs `nx.simple_cycles()` on the 36-edge RELATED_TO directed graph in O(V+E). The D-CHAIN decoys are linear chains with no back-edge, so they produce zero cycles by definition — 100% precision is structural, not threshold-dependent.

Detected cycles:
- 2 × 5-agency rings (riskWeight=0.9, HEAD_GSA)
- 2 × 4-agency rings (riskWeight=0.7, BRANCH_OFFICE)
- 2 × 3-agency rings (riskWeight=0.4, mixed HEAD_GSA + BRANCH_OFFICE)

Note: Restricting to riskWeight≥0.9 only catches 2/6 rings (the two easy ones). The recommended config uses all cycles with no risk filter for full recall.

---

**B. Ghost Agency Detector** — `query_ghost_agency.py`
**RESULT: 10/10 rings caught, 64.7% precision, 0 decoy false positives.**

Detects freshly-created shell agencies with high credit lines and no real activity. The actual discriminator (validated against data):
- `createdOn` within 90 days (all 10 ghosts are 7–60 days old)
- `creditLimit > 0` (all 10 ghosts have credit lines $21k–$91k)
- `agencyFirstSignIn` is empty (never signed in)

The D-BUSY decoys are all created 2012–2025 (well beyond 90 days), so the age filter alone excludes them — zero decoy FPs.

The 14 non-ring agencies flagged from the real population are also recently-created with credit lines and no sign-in history — genuinely worth investigating regardless.

Config sweep results:
| Config | Ghost Recall | Precision | Flagged | Decoy FPs |
|--------|-------------|-----------|---------|-----------|
| Naive (age<30d only) | 50% (5/10) | 11.3% | 115 | 0 |
| Full (age<90d + credit + no firstSignIn) | **100%** (10/10) | **64.7%** | 34 | 0 |

---

### Tier 2 — Beat the Existing Well-Performers Using Unused Fields

Current queries ignore three high-signal columns: `isSuccessfulLogin`, `ipThreatIntel`, and `authSessionId`.

---

**C. Failed-Login Burst Detection** — `query_failed_login_burst.py`
Targets credential_stuffing (6 rings) — currently only weakly caught by Bot Auth/Step-Up.

Detects bursts of failed logins against many accounts from one device/IP. Only 1,648 failed-login rows exist (vs 26,908 successes), so failures are rare and meaningful. Flags when a single device/IP has ≥5 failures targeting ≥3 distinct members. Catches slow, human-paced stuffing that Bot Auth's latency filter misses. Multi-member fanout discriminates against legitimate password typos.

---

**D. IP Threat-Intel Scorer** — `query_threat_intel.py`
A cross-cutting precision booster / standalone high-precision detector.

Parses `ipThreatIntel` field (`threat=NN;trust=NN` scores on ~130 edges). Flags access from IPs with `threat ≥ 50` and `trust ≤ 40`. Too sparse for standalone recall, but excellent as an enrichment multiplier — any member flagged by another algorithm AND touching a high-threat IP jumps to near-certain. Also the correct fix for Impossible Travel: replace "any country change" with "country change AND destination IP has threat intel."

---

**E. Session-Anomaly / Device-Fingerprint Mismatch** — `query_session_anomaly.py`
Targets account_takeover_downgrade and bot_scripted_auth from a different angle.

Groups edges by `authSessionId`. Flags when distinct `deviceId` values appear in one session OR `deviceId` and `tbohDeviceId` (browser fingerprint) disagree. `tbohDeviceId` is currently unused and is a second independent device identifier — mismatches are hard to fake and complement the ATO downgrade query for session hijacking detection.

---

### Tier 3 — Graph-Structural Algorithms That Fit Your Topology

The graph is sparse and disconnected, which is why Louvain and Betweenness failed. These respect that structure.

---

**F. Bipartite Co-Occurrence Projection** (member–member shared-infra graph)
A structural upgrade to Degree + Node Similarity.

Builds a member↔member graph where edge weight = number of shared devices + IPs between two members. Applies weight threshold ≥ 2 and runs connected components on the projected graph. Captures members sharing multiple distinct infra nodes pairwise — a stronger signal than sharing one busy node. Naturally excludes single-shared-IP NAT decoys. Essentially Node Similarity at member resolution instead of agency resolution.

---

**G. k-Core Decomposition** on the behavioral graph
A cheap, deterministic replacement for Louvain.

The k-core is the maximal subgraph where every node has degree ≥ k. Fraud rings form dense mutual-sharing cores (k ≥ 2–3); office NAT decoys form stars (one hub, many degree-1 leaves) which collapse at k=2. `nx.k_core(G, k=2)` deterministically strips star-shaped decoys while preserving mutually-interconnected rings — no modularity optimization, no random seed, O(E).

---

**H. Temporal Device Burst** — `query_device_burst.py`
Targets shared_device_unrelated with a time dimension the current Degree query ignores.

Groups LOGGED_IN edges by device, sorts by timestamp, applies a sliding window. Flags if ≥5 distinct members log into one device within 1 hour. The temporal compression discriminates against legitimately shared household/office devices whose accesses spread across weeks. Adds a time dimension that pure Degree analysis cannot provide.

---

### Recommended Additions to the Pipeline (Remaining Proposals)

| New Algo              | Fraud Type Targeted         | Current Recall | Expected         | Cost    |
|-----------------------|-----------------------------|----------------|------------------|---------|
| Failed-Login Burst (C)| credential_stuffing         | 100% (via Bot Auth) | higher precision | cheap   |
| Threat-Intel (D)      | cross-cutting               | n/a            | precision boost  | trivial |
| Co-occurrence proj (F)| shared_device / proxy_ip    | 87.5% / 100%  | close the last SDEV gap | medium  |
| k-core (G)            | replaces Louvain            | —              | ≥ WCC, deterministic | cheap |

**The Tier-1 detectors are now deployed.** The remaining proposals target precision improvements and the last shared_device_unrelated miss (F1-SDEV-006, a hard ring). The impossible_travel gap (7 missed rings) requires geodistance filtering — Tier 2D (IP Threat-Intel) combined with proper distance calculation is the recommended fix.
