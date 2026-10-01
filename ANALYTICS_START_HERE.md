# Neptune Analytics for Fraud Detection — START HERE

Welcome! You have a fraud detection graph loaded in Neptune DB. Now it's time to run advanced fraud detection algorithms.

---

## What You're About to Do (5-Minute Overview)

```
Your Neptune DB Graph (already loaded)
        ↓
Create a snapshot (5 min)
        ↓
Load into Neptune Analytics (30 min, automatic)
        ↓
Run 3 algorithms (Louvain, PageRank, Degree) — 5 min each
        ↓
Query results (instant)
        ↓
Get your first FRAUD REPORT
  ├─ Top 10 communities (collusion rings)
  ├─ Top 20 hubs (ringleaders)
  └─ Cross-agency device sharing (Pattern #1)
```

**Total time:** 1.5-2 hours  
**Your effort:** 30 minutes (mostly waiting for AWS)  
**Outcome:** First fraud signals detected

---

## Three Ways to Do This

### Option A: Automated (Recommended)

Run the Python script that handles everything:

```bash
python3 analytics_runner.py --cluster neptune-dev-cluster
```

**What it does:**
1. Creates snapshot
2. Creates Analytics graph
3. Runs all algorithms
4. Queries results
5. Generates report
6. Pauses graph (saves 90% cost)

**Output:** `fraud_analytics_report_TIMESTAMP.json`

**Go-to:** If this is your first time, use this.

---

### Option B: Copy-Paste Commands

All commands in one place, no Python needed:

```bash
# Set your Neptune cluster
export NEPTUNE_DB_CLUSTER="neptune-dev-cluster"
export AWS_REGION="eu-west-1"

# Then follow ANALYTICS_QUICK_START.md
# Copy and paste each command as you go
```

**Output:** Manual fraud report (you compile results yourself)

**Go-to:** If you want to understand each step or debug issues.

---

### Option C: Step-by-Step Detailed Guide

Read every detail of what's happening:

**Documentation files:**
1. `HOW_NEPTUNE_ANALYTICS_WORKS.md` — Understand the concepts
2. `NEPTUNE_ANALYTICS_GUIDE.md` — 9-step technical guide
3. `ANALYTICS_QUICK_START.md` — Copy-paste commands

**Go-to:** If you want deep understanding before executing.

---

## Pick Your Path

### I just want to see results (NOW)

```bash
# 1. Make sure you have awscurl installed
pip install awscurl

# 2. Run the automated script
python3 analytics_runner.py --cluster neptune-dev-cluster

# 3. Wait ~2 hours
# 4. Check the generated report
cat fraud_analytics_report_*.json
```

**Next:** Jump to "After You Get Your Report" below.

---

### I want to understand what's happening

```bash
# 1. Read the overview
#    HOW_NEPTUNE_ANALYTICS_WORKS.md (15 min read)

# 2. Follow the step-by-step guide
#    NEPTUNE_ANALYTICS_GUIDE.md
#    Copy each command, run it, read the output

# 3. Adjust based on your data
```

---

### I want detailed reference documentation

This is a full implementation guide. Files:

| File | Purpose | Read Time |
|------|---------|-----------|
| `HOW_NEPTUNE_ANALYTICS_WORKS.md` | Concepts + visuals | 15 min |
| `NEPTUNE_ANALYTICS_GUIDE.md` | 9-step technical walkthrough | 20 min |
| `ANALYTICS_QUICK_START.md` | Copy-paste commands | 10 min |
| `analytics_runner.py` | Automated Python script | 5 min to run |
| `ANALYTICS_ROADMAP.md` | 4-phase implementation (weeks 1-7) | 20 min |

---

## After You Get Your Report

### Step 1: Understand Your Findings

The report contains:

```json
{
  "findings": {
    "top_communities": [
      {"community": 1, "nodeCount": 523},
      {"community": 5, "nodeCount": 412},
      ...
    ],
    "top_hubs": [
      {"nodeType": "Device", "identifier": "device-abc", "score": 0.0856},
      {"nodeType": "Agency", "identifier": "AGENCY_001", "score": 0.0734},
      ...
    ],
    "cross_agency_devices": [
      {
        "agency1": "AGENCY_001",
        "agency2": "AGENCY_042",
        "sharedDeviceCount": 47
      },
      ...
    ]
  }
}
```

### Step 2: Validate with Your Domain

Ask yourself:

- **Communities:** "Are agencies in community 1 actually related? Or is this a new discovery?"
- **Hubs:** "Is device-abc really a proxy farm? Should AGENCY_001 be flagged?"
- **Shared devices:** "Did AGENCY_001 ↔ AGENCY_042 share 47 devices legitimately or is this fraud?"

### Step 3: Share with Stakeholders

Present the findings:

> "Neptune Analytics found 47 fraud communities in your graph.
> 
> **Top finding:** AGENCY_001 and AGENCY_042 share 47 devices in common, with 5 and 8 members respectively. This is suspicious.
> 
> **Hubs:** Device-abc is shared across 487 logins from 45 members in 12 agencies. This looks like a proxy farm.
> 
> **Next:** Manual investigation of these signals + integration into fraud decisioning."

### Step 4: Decide on Phase 2

- **Go:** These signals are real and actionable → proceed to build Analytics Portal (Phase 2)
- **No-go:** Signals don't match fraud patterns → investigate data quality or recalibrate algorithms

---

## Troubleshooting

### "awscurl command not found"

```bash
pip install awscurl
```

### "Graph is in CREATING status"

Analytics graphs take 10-30 minutes to load. This is normal. Check status:

```bash
aws neptune-analytics get-graph \
  --graph-identifier g-xyz1234567 \
  --region eu-west-1
```

Wait until `status` is `AVAILABLE`.

### "No results from queries"

Algorithms must run first. Did you run Louvain/PageRank/Degree? They add properties to nodes. Then queries read those properties.

### "Graph endpoint not responding"

Graph might be paused. Resume it:

```bash
aws neptune-analytics update-graph \
  --graph-identifier g-xyz1234567 \
  --action "resume" \
  --region eu-west-1
```

Wait 2-3 minutes, then try again.

### "Timeout waiting for status"

AWS operations sometimes take longer. Try checking manually:

```bash
aws neptune-analytics get-graph --graph-identifier g-xyz1234567 --region eu-west-1
```

If stuck in CREATING > 45 min, contact AWS support.

---

## Cost Management

### During Development

Always pause when not using:

```bash
aws neptune-analytics update-graph \
  --graph-identifier g-xyz1234567 \
  --action "pause" \
  --region eu-west-1
```

**Savings:** 90% cost reduction (from ~$230/month running to ~$23/month paused)

### During Production

**Recommended:** Scheduled batch (daily)

```
8am: Resume graph (2 min)
8:02am: Run algorithms (10 min)
8:12am: Query results (5 min)
8:17am: Export to S3 (5 min)
8:22am: Pause graph
Cost: ~$0.50/day = $15/month
```

vs

```
Graph always running: $230/month
```

---

## What Happens Next

### Week 1: Get Results
- Run analytics_runner.py
- Get fraud report
- Validate findings

### Week 2-3: Share Findings
- Present to stakeholders
- Document patterns
- Get approval for Phase 2

### Week 4+: Build Dashboard
- Automate daily runs
- Build investigator UI
- Train fraud model (XGBoost)
- Deploy real-time GNN

(See `ANALYTICS_ROADMAP.md` for full schedule)

---

## Quick Reference

### Files in This Package

```
ANALYTICS_START_HERE.md (you are here)
├─ HOW_NEPTUNE_ANALYTICS_WORKS.md (concepts)
├─ NEPTUNE_ANALYTICS_GUIDE.md (detailed guide)
├─ ANALYTICS_QUICK_START.md (copy-paste)
├─ analytics_runner.py (automated script)
├─ ANALYTICS_ROADMAP.md (4-phase plan)
└─ This folder: Synapse/
```

### Key Commands

```bash
# Run everything automatically
python3 analytics_runner.py --cluster neptune-dev-cluster

# Or manually
# 1. Create snapshot
aws neptune create-db-cluster-snapshot ...
# 2. Create Analytics graph
aws neptune-analytics create-graph ...
# 3. Run algorithms
awscurl -X POST https://ENDPOINT/openCypher -d '{"query": "CALL neptune.algo.louvain..."}'
# 4. Query results
awscurl -X POST https://ENDPOINT/openCypher -d '{"query": "MATCH (n) WHERE n.communityId..."}'
# 5. Pause to save costs
aws neptune-analytics update-graph --action pause
```

### Key Algorithms

```
Louvain      → Finds 10-100 fraud communities (collusion rings)
PageRank     → Ranks 20-50 top hubs (ringleaders)
Degree       → Finds super-shared artifacts (proxy farms)
```

### Key Fraud Patterns

```
Pattern #1:  Device shared across agencies ← Louvain + Degree
Pattern #3:  IP shared across agencies ← Degree
Pattern #5:  Agency collusion ring ← Louvain
Pattern #10: Composite fraud score ← All 3 algorithms
```

---

## Support & Questions

### Concepts
- See `HOW_NEPTUNE_ANALYTICS_WORKS.md`
- AWS Neptune Analytics docs: https://docs.aws.amazon.com/neptune-analytics/

### Technical Issues
- See `NEPTUNE_ANALYTICS_GUIDE.md` → Troubleshooting section
- Check AWS CloudWatch logs

### Fraud Model Questions
- See `docs/05-neptune-analytics-and-ml-for-fraud-detection.md`
- See `docs/04-graph-vs-rdbms-for-fraud-detection.md`

### Implementation Roadmap
- See `ANALYTICS_ROADMAP.md`

---

## One Last Thing: You're About to Find Real Fraud

Neptune Analytics + your loaded graph = **the first time you can actually see fraud patterns** that were invisible in the RDBMS.

The algorithms will find:
- ✅ Agencies that share devices (hidden before)
- ✅ Collusion rings (hard to detect in SQL)
- ✅ Proxy farms / super-hubs (obvious once you see them)
- ✅ Risk propagation paths (not queryable before)

These findings are **actionable today** and will drive:
- Investigator prioritization
- Real-time fraud scoring (next phases)
- Policy changes
- Risk management decisions

Go find that fraud. 🎯

---

## Ready? Let's Go

### Quickest Path: Run the Script

```bash
python3 analytics_runner.py --cluster neptune-dev-cluster
```

### Detailed Path: Copy-Paste Commands

Follow `ANALYTICS_QUICK_START.md`

### Learning Path: Read First

Read `HOW_NEPTUNE_ANALYTICS_WORKS.md`, then decide.

---

**Questions?** Start with `HOW_NEPTUNE_ANALYTICS_WORKS.md` — it covers all the concepts in plain English.

**Let's detect some fraud.** 🚀
