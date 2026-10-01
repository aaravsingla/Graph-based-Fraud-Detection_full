# Fraud Detection Query Compatibility Analysis

**Date:** June 26, 2026  
**Graph Status:** Active Neptune instance with SQL, DynamoDB, and OpenSearch data loaded  
**Schema:** See `docs/diagrams/02-graph-schema.md` and `03-full-graph-schema.md`

---

## Executive Summary

- **15 Fraud Patterns** defined in `docs/queries/03-fraud-detection-queries.md`
- **Can Run:** 12 patterns (80%)
- **Cannot Run:** 3 patterns (20%) — missing vertex types or edge relationships
- **Additional Queries Possible:** 8+ patterns not mentioned in doc

---

## Pattern Compatibility Matrix

| Pattern | Name | Status | Notes |
|---------|------|--------|-------|
| 1 | Shared Device Across Agencies | ✅ **CAN RUN** | Device, Member, Agency vertices + LOGGED_IN edge exist |
| 2 | Impossible Travel (Login Velocity) | ✅ **CAN RUN** | Member, Device, IpAddress + LOGGED_IN, ACCESSED_FROM edges exist with ipCountryCode |
| 3 | Shared IP Across Agencies | ✅ **CAN RUN** | IpAddress, Member, Agency + ACCESSED_FROM edge with full properties |
| 4 | Ghost Booking Ring | ❌ **CANNOT RUN** | Missing edge: `BOOKED_WITH_DEVICE`. See reasoning below. |
| 5 | Agency Collusion Ring | ✅ **CAN RUN** | Agency → Agency `RELATED_TO` edges with relationshipType, riskWeight, propagationRule |
| 6 | Address Reuse Across Agencies | ✅ **CAN RUN** | Address vertices, REGISTERED_AT (Member), HEADQUARTERED_AT (Agency) edges |
| 7 | Risk Propagation Through Hierarchy | ✅ **CAN RUN** | RELATED_TO edges carry propagationRule and riskWeight for weighted traversal |
| 8 | Ledger Manipulation by Flagged Members | ✅ **CAN RUN** | Member.status exists, CREATED_LEDGER, UPDATED_LEDGER edges with previousBalance/newBalance |
| 9 | Booking Volume Spike | ❌ **CANNOT RUN** | No HotelItinerary vertices loaded, no BOOKING_FOR edges |
| 10 | Composite Agency Fraud Score | ⚠️ **PARTIAL** | Depends on #1, #3, #8, #9. Can score on 3 of 5 dimensions (missing device/volume) |
| 11 | Credential Stuffing via Failed Auth | ✅ **CAN RUN** | AuthEvent vertices with isSuccessfulLogin, Device, IpAddress + AUTH_USED_DEVICE, AUTH_FROM_IP |
| 12 | Auth Method Cycling | ✅ **CAN RUN** | Member, AuthEvent vertices with eventType property |
| 13 | Bot Detection via Response Latency | ✅ **CAN RUN** | AuthEvent.initAuthTime, AuthEvent.respondToAuthTime exist → calculate responseLatencyMs |
| 14 | Cross-Agency Step-Up Rule Correlation | ✅ **CAN RUN** | StepUpEvent, StepUpRule, Member, Agency vertices with TRIGGERED_RULE, HAD_STEP_UP edges |
| 15 | Forgot Password Abuse | ✅ **CAN RUN** | AuthEvent.eventType = RESET_PASSWORD, IpAddress, AUTH_FROM_IP edge |

---

## Detailed Analysis

### ✅ RUNNABLE PATTERNS (12/15)

#### Pattern 1 — Shared Device Across Agencies

**Status:** ✅ Fully runnable

**Why it works:**
- Device vertices exist (from OpenSearch login history)
- Member vertices exist (from SQL)
- Agency vertices exist (from SQL)
- LOGGED_IN edges exist: Member → Device with ipCountryCode property
- Implicit path: Member → Device ← Member2 → Agency

**Verified edges:** LOGGED_IN ✓

**Query compatibility:** Run as-is, but adjust:
```groovy
// ACTUAL: Member doesn't have outgoing LOGGED_IN; it's incoming
g.V().hasLabel('Device')
  .in('LOGGED_IN').hasLabel('Member')  // ← Reverse direction
  .out('BELONGS_TO').hasLabel('Agency')
  .dedup()
```

---

#### Pattern 2 — Impossible Travel (Login Velocity)

**Status:** ✅ Fully runnable

**Why it works:**
- AuthEvent vertices have ipCountryCode and timestamps
- Alternative path via ACCESSED_FROM: Member → IpAddress with ipCountryCode
- timestamps available on edges for velocity calculation

**Verified properties:**
- `AuthEvent.initAuthTime` ✓
- `AuthEvent.respondToAuthTime` ✓
- `AuthEvent.ipCountryCode` ✓
- `Member.memberId`, `Member.email` ✓

**Query modification:**
```groovy
// Use AuthEvent path for finer granularity
g.V().hasLabel('AuthEvent')
  .in('HAD_AUTH_EVENT').hasLabel('Member')
  // Group by member, find multi-country within 4 hours
  .group().by(__.values('memberId')).unfold()
  .select(values).unfold().where(__.values('ipCountryCode').dedup().count().is(gt(1)))
```

---

#### Pattern 3 — Shared IP Across Agencies

**Status:** ✅ Fully runnable

**Why it works:**
- IpAddress vertices exist
- ACCESSED_FROM edges: Member → IpAddress with all needed properties
- Member → BELONGS_TO → Agency chain intact

**Verified:** ACCESSED_FROM edge ✓, IpAddress properties ✓, BELONGS_TO ✓

---

#### Pattern 5 — Agency Collusion Ring

**Status:** ✅ Fully runnable

**Why it works:**
- Agency → RELATED_TO → Agency edges fully specified
- relationshipType, riskWeight, propagationRule properties present
- Cycle detection with simplePath() works as documented

**Verified:** RELATED_TO edges with all properties ✓

---

#### Pattern 6 — Address Reuse Across Agencies

**Status:** ✅ Fully runnable

**Why it works:**
- Address vertices exist
- REGISTERED_AT: Member → Address
- HEADQUARTERED_AT: Agency → Address
- Both edges carry registeredAt timestamp

**Verified:** Both edges ✓, Address properties (city, countryCode, postalCode) ✓

---

#### Pattern 7 — Risk Propagation Through Agency Hierarchy

**Status:** ✅ Fully runnable

**Why it works:**
- RELATED_TO edges carry propagationRule and riskWeight
- Can traverse respecting rules (BIDIRECTIONAL, UP_ONLY, NONE)

**Verified:** Full rule semantics ✓

---

#### Pattern 8 — Ledger Manipulation by Flagged Members

**Status:** ✅ Fully runnable

**Why it works:**
- Member.status exists (in our data: "Active", "Suspended", "Flagged")
- CREATED_LEDGER: Member → Ledger
- UPDATED_LEDGER: Member → Ledger with previousBalance, newBalance
- OWNS_LEDGER: Agency → Ledger

**Verified:** All edges and properties ✓

---

#### Pattern 11 — Credential Stuffing via Failed Auth

**Status:** ✅ Fully runnable

**Why it works:**
- AuthEvent.isSuccessfulLogin property exists (bool: true/false)
- AuthEvent has initAuthTime for window filtering
- AUTH_USED_DEVICE: AuthEvent → Device
- AUTH_FROM_IP: AuthEvent → IpAddress
- HAD_AUTH_EVENT: Member → AuthEvent

**Verified:** All properties and edges ✓

---

#### Pattern 12 — Auth Method Cycling

**Status:** ✅ Fully runnable

**Why it works:**
- AuthEvent.eventType property exists (RESET_PASSWORD, LOGIN, etc.)
- Can group by member and distinct eventType
- HAD_AUTH_EVENT edges support traversal

**Verified:** eventType property ✓

---

#### Pattern 13 — Bot Detection via Response Latency

**Status:** ✅ Fully runnable

**Why it works:**
- AuthEvent.initAuthTime exists (epoch ms)
- AuthEvent.respondToAuthTime exists (epoch ms)
- Can calculate: responseLatencyMs = respondToAuthTime - initAuthTime
- Filter for lt(2000)

**Verified:** Both timestamps ✓

---

#### Pattern 14 — Cross-Agency Step-Up Rule Correlation

**Status:** ✅ Fully runnable

**Why it works:**
- StepUpEvent vertices exist
- StepUpRule vertices exist (derived from reason field)
- HAD_STEP_UP: Member → StepUpEvent
- TRIGGERED_RULE: StepUpEvent → StepUpRule
- All agency/member links preserved

**Verified:** All vertices and edges ✓

---

#### Pattern 15 — Forgot Password Abuse

**Status:** ✅ Fully runnable

**Why it works:**
- AuthEvent.eventType includes "RESET_PASSWORD"
- IpAddress vertices exist
- AUTH_FROM_IP: AuthEvent → IpAddress
- Can filter by time window

**Verified:** eventType and edge ✓

---

### ❌ CANNOT RUN (3/15)

#### Pattern 4 — Ghost Booking Ring

**Status:** ❌ Cannot run — missing HotelItinerary vertices and booking edges

**What's missing:**
1. **HotelItinerary vertices** — NOT loaded in Neptune
   - CSV files exist in `/batchjobs/data/final/vertices/sql-hotel_itineraries.csv`
   - But load manifest doesn't include them
   - Check if load job failed or was skipped

2. **Missing edges:**
   - `BOOKED_WITH_DEVICE`: HotelItinerary → Device
   - `BOOKED_FOR`: HotelItinerary → Agency
   - `BOOKED_AT_HOTEL`: HotelItinerary → Hotel

**Required to fix:** Re-run Neptune load including HotelItinerary vertices and booking edges

---

#### Pattern 9 — Booking Volume Spike

**Status:** ❌ Cannot run — same root cause as Pattern 4

**What's missing:**
- HotelItinerary vertices
- BOOKING_FOR edges: HotelItinerary → Agency with bookedAt timestamp
- Need bookedAt property for time-windowing

**Required to fix:** Load HotelItinerary vertices + BOOKING_FOR, BOOKING_FROM_IP, BOOKED_WITH_DEVICE edges

---

#### Pattern 10 — Composite Agency Fraud Score

**Status:** ⚠️ Partial — depends on #1, #3, #8, #9

**Runnable dimensions:**
- ✅ `sharedDeviceScore` (via Pattern 1) — 30 pts per cross-agency device
- ✅ `sharedIpScore` (via Pattern 3) — 20 pts per cross-agency IP
- ✅ `flaggedMemberScore` (via Pattern 8) — 25 pts per flagged member
- ❌ `volumeSpikeScore` (Pattern 9) — **missing** due to no HotelItinerary
- ❌ `collusionRing` (Pattern 5) — separate traversal, needs app-side aggregation

**Workaround:** Run composite with 4 of 5 components, returning null for volumeSpikeScore

---

### ⚠️ QUERIES THAT CANNOT RUN (Root Causes)

| Reason | Patterns Affected | Fix |
|--------|-------------------|-----|
| HotelItinerary vertices missing | 4, 9 | Reload from `sql-hotel_itineraries.csv` |
| Booking edges missing | 4, 9, 10 | Define ETL for BOOKED_WITH_DEVICE, BOOKED_FOR, BOOKED_AT_HOTEL |
| Hotel vertices missing | 4 (implied) | Load from OpenSearch hotel-static-data |

---

## Additional Query Patterns (Not in Doc)

Beyond the 15 documented patterns, your graph supports these fraud signals:

### 1. **Member-Device Timeline Anomalies**

**Signal:** A member logs in from new devices at accelerating frequency

```groovy
// Members acquiring new devices in last 30 days
g.V().hasLabel('Member')
  .as('m')
  .where(
    __.in('LOGGED_IN').hasLabel('Device')
      .where(__.values('firstSeen').is(gt(System.currentTimeMillis() - 2592000000L)))
      .count().is(gt(5))
  )
```

**Data available:** ✅ Device.firstSeen, Member, LOGGED_IN edge

---

### 2. **IP Geolocation Jump Patterns**

**Signal:** Same member using IPs from geographically distant locations in succession

```groovy
// Members logging in from 3+ distinct countries in 24 hours
g.V().hasLabel('Member')
  .where(
    __.out('ACCESSED_FROM').hasLabel('IpAddress')
      .values('ipCountryCode')
      .dedup()
      .count().is(gt(2))
  )
```

**Data available:** ✅ IpAddress.ipCountryCode, ACCESSED_FROM edges

---

### 3. **Auth Event Clustering by Challenge Type**

**Signal:** Unusual challenge type distribution (SMS vs Email vs MAGIC_LINK imbalance)

```groovy
// AuthEvents grouped by challenge type per member
g.V().hasLabel('Member')
  .out('HAD_AUTH_EVENT').hasLabel('AuthEvent')
  .groupCount().by('challengeName')
```

**Data available:** ✅ AuthEvent.challengeName, HAD_AUTH_EVENT edges

---

### 4. **Member Status Change Velocity**

**Signal:** Frequent member status transitions (Active→Suspended→Active patterns indicate oscillation attacks)

**Note:** Status is a static property on Member; need historical snapshots or CDC events for this

**Data available:** ❌ Current snapshot only; would need CDC history

---

### 5. **Device Reuse Across Time Zones**

**Signal:** Device used from different geographic regions, impossible timezone jumps

```groovy
// Devices with logins from 3+ distinct countries
g.V().hasLabel('Device')
  .where(
    __.in('LOGGED_IN')
      .outE('LOGGED_IN').values('ipCountryCode').dedup()
      .count().is(gt(2))
  )
```

**Data available:** ✅ Device, ipCountryCode on LOGGED_IN edges

---

### 6. **StepUp Rule Escalation Pattern**

**Signal:** Member triggering increasingly strict step-up rules (high_velocity → high_risk → manual_review)

```groovy
// Members with 3+ distinct StepUpRules triggered
g.V().hasLabel('Member')
  .out('HAD_STEP_UP').hasLabel('StepUpEvent')
  .out('TRIGGERED_RULE').hasLabel('StepUpRule')
  .groupCount().by('ruleName')
  .where(size().is(gt(2)))
```

**Data available:** ✅ StepUpEvent, StepUpRule, HAD_STEP_UP, TRIGGERED_RULE edges

---

### 7. **Ledger Balance Oscillation**

**Signal:** Rapid balance increases followed by withdrawals (inventory juggling fraud)

```groovy
// Members creating & updating ledger rapidly
g.V().hasLabel('Ledger')
  .in('CREATED_LEDGER').hasLabel('Member')
  .where(
    __.out('UPDATED_LEDGER').hasLabel('Ledger')
      .count().is(gt(10))
  )
```

**Data available:** ✅ Ledger, CREATED_LEDGER, UPDATED_LEDGER with previousBalance/newBalance

---

### 8. **Agency Balance Drainage**

**Signal:** AgencyBalance.availableBalance decreasing while credit limit increases

```groovy
// Agencies with balance < 10% of creditLimit (liquidity squeeze)
g.V().hasLabel('AgencyBalance')
  .where(
    __.values('availableBalance').math('_ / values("creditLimit")').is(lt(0.1))
  )
```

**Data available:** ✅ AgencyBalance.availableBalance, AgencyBalance.creditLimit

---

## Summary Table

| Query Type | Count | Runnable | Missing |
|------------|-------|----------|---------|
| Documented fraud patterns | 15 | 12 | 3 |
| Additional detectable patterns | 8+ | 6 (80%) | 2 (CDC history needed) |
| **Total actionable queries** | **23+** | **18 (78%)** | **5 (22%)** |

---

## Recommended Next Steps

### Phase 1: Enable Missing Patterns (High Priority)

1. **Reload HotelItinerary Vertices**
   ```bash
   # Check if load was skipped
   grep -i "hotel" batchjobs/csv_export/run_all_exports.py
   
   # Re-load from SQL with:
   python load_neptune_csv_to_gremlin.py \
     --data-dir ../data/final \
     --files vertices/sql-hotel_itineraries.csv
   ```

2. **Add Booking Edges**
   - Define ETL for BOOKED_WITH_DEVICE, BOOKED_FOR, BOOKED_AT_HOTEL
   - Modify export job to include booking edge generation
   - Re-load Phase 2 (edges)

### Phase 2: Advanced Patterns (Medium Priority)

- Implement Pattern 4, 9, 10 once HotelItinerary is loaded
- Add login time analysis (see separate login-time-analysis.md)

### Phase 3: Historical Analysis (Lower Priority)

- Capture CDC events for Member.status change history
- Enable Pattern 4 (status velocity analysis)
- Implement sliding window queries over historical data

