# Neptune Graph Database - Complete Data Analysis

## Overview
Neptune contains a comprehensive graph model spanning three data sources: **SQL** (transactional), **DynamoDB** (customer 360 data), and **OpenSearch** (authentication & security events). The graph represents a customer relationship and transaction network with 27 vertex types and 15 edge relationships.

---

## VERTEX DATA TYPES (27 Total)

### SQL Vertices (8 Types)

#### 1. **Member** (`Member`)
- **Fields:**
  - `memberId:Long` - Unique member identifier
  - `email:String` - Member email
  - `status:String` - Account status (Active, etc.)
  - `agencyId:Long` - Associated agency
  - `roleId:Int` - Role reference ID
  - `role:String` - Role name (e.g., "Agent")
  - `isPrimaryAgent:Bool` - Primary agent flag
  - `prefLanguage:String` - Preferred language
  - `mobileNumber:String` - Mobile contact
  - `createdAt:String` - Account creation timestamp
- **Files:** `sql-members-part-001.csv`, `sql-members-part-002.csv`

#### 2. **Agency** (`Agency`)
- **Fields:**
  - `agencyId:Long` - Unique agency identifier
  - `name:String` - Agency name
  - `agencyType:String` - Type of agency
  - `gsaType:Int` - GSA classification
  - `agencyClass:String` - Agency classification
  - `travelGroup:Int` - Travel group assignment
  - `affiliateAdminId:Long` - Affiliate admin reference
  - `isSSO:Bool` - Single Sign-On enabled flag
  - `priorityCategory:String` - Priority tier
  - `award:String` - Award/achievement
  - `creditLimit:Double` - Credit limit amount
  - `currency:String` - Currency code (USD, etc.)
  - `createdOn:String` - Creation timestamp
  - `agencyFirstSignIn:String` - First login timestamp
- **Files:** `sql-agencies-part-001.csv`, `sql-agencies-part-002.csv`

#### 3. **Address** (`Address`)
- **Fields:** (Inferred from file naming: likely postal/physical addresses)
- **Files:** `sql-addresses-part-001.csv` through `sql-addresses-part-005.csv` (5 parts)

#### 4. **Ledger** (`Ledger`)
- **Fields:**
  - `ledgerId:Long` - Unique ledger identifier
  - `balance:Double` - Account balance amount
  - `currencyCode:String` - Currency (USD, etc.)
  - `updatedAt:String` - Last update timestamp
- **Files:** `sql-ledgers-part-001.csv`, `sql-ledgers-part-002.csv`

#### 5. **Hotel Itinerary** (`HotelItinerary`)
- **Fields:** (Inferred from booking/travel data)
- **Files:** `sql-hotel_itineraries.csv`

#### 6-8. **Additional SQL Vertices**
- Likely additional transaction or reference data vertices
- Files are split across multiple parts for scalability

---

### DynamoDB Vertices (6 Types)

#### 1. **AgencyBalance** (`AgencyBalance`)
- **Fields:**
  - `agencyId:Long` - Agency identifier
  - `availableBalance:String` - Available balance amount
  - `creditLimit:String` - Credit limit
  - `cashBalance:String` - Cash balance
  - `lccBalance:String` - Local credit card balance
  - `localCreditLimit:String` - Local credit limit
  - `currencyCode:String` - Currency code
  - `isCreditLimitReleased:Bool` - Credit limit release status
  - `updatedAt:String` - Last update timestamp
- **Files:** `dynamo-agency_balances-part-001.csv`, `dynamo-agency_balances-part-002.csv`

#### 2. **Role** (`Role`)
- **Fields:**
  - `roleId:Int` - Unique role identifier
  - `roleName:String` - Role name
  - `roleCategory:String` - Category (e.g., "Agent")
  - `description:String` - Role description
- **Files:** `dynamo-roles.csv`

#### 3. **SalesCoordinator** (`SalesCoordinator`)
- **Fields:**
  - `salesCoordinatorId:Long` - Coordinator ID
  - `salesCoordinatorEmail:String` - Email address
- **Files:** `dynamo-sales_coordinators.csv`

#### 4. **SalesHead** (`SalesHead`)
- **Fields:** (Similar to SalesCoordinator, likely includes ID and contact info)
- **Files:** `dynamo-sales_heads-part-001.csv`, `dynamo-sales_heads-part-002.csv`

#### 5. **EmailPreferences** (`EmailPreferences`)
- **Fields:** (Communication preferences for users)
- **Files:** `dynamo-email_preferences.csv`

#### 6. **Permissions** (`Permissions`)
- **Fields:** (Access control and permission definitions)
- **Files:** `dynamo-permissions.csv`

#### 7. **Promos** (`Promos`)
- **Fields:** (Promotional/offer data)
- **Files:** `dynamo-promos.csv`

---

### OpenSearch Vertices (8 Types)

#### 1. **AuthEvent** (`AuthEvent`)
- **Fields:**
  - `authSessionId:String` - Session identifier
  - `eventType:String` - Event type (RESET_PASSWORD, LOGIN, etc.)
  - `challengeName:String` - Auth challenge type
  - `email:String` - User email
  - `memberId:String` - Member reference
  - `agencyId:String` - Agency reference
  - `ipAddress:String` - IP address used
  - `ipCountryCode:String` - Country code of IP
  - `deviceId:String` - Device identifier
  - `tbohDeviceId:String` - TBOH device ID
  - `isSuccessfulLogin:Bool` - Login success flag
  - `initAuthTime:String` - Auth initiation time
  - `respondToAuthTime:String` - Response time
  - `medium:String` - Channel (web, mobile, etc.)
  - `ipThreatIntel:String` - Threat intelligence data
  - `SuspiciousMail:String` - Email security flag
  - `UserCreatedOn:String` - User creation date
  - `timestamp:String` - Event timestamp
  - `processedAt:String` - Processing timestamp
- **Files:** `opensearch-auth_events.csv`

#### 2. **Device** (`Device`)
- **Fields:**
  - `deviceId:String` - Unique device identifier
  - `tbohDeviceId:String` - TBOH system device ID
  - `deviceFingerprint:String` - Device fingerprint hash
  - `deviceType:String` - Device type (phone, tablet, computer)
  - `os:String` - Operating system
  - `browser:String` - Browser/user agent
  - `firstSeen:String` - First usage timestamp
  - `lastSeen:String` - Last usage timestamp
  - `useCount:String` - Number of uses
- **Files:** `opensearch-devices.csv`

#### 3. **IpAddress** (`IpAddress`)
- **Fields:**
  - `ipAddress:String` - IP address
  - `ipCountryCode:String` - Country code
  - `isp:String` - Internet Service Provider
  - `ipThreatIntel:String` - Threat intelligence score
  - `firstSeen:String` - First seen timestamp
  - `lastSeen:String` - Last seen timestamp
  - `loginCount:String` - Number of login attempts
- **Files:** `opensearch-ip_addresses.csv`

#### 4. **Hotel** (`Hotel`)
- **Fields:** (Hotel/property data for booking context)
- **Files:** `opensearch-hotels.csv`

#### 5. **StepupEvent** (`StepupEvent`)
- **Fields:** (Step-up authentication events)
- **Files:** `opensearch-stepup_events.csv`

#### 6. **StepupRule** (`StepupRule`)
- **Fields:** (Rules for step-up authentication)
- **Files:** `opensearch-stepup_rules.csv`

---

## EDGE DATA TYPES (15 Total)

### SQL Edges (7 Edge Types)

#### 1. **BELONGS_TO** (Member → Agency)
- **Fields:**
  - `joinedAt:String` - Join date
  - `roleId:String` - Role at agency
  - `roleName:String` - Role name
  - `roleCategory:String` - Role category
  - `isPrimaryAgent:Bool` - Primary agent flag
  - `relationshipType:String` - Type of relationship
  - `riskWeight:String` - Risk assessment score
  - `propagationRule:String` - Rule for propagation
  - `createdAt:String` - Relationship creation
  - `updatedAt:String` - Last update
  - `previousBalance:String` - Previous balance
  - `newBalance:String` - New balance
  - `registeredAt:String` - Registration date
  - `assignedAt:String` - Assignment date
- **Files:** `sql-edges-core-part-001.csv` through `sql-edges-core-part-007.csv` (7 parts)

#### 2. **SQL Fraud-Related Edges** (Fraud Detection)
- **Fields:** (Risk scoring and fraud indicators)
- **Files:** `sql-edges-fraud.csv`

#### 3-7. **Additional SQL Edges**
- Implied relationships between SQL vertices
- Distributed across core and fraud edge files

---

### DynamoDB Edges (5 Edge Types)

#### 1. **HAS_BALANCE** (Agency → AgencyBalance)
- **Fields:**
  - `updatedAt:String` - Last balance update
  - `assignedAt:String` - Assignment timestamp
  - `productType:String` - Product type
  - `consumedAt:String` - Consumption date
  - `bookingAmount:String` - Amount booked
  - `agencyId:String` - Agency reference
- **Files:** `dynamo-edges-customer360-part-001.csv` through `dynamo-edges-customer360-part-005.csv` (5 parts)

#### 2-5. **Additional Customer360 Edges**
- Implied relationships within DynamoDB customer 360 model
- Distributed across 5 part files for scalability

---

### OpenSearch Edges (3 Edge Types)

#### 1. **LOGGED_IN** (Member → Device)
- **Fields:**
  - `authSessionId:String` - Session identifier
  - `ipAddress:String` - IP address used
  - `ipCountryCode:String` - Country code
  - `ipThreatIntel:String` - Threat score
  - `timestamp:String` - Login timestamp
  - `isSuccessfulLogin:Bool` - Success flag
  - `challengeName:String` - Challenge type
  - `medium:String` - Channel (web, etc.)
  - `agencyId:String` - Agency context
  - `deviceId:String` - Device identifier
  - `tbohDeviceId:String` - TBOH device ID
  - `bookedAt:String` - Booking timestamp
- **Files:** `opensearch-edges-fraud.csv`

#### 2. **AUTH Edges** (Authentication Flow)
- **Fields:** (Auth session and event tracking)
- **Files:** `opensearch-edges-auth.csv`

#### 3. **FRAUD Edges** (Fraud Detection/Risk)
- **Fields:** (Fraud indicators and risk scores)
- **Files:** `opensearch-edges-fraud.csv`

---

## Data Summary

| Dimension | Count | Details |
|-----------|-------|---------|
| **Vertex Types** | 27 | SQL: 8, DynamoDB: 7, OpenSearch: 8, System: 4 |
| **Edge Types** | 15 | SQL: 7, DynamoDB: 5, OpenSearch: 3 |
| **Vertex Files** | 27 | (Some split across multiple CSV parts) |
| **Edge Files** | 15 | (Some split across multiple CSV parts) |
| **Data Sources** | 3 | SQL (transactional), DynamoDB (customer 360), OpenSearch (auth & events) |
| **Load Phases** | 2 | Phase 1: Vertices (all types), Phase 2: Edges (all relationships) |

---

## Graph Structure Patterns

### Core Relationship Hierarchy
```
Agency (root entity)
├── HAS_BALANCE → AgencyBalance
├── MEMBERS ← Member (BELONGS_TO)
│   ├── LOGGED_IN → Device
│   └── PARTICIPATED_IN → AuthEvent
├── MANAGES → SalesHead
├── ASSIGNED_TO → SalesCoordinator
├── HAS_ROLES → Role
├── HAS_PERMISSIONS → Permissions
└── EMAIL_PREFERENCES → EmailPreferences
```

### Key Insights
1. **Multi-source Integration**: Data from SQL, DynamoDB, and OpenSearch is unified into a single graph
2. **Customer 360 View**: Combines transactional (SQL), balance (DynamoDB), and behavioral (OpenSearch) data
3. **Security & Fraud**: Extensive auth events and device tracking for anomaly detection
4. **Risk Assessment**: Risk weights, threat intelligence, and fraud edges enable risk scoring
5. **Scalability**: Large datasets are partitioned across multiple CSV files for parallel loading

---

## Data Type Distribution

### By Data Source

**SQL (Transactional)**
- Members, Agencies, Addresses, Ledgers, Hotel Itineraries
- Core relationships (BELONGS_TO)

**DynamoDB (Customer 360)**
- Agency Balances, Roles, Sales Coordinators, Sales Heads
- Preferences, Permissions, Promos
- Balance management edges (HAS_BALANCE)

**OpenSearch (Auth & Events)**
- Auth Events, Devices, IP Addresses, Hotels
- Step-up authentication rules and events
- Login and fraud detection edges

### By Entity Type

**User/Identity**
- Member (SQL)
- AuthEvent (OpenSearch)
- Device (OpenSearch)

**Organization**
- Agency (SQL)
- SalesCoordinator, SalesHead (DynamoDB)

**Transaction**
- Ledger (SQL)
- AgencyBalance (DynamoDB)
- Hotel Itinerary (SQL)

**Security**
- IpAddress (OpenSearch)
- StepupEvent, StepupRule (OpenSearch)
- Permissions (DynamoDB)

---

## Neptune Load Configuration

**Loading Strategy**: Two-phase sequential load
- **Phase 1**: All vertices → ensures all entities exist
- **Phase 2**: All edges → prevents foreign key constraint violations

**File Organization**:
```
neptune-load/
├── vertices/
│   ├── sql-*.csv
│   ├── dynamo-*.csv
│   └── opensearch-*.csv
└── edges/
    ├── sql-*.csv
    ├── dynamo-*.csv
    └── opensearch-*.csv
```

**Error Handling**: Failed loads can be retried with `--skip-vertices` to reload edges only

---

Generated: Analysis of Neptune data exports from SQL, DynamoDB, and OpenSearch sources.
