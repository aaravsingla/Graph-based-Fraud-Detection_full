# Neptune Analytics Implementation Roadmap

**Status:** Ready to Execute  
**Timeline:** 3-4 weeks  
**Effort:** 50% infrastructure (AWS), 50% analysis & interpretation

---

## Phase Overview

| Phase | What | Timeline | Effort | Outcome |
|-------|------|----------|--------|---------|
| **Phase 1** | Analytics infrastructure + Louvain/PageRank/Degree | 2-3 weeks | 20 hrs | Top 10 communities, 20 hubs, shared artifacts identified |
| **Phase 2** | Analytics Portal (dashboard, reports) | 1 week | 15 hrs | Automated daily fraud reports, investigator UI |
| **Phase 3** | XGBoost baseline model | 1 week | 10 hrs | Fraud scoring → Adaptive Auth Agent integration |
| **Phase 4** | Neptune ML (GNN) + real-time inference | 2 weeks | 20 hrs | Real-time fraud detection at booking/login time |

---

## PHASE 1: Neptune Analytics Infrastructure (2-3 weeks)

### Week 1: Setup & Validation

**Your tasks:**

1. **Day 1-2:** Load your graph into Neptune DB
   - Run `neptune_serial_load.py` (already built)
   - Verify vertex/edge counts
   - Spot-check a few Gremlin queries

   **Commands:**
   ```bash
   python3 neptune_serial_load.py
   # Wait for completion (~1-2 hours)
   ```

2. **Day 3-4:** Create Neptune Analytics cluster
   - Run the snapshot creation flow from `ANALYTICS_QUICK_START.md`
   - Create Analytics graph from snapshot (takes 10-30 min)
   - Verify data loaded

   **Commands:**
   ```bash
   # Use the automated runner
   python3 analytics_runner.py --cluster neptune-dev-cluster
   # Wait for full completion (~2-3 hours total)
   ```

3. **Day 5:** Validate algorithms run
   - Louvain (finds communities)
   - PageRank (finds hubs)
   - Degree (finds super-shared artifacts)

   **Commands:**
   ```bash
   export ANALYTICS_ENDPOINT="g-xyz..."  # from step 2
   
   # Test Louvain
   awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
     -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
     -d '{"query": "CALL neptune.algo.louvain.mutate({...}) YIELD success, communityCount RETURN success, communityCount"}'
   ```

**Deliverable:** First fraud report with top 10 communities, 20 hubs, cross-agency device sharing

### Week 2-3: Interpretation & Validation

**Your tasks:**

1. **Analyze results:** Are the communities real or noise?
   - Pick top 3 communities → manually inspect members/agencies
   - Validate hubs are actually suspicious (high-degree devices, central members)
   - Cross-agency pairs → are they known risk, or new discovery?

2. **Document patterns:** Map findings to fraud model
   - Pattern #1 (shared device): ___ agencies, ___ devices, ___ members
   - Pattern #3 (shared IP): ___ agencies, ___ IPs
   - Pattern #5 (collusion ring): ___ agencies, ___ connections
   - (Add others from the 15-pattern model)

3. **Stakeholder walkthrough:** Present findings
   - "Here are the top 10 fraud communities Neptune found"
   - "These agencies share devices: AGENCY_001 ↔ AGENCY_042 (47 shared devices)"
   - "Top ringleader member: member-5678 (PageRank score 0.0612)"
   - Get feedback: "Are these real fraud or false positives?"

**Deliverable:** Validated fraud signals, stakeholder buy-in, next-phase go/no-go decision

---

## PHASE 2: Analytics Portal Dashboard (1 week)

### Objective
Automate the fraud query workflow into a **repeating daily/weekly batch** that feeds a **dashboard** for analysts to track fraud trends.

### Architecture

```
Neptune DB (live)
    ↓
[Daily scheduler (Lambda / EventBridge)]
    ↓
1. Create/refresh Analytics snapshot
2. Run algorithms (Louvain, PageRank, Degree)
3. Export results → S3 (Parquet/CSV)
4. Write stamped properties back to Neptune DB
    ↓
[Dashboard API (Lambda + DynamoDB)]
    ↓
[Investigator UI (React/Vue)]
```

### Build Steps

1. **Automated batch job** (AWS Step Functions or Lambda)
   - Snapshot Neptune DB
   - Create Analytics graph
   - Run 3 algorithms
   - Export results to S3
   - Pause graph
   - Notify on completion

   **Code:** Create `lambdas/analytics-orchestrator/` Lambda

2. **Results store** (S3 + DynamoDB)
   - Parquet files with stamped properties (communityId, fraudProximityScore, inDegree)
   - DynamoDB for quick lookups (agency → communities, member → hub)

3. **Dashboard backend** (API Gateway + Lambda)
   - `/api/communities` → top 10 communities by size
   - `/api/hubs` → top 20 nodes by PageRank
   - `/api/agencies/{id}` → shared devices with other agencies
   - `/api/timeline` → fraud signals over time

4. **Dashboard frontend** (React)
   - Community network visualization (nodes = agencies, edges = shared devices)
   - Hub ranking table (sortable by score)
   - Cross-agency matrix (heatmap of shared artifacts)
   - Timeline charts

**Deliverable:** Live dashboard showing updated fraud signals daily

---

## PHASE 3: XGBoost Baseline Model (1 week)

### Objective
Train a **supervised fraud classifier** that combines Neptune Analytics algorithm outputs with known fraud labels.

### Steps

1. **Build label store** (Prepare training data)
   - Add `FraudCase` vertex (design spec already has this)
   - Link confirmed frauds to Member/Agency/Booking nodes
   - Start with manual labeling of known fraud cases
   - Target: 100-500 labelled instances

   **Pseudo-code:**
   ```cypher
   CREATE (:FraudCase {caseId: "FC001", status: "CONFIRMED"})
   CREATE (:FraudCase {caseId: "FC002", status: "INVESTIGATING"})
   
   // Link to nodes
   MATCH (m:Member {memberId: "member-123"}), (fc:FraudCase {caseId: "FC001"})
   CREATE (m)-[:INVOLVED_IN]->(fc)
   ```

2. **Export features for training**
   - From Neptune Analytics: communityId, pageRankScore, inDegree, ...
   - From Neptune DB: Member properties (createdAt, status, ...), Agency properties, booking counts
   - From source systems: transaction volumes, booking amounts, auth failure rates

   **Export to CSV:**
   ```python
   # Query Neptune → extract features
   # columns: memberId, label (fraud/clean), pageRankScore, inDegree, communityId, createdAt, bookingCount, failedAuthCount, ...
   ```

3. **Train XGBoost on SageMaker**
   ```python
   import sagemaker
   from xgboost import XGBClassifier
   
   # Load training data
   X_train, y_train = load_features("fraud_features.csv")
   
   # Train
   model = XGBClassifier(max_depth=6, learning_rate=0.1, n_estimators=100)
   model.fit(X_train, y_train)
   
   # Feature importance
   print(model.feature_importances_)  # Which features matter most?
   
   # Save
   model.save_model("fraud_model_v1.pkl")
   ```

4. **Integrate into Adaptive Auth Agent**
   - When a booking is created, score it with the XGBoost model
   - Combine score with other signals (velocity, device reputation, etc.)
   - Higher score → trigger step-up or block

   **Pseudo-code:**
   ```python
   def adaptive_auth_score(booking):
       # Get member features
       member = db.get_member(booking.member_id)
       pagerank = member.pageRankScore
       degree = member.inDegree
       community = member.communityId
       
       # Score with XGBoost
       score = fraud_model.predict([[pagerank, degree, community, ...]])
       
       # Decision
       if score > 0.7:
           return "BLOCK"
       elif score > 0.4:
           return "STEP_UP"
       else:
           return "ALLOW"
   ```

**Deliverable:** Fraud model scoring bookings in real-time, integration with existing auth system

---

## PHASE 4: Neptune ML (GNN) Real-Time Inference (2 weeks)

### Objective
Deploy a **Graph Neural Network** trained on historical fraud labels that can score **brand-new bookings** in real-time by analyzing their subgraph neighbourhood.

### Steps

1. **Export graph + labels for Neptune ML**
   ```bash
   # Neptune ML handles the export
   aws sagemaker-geospatial create-vector-enrichment-job ...  # Export CSV
   
   # Result: CSV files with node IDs, features, and labels (fraud/clean)
   ```

2. **Configure Neptune ML data processing**
   - Define node types (Member, Agency, Device, etc.) and their features
   - Define edge types (LOGGED_IN, CREATED_BOOKING, etc.)
   - Specify labels (isFraud on Member nodes)

3. **Train R-GCN model on SageMaker**
   - Neptune ML wraps DGL (Deep Graph Library) training
   - Hyperparameter optimization (HPO) picks best model
   - Output: deployed SageMaker endpoint

   **Timeline:** 2-4 hours for training

4. **Deploy endpoint + enable inductive inference**
   - Model can score **new nodes not in training set** (real-time)
   - Extract n-hop subgraph around new member
   - Pull features for that subgraph
   - Send to endpoint → fraud probability

5. **Integrate into real-time booking pipeline**
   ```groovy
   // Gremlin query with inline ML inference
   g.with("Neptune#ml.endpoint", "synapse-fraud-nc-ep")
    .V('member-new-user')
    .properties("riskTier", "Neptune#ml.score")
      .with("Neptune#ml.classification")
      .with("Neptune#ml.inductiveInference")  // new member!
   ```

**Deliverable:** Real-time fraud scoring on new bookings, integration with booking API

---

## Task Breakdown & Schedule

### Week 1: Setup & Validation
- [ ] Verify Neptune DB has full graph loaded
- [ ] Create Analytics snapshot
- [ ] Create Analytics graph
- [ ] Run Louvain → get community count
- [ ] Run PageRank → get top 20 hubs
- [ ] Run Degree → get super-shared artifacts
- [ ] Extract top 10 communities for manual inspection

### Week 2-3: Analysis & Stakeholder Alignment
- [ ] Inspect top 3 communities (are they real fraud?)
- [ ] Validate hubs (are high-PageRank nodes actually suspicious?)
- [ ] Cross-check cross-agency device sharing (known risk or new?)
- [ ] Document patterns → fraud model
- [ ] Present findings to stakeholders
- [ ] Get approval for Phase 2

### Week 4: Dashboard Infrastructure
- [ ] Build automated scheduler (Lambda + EventBridge)
- [ ] Automate snapshot → graph creation → algorithms
- [ ] Export results to S3 (Parquet)
- [ ] Build API endpoints (communities, hubs, agencies)
- [ ] Deploy dashboard frontend (React)

### Week 5: Training Data & XGBoost
- [ ] Build FraudCase label store in Neptune
- [ ] Export features (algorithm outputs + node properties)
- [ ] Train XGBoost on SageMaker
- [ ] Validate model performance (AUC, precision, recall)
- [ ] Integrate scores into Adaptive Auth Agent

### Weeks 6-7: Neptune ML
- [ ] Configure ML data processing
- [ ] Train R-GCN model
- [ ] Deploy SageMaker endpoint
- [ ] Enable inductive inference
- [ ] Integration test with real bookings

---

## Success Criteria

### Phase 1 ✅
- [ ] Fraud analytics report generated
- [ ] Top 10 communities identified and documented
- [ ] 20+ hubs ranked by centrality
- [ ] Cross-agency device sharing matrix built
- [ ] Stakeholder validation: "These are real fraud signals"

### Phase 2 ✅
- [ ] Daily analytics refresh (fully automated)
- [ ] Dashboard live and queried by analysts
- [ ] 3+ fraud investigations per week initiated from dashboard

### Phase 3 ✅
- [ ] XGBoost model AUC ≥ 0.8 on validation set
- [ ] Model integrated into booking pipeline
- [ ] 10-20% of risky bookings flagged for step-up

### Phase 4 ✅
- [ ] Neptune ML model trained and deployed
- [ ] Real-time inference latency < 200ms
- [ ] Model catches 30%+ of fraud attempts not caught by rule-based system

---

## Estimated Costs (Monthly)

| Component | Dev/Test | Prod |
|-----------|----------|------|
| Neptune DB (db.t3.medium) | $50 | $500 |
| Neptune Analytics (32 m-NCU, paused mostly) | $50 | $200 |
| SageMaker (training + endpoint) | $100 | $300 |
| S3 (analytics results, snapshots) | $10 | $50 |
| Lambda (scheduled jobs) | $5 | $10 |
| **Total** | **~$215** | **~$1,060** |

**Saving tip:** Keep Analytics paused except during batch runs (saves 90% on Analytics costs)

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Graph too small/sparse for algorithms | Communities are noise | Run on prod data first; validate with domain experts |
| Algorithm outputs don't correlate with fraud | Model performance poor | Start with Phase 3 baseline (XGBoost + algorithm features) |
| Too many false positives | Analyst fatigue | Tune thresholds based on Phase 1 findings |
| Neptune ML model training too slow | Schedule delays | Use Neptune ML auto-HPO; train on subset first |
| Real-time inference adds latency | Booking UX impact | Cache scores; use background async refresh |

---

## Next Action

**Start here:** Run `ANALYTICS_QUICK_START.md` commands to get your first fraud report.

Then share findings with stakeholders and proceed to Phase 2.

**Questions?**
- Neptune Analytics docs: https://docs.aws.amazon.com/neptune-analytics/
- Gremlin/OpenCypher: See `docs/queries/` in this repo
- Fraud patterns: See `docs/05-neptune-analytics-and-ml-for-fraud-detection.md`
