# Neptune Analytics for Fraud Detection — Complete Documentation

This folder contains comprehensive guides for setting up and running Neptune Analytics algorithms on your Synapse fraud detection graph.

## Quick Navigation

### 🚀 **Start Here** (Everyone)
- **[ANALYTICS_START_HERE.md](./ANALYTICS_START_HERE.md)** — Entry point; pick your path (automated, copy-paste, or detailed)

### 📚 **Understanding the Concepts** (5-15 min read)
- **[HOW_NEPTUNE_ANALYTICS_WORKS.md](./HOW_NEPTUNE_ANALYTICS_WORKS.md)** — Visual, conceptual explanation of what Neptune Analytics is and why you need it

### 🔧 **Implementation Guides**

#### Option A: Automated (Recommended)
- **[analytics_runner.py](./analytics_runner.py)** — Python script that does everything automatically
- Run: `python3 analytics_runner.py --cluster neptune-dev-cluster`
- Output: JSON fraud report with top communities, hubs, and patterns

#### Option B: Manual Step-by-Step
- **[NEPTUNE_ANALYTICS_GUIDE.md](./NEPTUNE_ANALYTICS_GUIDE.md)** — 9-step detailed technical walkthrough (read this if you want to understand each step)
- **[ANALYTICS_QUICK_START.md](./ANALYTICS_QUICK_START.md)** — Copy-paste commands for impatient people (read this if you want to just get it done)

### 📋 **Planning & Strategy**
- **[ANALYTICS_ROADMAP.md](./ANALYTICS_ROADMAP.md)** — 4-phase implementation plan (Weeks 1-7, from analytics setup to real-time ML inference)

---

## The 30-Second Pitch

You have a fraud graph loaded in Neptune DB. Neptune Analytics lets you:

1. **Find fraud communities** — `Louvain` algorithm detects collusion rings (Pattern #5)
2. **Rank ringleaders** — `PageRank` algorithm finds structurally important members
3. **Find super-shared artifacts** — `Degree` algorithm detects proxy farms and shared devices

All in **minutes**, not hours. No complex Gremlin queries needed.

**Next:** Pick a guide above and run it. Expected time: 1-2 hours, mostly AWS compute.

---

## File Reference

| File | Purpose | Audience | Time |
|------|---------|----------|------|
| ANALYTICS_START_HERE.md | Entry point + navigation | Everyone | 5 min |
| HOW_NEPTUNE_ANALYTICS_WORKS.md | Conceptual overview | Developers who want to understand | 15 min |
| NEPTUNE_ANALYTICS_GUIDE.md | Detailed 9-step walkthrough | Technical implementers | 20 min |
| ANALYTICS_QUICK_START.md | Copy-paste commands | Impatient people | 10 min |
| analytics_runner.py | Automated Python script | Anyone who likes automation | 0 min (just run it) |
| ANALYTICS_ROADMAP.md | 4-phase roadmap (weeks 1-7) | Planners, project managers | 20 min |
| README_ANALYTICS.md | This file | Everyone | 5 min |

---

## Quick Start (Copy-Paste)

### 1. Install Prerequisites
```bash
pip install awscurl boto3
```

### 2. Run Automated Setup
```bash
python3 analytics_runner.py --cluster neptune-dev-cluster
# Wait ~2 hours
```

### 3. Check Results
```bash
cat fraud_analytics_report_*.json | jq '.findings'
```

### 4. Pause to Save Costs
```bash
aws neptune-analytics update-graph \
  --graph-identifier g-xyz1234567 \
  --action pause \
  --region eu-west-1
```

---

## Key Concepts

### Neptune DB vs Neptune Analytics

| Aspect | Neptune DB | Neptune Analytics |
|--------|------------|-------------------|
| **Use for** | Real-time fraud rules | Batch algorithms |
| **Query Language** | Gremlin, openCypher | openCypher only |
| **Algorithms** | None (you write them) | 25+ built-in (Louvain, PageRank, etc.) |
| **Latency** | Sub-100ms | Sub-second |
| **Freshness** | Live | Hours old (snapshot-based) |
| **Scaling** | Per-request | Fixed capacity (m-NCUs) |

### The 3 Algorithms You'll Run

1. **Louvain Community Detection**
   - Finds groups of highly connected nodes
   - For fraud: Detects collusion rings (agencies sharing devices)
   - Output: `communityId` stamped on each node

2. **PageRank Centrality**
   - Ranks nodes by structural importance
   - For fraud: Finds ringleaders and hubs
   - Output: `pageRankScore` on each node

3. **Degree Centrality**
   - Counts incident edges per node
   - For fraud: Finds super-shared artifacts (proxy farms)
   - Output: `inDegree` on each node

---

## Fraud Patterns Detected

| Pattern # | What | Algorithm |
|-----------|------|-----------|
| #1 | Device shared across agencies | Louvain + Degree |
| #3 | IP shared across agencies | Degree |
| #5 | Agency collusion ring | Louvain |
| #10 | Composite fraud score | All 3 |
| More... | See docs/05-neptune-analytics-and-ml-for-fraud-detection.md | — |

---

## Implementation Timeline

### Week 1: Setup & Validation
- [ ] Create Neptune Analytics cluster (this package)
- [ ] Run Louvain, PageRank, Degree algorithms
- [ ] Generate first fraud report
- [ ] Validate with domain experts

### Week 2-3: Analysis & Stakeholder Alignment
- [ ] Investigate top 10 communities manually
- [ ] Document fraud patterns found
- [ ] Present to stakeholders
- [ ] Get approval for Phase 2

### Week 4+: Analytics Portal & ML
- [ ] Build automated daily analytics runs
- [ ] Create fraud dashboard
- [ ] Train XGBoost model (Phase 3)
- [ ] Deploy real-time GNN (Phase 4)

(See ANALYTICS_ROADMAP.md for full details)

---

## Cost Optimization

**Running:** 32 m-NCU Analytics cluster = ~$230/month  
**Paused:** 32 m-NCU Analytics cluster = ~$23/month (90% savings!)

**Recommended:** Run daily batch (10-15 min) → pause after

```bash
# Daily cron job
0 9 * * * python3 analytics_runner.py --cluster neptune-dev-cluster
# Then pause manually or automatically after completion
```

Cost per daily run: ~$0.50 → **$15/month total**

---

## Troubleshooting

### Graph creation is stuck

```bash
# Check status
aws neptune-analytics get-graph \
  --graph-identifier g-xyz1234567 \
  --region eu-west-1
```

If stuck >45 minutes, contact AWS support.

### "No results" from queries

Algorithms must run first. They add properties to nodes that queries then read.

### High costs

Pause the graph when not using:

```bash
aws neptune-analytics update-graph \
  --graph-identifier g-xyz1234567 \
  --action pause \
  --region eu-west-1
```

---

## Next Steps

1. **Read** [ANALYTICS_START_HERE.md](./ANALYTICS_START_HERE.md) for navigation
2. **Choose** your implementation path (automated, manual, or detailed)
3. **Run** the setup (1-2 hours)
4. **Analyze** the fraud report
5. **Share** findings with stakeholders
6. **Follow** [ANALYTICS_ROADMAP.md](./ANALYTICS_ROADMAP.md) for Phases 2-4

---

## Support

- **Concepts:** Read [HOW_NEPTUNE_ANALYTICS_WORKS.md](./HOW_NEPTUNE_ANALYTICS_WORKS.md)
- **Technical:** See [NEPTUNE_ANALYTICS_GUIDE.md](./NEPTUNE_ANALYTICS_GUIDE.md) → Troubleshooting
- **Strategy:** See [ANALYTICS_ROADMAP.md](./ANALYTICS_ROADMAP.md)
- **AWS Docs:** https://docs.aws.amazon.com/neptune-analytics/

---

**Ready to detect fraud?** Start with [ANALYTICS_START_HERE.md](./ANALYTICS_START_HERE.md) 🚀
