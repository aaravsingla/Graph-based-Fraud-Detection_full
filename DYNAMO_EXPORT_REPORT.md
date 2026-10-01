# DynamoDB Export — Data Quality Report

**Run ID:** 20260624T140021Z  
**Export duration:** 45.9 seconds  
**Source tables:** `DYNAMO_CUSTOMER360_TABLE`, `DYNAMO_ROLE_PERMISSIONS_TABLE`  
**Extraction method:** Parallel DynamoDB scan (up to 16 segments)  
**Prepared by:** Synapse Pipeline Analysis

---

## 1. What Is Being Extracted

DynamoDB uses a single-table design where all entity types live in one table, identified
by `pk` (partition key) and `sk` (sort key) patterns. The exporter reads the entire table
and routes each item to the correct vertex/edge type based on those patterns.

**7 vertex types + 1 edge file are produced:**

| File | Rows | What It Represents |
|---|---|---|
| `agency_balances` | **12,113** | Financial balance per agency |
| `sales_heads` | **11,482** | Internal TBO employees managing agencies |
| `sales_coordinators` | **3,240** | Coordinator-level staff per agency |
| `email_preferences` | **890** | Per-member email notification settings |
| `permissions` | **591** | Permission definitions per product |
| `roles` | **142** | Role definitions |
| `promos` | **2** | Promotional codes consumed by members |
| `edges-customer360` | **41,276** | All DynamoDB relationships combined |

---

## 2. Field-Level Data Quality

### 2.1 Agency Balances (12,113 rows)

The most used entity — every agency has exactly one balance record.

| Field | Filled | Fill Rate | Notes |
|---|---|---|---|
| `~id` | 12,113 | 100% | ✅ |
| `agencyId` | 12,113 | 100% | ✅ |
| `availableBalance` | 12,113 | 100% | ✅ (but see below) |
| `isCreditLimitReleased` | 12,113 | 100% | ✅ (mostly false) |
| `creditLimit` | **0** | **0%** | ❌ Always empty |
| `cashBalance` | **0** | **0%** | ❌ Always empty |
| `lccBalance` | **0** | **0%** | ❌ Always empty |
| `localCreditLimit` | **0** | **0%** | ❌ Always empty |
| `updatedAt` | **0** | **0%** | ❌ Always empty |
| `currencyCode` | **0** | **0%** | ❌ Always empty |

**Key finding — availableBalance breakdown:**
- `0` (zero balance): **7,896 agencies (65.2%)**
- Non-zero balance: **4,217 agencies (34.8%)**
- Empty: **0**

> **Data quality concern:** 5 out of 10 balance fields are completely empty across all
> 12,113 rows. The DynamoDB items simply don't carry these fields. The `AgencyBalance`
> vertex in the graph is therefore only useful for `availableBalance` and
> `isCreditLimitReleased`. Any graph query expecting `creditLimit` or `cashBalance` from
> this vertex will always return null. The SQL `Ledger` vertex carries `balance` and
> `currencyCode` and may be a better source for financial queries.

---

### 2.2 Sales Heads (11,482 rows → 239 unique individuals)

**Critical structural note:** The 11,482 rows do not represent 11,482 people.
The same sales head is written once per agency they manage — so `saleshead:10423`
appears 5,107 times in the vertex file (once per managed agency). There are only
**239 unique sales head IDs** in the dataset.

> **Implication:** The Neptune graph will have one `SalesHead` vertex per unique ID
> (Neptune deduplicates on `~id`), but the vertex file has massive row duplication.
> This is intentional — the deduplication is Neptune's job — but the raw CSV is 48x
> larger than the actual graph node count.

| Field | Fill Rate | Notes |
|---|---|---|
| `salesHeadMemberId` | 100% | ✅ |
| `salesHeadEmail` | 99.6% | ✅ (44 rows missing) |
| `salesHeadCountry` | 100% | ✅ |
| `salesHeadContactNumber` | 95.2% | ✅ (549 rows missing) |

**Sales head coverage across agencies:**
- Agencies with a sales head assigned: **11,480 / 12,099 (94.9%)**
- Agencies with no sales head: **619 (5.1%)**

---

### 2.3 Sales Coordinators (3,240 rows → 289 unique individuals)

Same duplication pattern as sales heads. 289 unique coordinators across 3,240 assignments.

| Field | Fill Rate | Notes |
|---|---|---|
| `salesCoordinatorId` | 100% | ✅ |
| `salesCoordinatorEmail` | 85.4% | ⚠️ 474 rows missing email |

**Coordinator coverage across agencies:**
- Agencies with a coordinator: **3,240 / 12,099 (26.8%)**
- Agencies with no coordinator: **8,859 (73.2%)**

> **Expected or concern?** Coordinators are clearly a secondary tier — not every agency
> has one. The 26.8% coverage could be intentional (coordinators only for active/premium
> accounts) or it could mean the coordinator assignment process is incomplete.
> Worth confirming with the business team.

---

### 2.4 Roles (142 rows)

| Field | Fill Rate | Notes |
|---|---|---|
| `roleId` | 100% | ✅ |
| `roleName` | **0%** | ❌ All empty |
| `roleCategory` | **0%** | ❌ All empty |
| `description` | **0%** | ❌ All empty |

> **Data quality issue:** Every role vertex is a stub — only the `roleId` exists.
> No human-readable name, no category, no description. The source DynamoDB table
> (`DYNAMO_ROLE_PERMISSIONS_TABLE`) stores role-permission mappings (`Role#2 → PID#Hotel#ViewBooking`)
> but does not store the role's metadata.  
>
> **Impact on the graph:** Any Gremlin query like "show me all members with the 'Admin'
> role" cannot be answered from this data. You'd have to join back to the SQL `Role`
> table which has the names. The graph's role vertices are currently decoration only.

---

### 2.5 Permissions (591 rows)

| Field | Fill Rate | Notes |
|---|---|---|
| `permissionId` | 100% | ✅ |
| `productType` | 100% | ✅ |
| `taskName` | **0%** | ❌ All empty |
| `description` | **0%** | ❌ All empty |

**All 591 permissions are for a single product type: `Hotel`.**

> **Data quality issue:** Same problem as roles — the metadata fields are absent.
> The DynamoDB source only has IDs and product types, not the actual permission names.
> More importantly: only the `Hotel` product type is present, despite email preferences
> showing 9 product types (Hotels, Flights, Car Rental, Cruise, Transfers, Umrah,
> Sightseeing, Eurorail, Growth). The role-permission mapping for non-Hotel products
> is either in a different table or was never populated in DynamoDB.

---

### 2.6 Email Preferences (890 rows)

The best-quality entity in the DynamoDB export.

| Field | Fill Rate | Notes |
|---|---|---|
| `memberId` | 100% | ✅ |
| `productType` | 100% | ✅ |
| `emailType` | 100% | ✅ |
| `sendToBooker` | 100% | ✅ |
| `sendToAdmin` | 100% | ✅ |
| `sendToFinance` | 100% | ✅ |
| `isActive` | 100% | ✅ |
| `createdAt` | 100% | ✅ |
| `agencyId` | 94.7% | ✅ (47 rows missing) |
| `isDefaultEntry` | 94.6% | ✅ (48 rows missing) |

**All 890 rows have `isActive = true`.** There are no inactive preferences in the export.
This may mean: inactive preferences are deleted from DynamoDB rather than flagged, or the
export only captures active ones by coincidence of the data window.

**Product type breakdown:**
| Product | Rows |
|---|---|
| HOTELS | 171 |
| UMRAH | 165 |
| CRUISE | 120 |
| TRANSFERS | 105 |
| FLIGHTS | 104 |
| SIGHTSEEING | 75 |
| CARRENTAL | 60 |
| EURORAIL | 45 |
| GROWTH | 45 |

---

### 2.7 Promos (2 rows)

```
promo:8  | promoCode=8  | type=''  | discount=0  | validFrom=''  | validTo=''  | maxUsage=0
promo:9  | promoCode=9  | type=''  | discount=0  | validFrom=''  | validTo=''  | maxUsage=0
```

> **Effectively empty data.** Only 2 promo records exist, both with numeric IDs (`8` and `9`)
> that look like test/placeholder entries. All metadata fields are blank — no type, no
> discount value, no validity dates. The `CONSUMED_PROMO` edges link to these two promos:
> - `member:10601 → promo:8`
> - `member:10595 → promo:8`
> - `member:13261 → promo:9`
> - `member:10561 → promo:9`
>
> Critically, **all 4 CONSUMED_PROMO edges also have every property empty** — no
> `consumedAt`, no `bookingAmount`, no `agencyId`. The promo feature in the graph is
> non-functional with this data.

---

## 3. Edge Quality

### 3.1 Edge Type Distribution (41,276 total)

| Edge Type | Count | % |
|---|---|---|
| `ROLE_PERMITS` | 13,019 | 31.5% |
| `HAS_BALANCE` | 12,113 | 29.3% |
| `MANAGED_BY_SALESHEAD` | 11,482 | 27.8% |
| `MANAGED_BY_COORDINATOR` | 3,240 | 7.8% |
| `HAS_EMAIL_PREF` | 890 | 2.2% |
| `COORDINATOR_IS_MEMBER` | 289 | 0.7% |
| `SALESHEAD_IS_MEMBER` | 239 | 0.6% |
| `CONSUMED_PROMO` | **4** | **0.01%** |

### 3.2 Edge Property Fill Rates

The edge schema has 6 property columns beyond the 4 system columns (`~id, ~from, ~to, ~label`):

| Property | Filled | Fill Rate | Only used by |
|---|---|---|---|
| `productType` | 13,019 | 31.5% | `ROLE_PERMITS` edges |
| `updatedAt` | **0** | **0%** | Nothing |
| `assignedAt` | **0** | **0%** | Nothing |
| `consumedAt` | **0** | **0%** | Nothing |
| `bookingAmount` | **0** | **0%** | Nothing |
| `agencyId` | **0** | **0%** | Nothing |

> **5 out of 6 edge property columns are completely empty across all 41,276 rows.**
> The schema was designed to accommodate all edge types in one file, but the source data
> doesn't populate the time-based fields. This means you cannot query "when was this
> agency assigned this sales head" or "when was this promo consumed" from the graph.

### 3.3 Dangling Edge Drop at Combine Stage

| Metric | Value |
|---|---|
| Edges before combine | 41,276 |
| Edges after combine (final/) | 41,131 |
| Dropped as dangling | **145 (0.4%)** |

Breakdown of what was dropped:
- `HAS_BALANCE`: 14 edges dropped (agencies in Dynamo but not in SQL export)
- `MANAGED_BY_SALESHEAD`: 2 dropped
- `HAS_EMAIL_PREF`: 118 dropped (members in Dynamo but not in SQL export)

> The 118 dropped `HAS_EMAIL_PREF` edges are notable — these are members who have email
> preferences in DynamoDB but no corresponding vertex in the SQL member export. This could
> mean those members were deleted from SQL or fall outside the SQL query window.

---

## 4. Mock / Test Data Findings

This is the most significant data quality issue in the DynamoDB export.

### 4.1 Sales Head Emails — Predominantly Fake

| Email | Occurrences | Type |
|---|---|---|
| `gsdghfgf@tbo.com` | 7,125 | 🔴 Gibberish/test email |
| `administrator@yopmail.com` | 3,062 | 🔴 Yopmail (disposable address) |
| `neha.sharma+2@tbo.com` | 437 | 🟡 Possibly real but test-tagged |
| `mittalyash394@gmail.com` | 103 | 🟡 Personal Gmail |
| `yash.mittal@tbo.com` | 93 | 🟢 Looks real |

**62.1% of all SalesHead rows** use the gibberish email `gsdghfgf@tbo.com`.  
**28.2% of all SalesHead rows** use `@yopmail.com` addresses (disposable email service).

In total: **~90% of SalesHead row-assignments have clearly test/fake emails.**

Since one person can manage thousands of agencies, the actual number of fake
*individuals* is smaller — but the dominant `saleshead:10423` (5,107 agencies)
and `saleshead:1` (3,062 agencies) use `gsdghfgf@tbo.com` and
`administrator@yopmail.com` respectively, meaning **roughly 2/3 of all agency
sales assignments point at two placeholder accounts.**

### 4.2 Sales Coordinator Emails — Same Pattern

| Metric | Value |
|---|---|
| Rows using `gsdg*` emails | 2,016 / 3,240 (62.2%) |
| Rows using `@yopmail.com` | 317 / 3,240 (9.8%) |
| Combined mock/test rows | ~72% |

The top coordinator (`coordinatorId=4007`, 559 agencies) also uses a test email.

### 4.3 Summary of Mock Data Impact

| Entity | Real data | Likely mock/test |
|---|---|---|
| SalesHead individuals | ~35–40 (est.) | ~199 of 239 unique IDs (~83%) |
| Agency-SalesHead assignments | ~1,500 (est.) | ~9,982 (87%) |
| SalesCoordinator individuals | ~80 (est.) | ~209 of 289 (~72%) |
| Promos | 0 | 2 (100%) |

---

## 5. Cross-Source Consistency

| Check | Result |
|---|---|
| Agencies in SQL: 12,099 | Agencies in DynamoDB: 12,113 | ⚠️ 14 extra in Dynamo |
| All SQL agencies have a Dynamo balance record | ✅ 12,099/12,099 |
| 14 Dynamo balance records have no SQL agency | ⚠️ These 14 will have dangling `HAS_BALANCE` edges dropped |
| All SALESHEAD_IS_MEMBER edges resolve | ✅ 239/239 linked |
| COORDINATOR_IS_MEMBER edges resolve | ✅ 289/289 linked |

The 14 agencies present in DynamoDB but absent from SQL are most likely agencies that
were deleted from SQL or created in DynamoDB before being fully provisioned in SQL.

---

## 6. What the Graph Can and Cannot Do with This Data

| Use Case | Possible? | Reason |
|---|---|---|
| Find agency's current available balance | ✅ | `availableBalance` is populated |
| Find agency's credit limit | ❌ | `creditLimit` always empty |
| Find who manages an agency (by person) | ⚠️ | Works, but 87% of assignments are test data |
| Find agency's coordinator | ⚠️ | Only 27% of agencies have one |
| Find what permissions a role grants | ⚠️ | Role-permission links exist but names are blank |
| Answer "which role allows hotel cancellations?" | ❌ | `taskName` always empty |
| Track promo usage per member | ❌ | Only 4 edges, all metadata empty, promos are stubs |
| Find member's email notification settings | ✅ | Best quality data in the export |
| When was this sales head assigned? | ❌ | `assignedAt` always empty |

---

## 7. Recommendations

1. **Sales Head / Coordinator mock data** — The `gsdghfgf@tbo.com` account and
   `administrator@yopmail.com` account together control ~8,000 agency assignments.
   Before using this for any CRM or fraud analysis, confirm with the product team
   whether these are placeholder staging entries or whether real assignments were
   never migrated. If this is production data, it's a data integrity problem in the
   source DynamoDB table itself.

2. **Role and Permission metadata** — The role-permission graph is currently
   structurally present but semantically empty. If the team wants to use Neptune
   for access control queries ("who has permission X?"), role names and task names
   need to be populated. The SQL `Role` table likely has these and could be joined
   during export.

3. **Balance fields** — `creditLimit`, `cashBalance`, `lccBalance`, `updatedAt`,
   `currencyCode` are never populated. Either the DynamoDB table doesn't store these
   (they live only in SQL Ledger), or the DynamoDB item structure uses different
   field names than what the exporter expects. Worth checking the raw DynamoDB item
   schema against the exporter's field mapping.

4. **Promo data** — The 2-row promo export with all-empty metadata is not usable.
   Either the promo catalog lives in a different table/service, or the `ConsumedSalesPromo`
   DynamoDB items don't carry catalog details (only the event). Consider whether
   promo vertices add any value to the graph in their current state.

5. **Edge timestamps** — `assignedAt`, `updatedAt`, `consumedAt` are never populated
   in any edge. Time-based graph queries ("agencies whose sales head changed this month")
   are impossible. This should be investigated at the DynamoDB item level.

6. **Permissions scope** — Only `Hotel` product permissions are extracted. If the
   role-permission table covers other products, confirm whether the `DYNAMO_ROLE_PERMISSIONS_TABLE`
   env var is pointing to the right table, or whether other product permissions are
   in separate tables.
