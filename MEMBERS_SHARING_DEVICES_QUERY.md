# Members Sharing Devices Query

## Query

Find all devices that have more than one member associated with them (shared devices), limited to the first 5 cases.

### Gremlin Query

```gremlin
g.V().hasLabel('Device')
  .where(__.in().hasLabel('Member').count().is(gt(1)))
  .project('deviceId', 'memberCount', 'uniqueMembers')
  .by(__.values('deviceId'))
  .by(__.in().hasLabel('Member').count())
  .by(__.in().hasLabel('Member').values('memberId').dedup().fold())
  .limit(5)
```

### Query Breakdown

| Component | Purpose |
|-----------|---------|
| `g.V().hasLabel('Device')` | Start with all device vertices |
| `.where(__.in().hasLabel('Member').count().is(gt(1)))` | Filter to devices with 2+ incoming Member edges |
| `.project('deviceId', 'memberCount', 'uniqueMembers')` | Create result object with 3 fields |
| `.by(__.values('deviceId'))` | Extract device ID |
| `.by(__.in().hasLabel('Member').count())` | Count total member connections (may have duplicates) |
| `.by(__.in().hasLabel('Member').values('memberId').dedup().fold())` | Get unique member IDs |
| `.limit(5)` | Return only first 5 results |

---

## Results

### Case 1 - Single Member, Multiple Device Uses
```
Device ID:         1a29ffeb-3afe-40e4-89fb-f0e881df0e9c
Total Connections: 4
Unique Members:    1
Members:           [22028]
```
**Interpretation**: Member 22028 is associated with this device 4 times (possibly re-registration or multiple sessions).

---

### Case 2 - True Shared Device
```
Device ID:         858465d1-bc3a-4631-bc2d-7d5d1f724ab1
Total Connections: 2
Unique Members:    2
Members:           [21865, 22628]
```
**Interpretation**: Members 21865 and 22628 both use this device. Potential fraud indicator or shared device (family member, colleague).

---

### Case 3 - Single Member, Multiple Device Uses (High Frequency)
```
Device ID:         28feb958-76e6-451e-8bbc-b29d7725b79c
Total Connections: 5
Unique Members:    1
Members:           [22028]
```
**Interpretation**: Member 22028 is heavily associated with this device (5 connections). 

---

### Case 4 - Single Member, Duplicate Entries
```
Device ID:         36c65370-10f3-417e-8290-c6cb081b2a70
Total Connections: 2
Unique Members:    1
Members:           [21680]
```
**Interpretation**: Member 21680 has 2 edges to this device. May indicate:
- Device re-registration
- Multiple authentication events
- Data duplication

---

### Case 5 - True Shared Device (High Activity)
```
Device ID:         ea752104-1cf4-4904-bccc-4efc3e196dcf
Total Connections: 8
Unique Members:    2
Members:           [13208, 22445]
```
**Interpretation**: Members 13208 and 22445 share this device with 8 total connections.
- **RED FLAG**: This is the highest activity case
- Could indicate shared access or account compromise
- Members 13208 and 22445 may be related (family, team members, or fraudsters)

---

## Key Findings

### Shared Devices Pattern
| Case | Device ID | Members | Type |
|------|-----------|---------|------|
| 1 | 1a29ffeb... | 1 member × 4 | Repeat User |
| 2 | 858465d1... | 2 unique members | **Shared** ✓ |
| 3 | 28feb958... | 1 member × 5 | Repeat User |
| 4 | 36c65370... | 1 member × 2 | Repeat User |
| 5 | ea752104... | 2 members, 8 total | **Shared + High Activity** ⚠️ |

### Observations
- **Only 2 out of 5 cases** have true device sharing (Cases 2 & 5)
- **3 out of 5 cases** show single members with multiple connections (likely re-registration)
- **Case 5** is the most concerning: 2 different members with 8 total connections

---

## Fraud Detection Use Cases

### Use Case 1: Identify Shared Devices
```gremlin
# Find devices shared by 2+ members
g.V().hasLabel('Device')
  .where(__.in().hasLabel('Member').dedup().count().is(gt(1)))
  .project('deviceId', 'memberCount', 'uniqueMembers')
  .by(__.values('deviceId'))
  .by(__.in().hasLabel('Member').dedup().count())
  .by(__.in().hasLabel('Member').values('memberId').dedup().fold())
```

### Use Case 2: Find High-Risk Shared Devices
```gremlin
# Devices with 2+ members AND 5+ total connections
g.V().hasLabel('Device')
  .where(
    __.in().hasLabel('Member').dedup().count().is(gt(1))
    .and(__.in().hasLabel('Member').count().is(gte(5)))
  )
  .project('deviceId', 'memberCount', 'connectionCount', 'members')
  .by(__.values('deviceId'))
  .by(__.in().hasLabel('Member').dedup().count())
  .by(__.in().hasLabel('Member').count())
  .by(__.in().hasLabel('Member').values('memberId').dedup().fold())
```

### Use Case 3: Member Pairs Sharing Devices
```gremlin
# Find pairs of members who share devices
g.V().hasLabel('Device')
  .where(__.in().hasLabel('Member').dedup().count().is(eq(2)))
  .project('deviceId', 'members', 'sharedAuthEvents')
  .by(__.values('deviceId'))
  .by(__.in().hasLabel('Member').values('memberId').dedup().order().fold())
  .by(
    __.in().hasLabel('Member')
    .out().hasLabel('AuthEvent')
    .in().hasLabel('Member')
    .filter(is(neq(original_member)))
    .values('memberId')
    .fold()
  )
```

---

## Recommendations

1. **Monitor Case 5** - Investigate members 13208 and 22445 for potential account takeover or collusion
2. **Device Lifecycle** - Track why single members have multiple connections to same device
3. **Alert Threshold** - Flag devices with 2+ members and 5+ total connections as high-risk
4. **Member Relationships** - Correlate shared devices with other risk factors (same IP, same location, rapid transactions)

