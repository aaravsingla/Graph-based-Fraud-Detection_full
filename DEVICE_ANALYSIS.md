# Device Vertex Analysis Report

## Executive Summary

Your device dataset contains **456 unique devices** with significant data quality issues around fingerprints. Only **26% of devices** have fingerprints recorded, yet those fingerprints show **65.3% duplication**—meaning many different physical devices are marked with identical fingerprints.

---

## Dataset Overview

| Metric | Value |
|--------|-------|
| **Total Devices** | 456 |
| **Data Source** | `batchjobs/data/opensearch/vertices/devices.csv` |
| **Columns** | 11 |
| **Date Range** | 2026-05-20 to 2026-06-24 |

---

## Data Quality Assessment

### Column Fill Rates

| Field | Filled | Percentage | Status |
|-------|--------|-----------|--------|
| `deviceId` | 456/456 | 100% | ✅ Perfect |
| `tbohDeviceId` | 456/456 | 100% | ✅ Perfect |
| `deviceFingerprint` | 118/456 | 26% | ⚠️ **Critical Gap** |
| `deviceType` | 0/456 | 0% | ❌ Missing |
| `os` | 0/456 | 0% | ❌ Missing |
| `browser` | 0/456 | 0% | ❌ Missing |

### Key Observations

1. **Device IDs are fully populated** - Every device has a unique UUID-style deviceId
2. **No enrichment data** - deviceType, os, and browser are completely empty across all devices
3. **Fingerprints are sparse** - Only 118 of 456 devices (26%) have a fingerprint
4. **First/Last seen dates** - All devices have temporal markers (creation and last update timestamps)

---

## Uniqueness Analysis

### Device Identification

| Identifier | Unique Values | Total Records | Duplication |
|------------|---------------|---------------|-------------|
| `deviceId` | 456 | 456 | **0% duplication** ✅ |
| `tbohDeviceId` | 456 | 456 | **0% duplication** ✅ |
| `deviceFingerprint` | 41 | 118 | **65.3% duplication** ⚠️ |

### Fingerprint Duplication Issues

**Critical Finding:** 10 distinct fingerprints are shared across 87 devices.

#### Top Duplicate Fingerprints

| Fingerprint | Devices | Implication |
|-------------|---------|-------------|
| `dad81fe9b86a0c7c6c41e38d0a242dc2` | 66 | **Massive duplication** - 66 unique devices share this fingerprint |
| `9e4db5dac9aa3dabee8c20a0b5490da6` | 3 | 3 unique devices |
| `d026129c71e47a38218f790be69bb401` | 3 | 3 unique devices |
| `8dfd68533877925ffb0687cf1559d418` | 3 | 3 unique devices |
| `a85b42738cc2b25f8632a46b367fd2d6` | 2 | 2 unique devices |

---

## What Makes a Device Vertex Unique?

Based on your current data structure, a device is uniquely identified by:

### Primary Key
- **`deviceId`** (UUID format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
  - Always populated
  - Guaranteed unique
  - Used as the graph vertex ID (prefix: `device:`)

### Current Composition of a Device Vertex

```
Device Vertex = {
  ~id:                   "device:<UUID>"              (Primary identifier)
  ~label:                "Device"                      (Graph label)
  deviceId:              "<UUID>"                      (Unique identifier)
  tbohDeviceId:          "<UUID>"                      (Duplicate of deviceId)
  deviceFingerprint:     "<32-char-hash>"              (Sparse: 26% filled)
  deviceType:            ""                            (EMPTY - Not populated)
  os:                    ""                            (EMPTY - Not populated)
  browser:               ""                            (EMPTY - Not populated)
  firstSeen:             "2026-05-20T10:03:15.061Z"    (ISO timestamp)
  lastSeen:              "2026-06-24T13:33:04.365Z"    (ISO timestamp)
  useCount:              ""                            (EMPTY - Not populated)
}
```

### Optional/Supplementary Fields
- **`deviceFingerprint`** - Device browser fingerprint (MD5/SHA1 hash)
  - Only 26% of devices have this
  - When present, shared across multiple devices (no uniqueness guarantee)
  - Should NOT be used as a unique identifier

---

## Data Entry Logic Issues

### Problems Identified

#### 1. **Fingerprint Entropy Problem**
- **Issue**: One fingerprint appears 66 times across different devices
- **Root Cause**: Either:
  - Fingerprint generation is producing identical hashes for different devices
  - Data export/import error caused fingerprint truncation or collision
  - Multiple browser/device combinations produce the same fingerprint
- **Impact**: Cannot use fingerprint alone to identify unique devices

#### 2. **Redundant ID Fields**
- **Issue**: `deviceId` and `tbohDeviceId` are always identical
- **Recommendation**: Remove `tbohDeviceId` as it's redundant
- **Question**: What does "tboh" prefix mean? (TBOH = "To Be Observed Hospital" or something else?)

#### 3. **Missing Enrichment Attributes**
- **Issue**: `deviceType`, `os`, `browser` are all empty
- **Why it matters**: These would help identify similar devices
- **Current state**: Cannot differentiate between:
  - Mobile vs. Desktop devices
  - iOS vs. Android vs. Windows vs. macOS
  - Chrome vs. Firefox vs. Safari
- **Recommendation**: Populate these fields during data ingestion

#### 4. **useCount Always Empty**
- **Issue**: useCount field is never populated
- **Implication**: Cannot track device usage frequency from vertex properties
- **Workaround**: Must derive from incoming/outgoing edges (relationships to Members/AuthEvents)

---

## Recommended Unique Device Definition

### Strict Uniqueness (Current Implementation)
```
UNIQUE_DEVICE = deviceId
```
- Works perfectly
- 456 devices have 456 unique IDs
- No collisions

### Logical Grouping (With Fingerprint Context)
```
DEVICE_CLUSTER = {
  PRIMARY_KEY: deviceId (always unique)
  FINGERPRINT: deviceFingerprint (may be shared)
  GROUP_ID: fingerprint value (for clustering similar devices)
}
```

This allows you to:
- Keep devices unique by their deviceId (for graph traversal)
- Group similar devices by fingerprint (for analysis)
- Detect shared fingerprints as potential re-inventorying or data collection issues

---

## Sample Devices

### Device WITH Fingerprint
```json
{
  "~id": "device:9e718739-e61f-4690-aab7-590dfe88d2f7",
  "deviceId": "9e718739-e61f-4690-aab7-590dfe88d2f7",
  "deviceFingerprint": "4e41d14538f55d46942cd8a5244afb19",
  "firstSeen": "2026-05-20T10:01:34.135Z",
  "lastSeen": "2026-05-20T10:01:34.135Z"
}
```

### Device WITHOUT Fingerprint
```json
{
  "~id": "device:1a29ffeb-3afe-40e4-89fb-f0e881df0e9c",
  "deviceId": "1a29ffeb-3afe-40e4-89fb-f0e881df0e9c",
  "deviceFingerprint": "",
  "firstSeen": "2026-05-20T10:03:15.061Z",
  "lastSeen": "2026-05-20T10:03:15.061Z"
}
```

---

## Gremlin Query Insights

From your recent query showing devices and associated members:

```
Q3 – Devices and associated Members
g.V().hasLabel('Device')
  .project('deviceId','deviceType','members')
  .by(__.values('deviceId'))
  .by(__.values('deviceType'))
  .by(__.in().hasLabel('Member').values('memberId').fold())
```

**Observation**: The query successfully returns deviceIds and associated members, but `deviceType` always shows empty because the field isn't populated.

---

## Recommendations

### Immediate Actions
1. **Stop using `tbohDeviceId`** - It's redundant; remove from data export
2. **Populate enrichment fields**:
   - Extract `deviceType` from browser fingerprinting libraries (known fingerprints → device types)
   - Extract `os` and `browser` during collection, not just fingerprint
   - Calculate and populate `useCount` based on edge counts

3. **Investigate fingerprint collisions**:
   - Query why 66 devices share `dad81fe9b86a0c7c6c41e38d0a242dc2`
   - Check if fingerprint generation has a bug
   - Verify data ingestion pipeline

### Data Model Improvements
```
Enhanced Device Vertex = {
  ~id:                   "device:<UUID>"
  ~label:                "Device"
  deviceId:              "<UUID>"                 ← Keep as primary key
  deviceFingerprint:     "<hash>"                 ← Optional, for clustering
  fingerprintGroup:      "<hash>"                 ← Group ID for similar devices
  deviceType:            "MOBILE|DESKTOP|TABLET" ← Populate this
  os:                    "iOS|Android|Windows|macOS|Linux"
  browser:               "Chrome|Firefox|Safari|Edge"
  browserVersion:        "<version>"
  firstSeen:             "<ISO timestamp>"
  lastSeen:              "<ISO timestamp>"
  useCount:              <integer>                ← Computed from edges
}
```

### Gremlin Queries to Validate
```gremlin
# Find devices with duplicate fingerprints
g.V().hasLabel('Device')
  .group().by('deviceFingerprint')
  .unfold()
  .select(Keys)
  .filter(is(neq("")))
  .as('fp')
  .filter(__.select('fp').valueMap())
```

---

## Conclusion

**Your device vertices are unique by design** (`deviceId` is 100% unique), but **fingerprint data quality is poor** (26% coverage, 65% duplication). The current structure works for graph traversal but lacks the richness needed for device classification and comparison.

**Action:** Prioritize populating `deviceType`, `os`, and `browser` fields to enable meaningful device analysis in your customer 360 and fraud detection use cases.
