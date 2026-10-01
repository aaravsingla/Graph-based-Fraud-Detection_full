# Synapse - Neptune Graph Data Pipeline

**Synapse** is a comprehensive data pipeline system that extracts, transforms, and loads (ETL) data from multiple sources (SQL Server, DynamoDB, OpenSearch) into Neptune-compatible CSV files for graph database bulk loading. The pipeline supports both local development and AWS Batch/ECS production environments.

## 🎯 Project Overview

Synapse creates a unified view of customer, transaction, and behavioral data by combining:

- **SQL Server**: Core business data (agencies, members, addresses, ledgers, hotel bookings)
- **DynamoDB**: Customer360 data (agency balances, sales coordinators, email preferences, roles)
- **OpenSearch**: Authentication events, device fingerprints, step-up events, hotel metadata

The pipeline outputs Neptune-compatible CSV files with proper graph structure (vertices and edges) ready for bulk loading into Amazon Neptune or local Gremlin servers.

### Key Features

- ✅ **Multi-source ETL**: Parallel extraction from SQL, DynamoDB, and OpenSearch
- ✅ **Neptune-ready output**: Proper `~id`, `~label`, `~from`, `~to` CSV headers with typed properties
- ✅ **Synthetic data generation**: Fill gaps in test/dev environments with realistic synthetic data
- ✅ **Local development support**: Full pipeline runs on developer laptops with proper ODBC setup
- ✅ **AWS production ready**: Supports IAM roles, SigV4 auth, S3 output, and email notifications
- ✅ **Flexible execution**: Run individual sources or complete pipeline with customizable concurrency
- ✅ **Data validation**: Export summaries with row counts and completion reports

## 📊 Data Flow & Architecture

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐
│  SQL Server │    │  DynamoDB    │    │  OpenSearch   │
│             │    │              │    │               │
│ • Agencies  │    │ • Balances   │    │ • Auth Events │
│ • Members   │    │ • Sales Head │    │ • Devices     │
│ • Bookings  │    │ • Roles      │    │ • Hotels      │
│ • Ledgers   │    │ • Permissions│    │ • Step-up     │
└──────┬──────┘    └──────┬───────┘    └───────┬───────┘
       │                  │                    │
       ▼                  ▼                    ▼
┌─────────────────────────────────────────────────────────┐
│               Synapse ETL Pipeline                       │
│                                                         │
│  SQL Export     DynamoDB Export    OpenSearch Export    │
│     │               │                    │              │
│     ▼               ▼                    ▼              │
│  vertices/       vertices/           vertices/          │
│  edges/          edges/              edges/             │
└──────┬──────────────┬──────────────────┬───────────────┘
       │              │                  │
       └──────────────┼──────────────────┘
                      ▼
            ┌─────────────────────┐
            │   Combined Output   │
            │                     │
            │  batchjobs/data/    │
            │    final/           │
            │  ├── vertices/      │
            │  ├── edges/         │
            │  └── load-manifest  │
            └─────────┬───────────┘
                      │
                      ▼
            ┌─────────────────────┐
            │   Neptune/Gremlin   │
            │   Bulk Load Ready   │
            └─────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

**macOS/Linux Requirements:**
```bash
# ODBC drivers for SQL Server connectivity
brew install unixodbc
brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18

# Python 3.8+ (check with python3 --version)
python3 --version
```

**Windows Requirements:**
- Python 3.8+
- Microsoft ODBC Driver 18 for SQL Server
- Git for Windows

### 1. Clone and Setup

```bash
# Clone the repository
git clone https://github.com/TTL-EntArch/Synapse.git
cd Synapse

# Quick setup (creates virtual environment and installs dependencies)
cd batchjobs/csv_export
./run_all.sh --help  # This will create .venv automatically on first run
```

### 2. Configure Environment

```bash
# Copy example configuration
cp batchjobs/csv_export/.env.example batchjobs/csv_export/.env

# Edit configuration for your environment
nano batchjobs/csv_export/.env
```

**Minimal `.env` for local development:**
```env
# Global settings
IS_LOCAL=true
AWS_REGION=eu-west-1

# SQL Server (choose one connection method)
SQL_HOST=your-sql-server.com
SQL_PORT=1433
SQL_DATABASE=TBOMena_Prep
SQL_USERNAME=your-username
SQL_PASSWORD=your-password
SQL_DRIVER=ODBC Driver 18 for SQL Server

# AWS credentials (for DynamoDB/OpenSearch)
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key

# DynamoDB tables
DYNAMO_CUSTOMER360_TABLE=Prep-Customer360
DYNAMO_ROLE_PERMISSIONS_TABLE=Prep-RolePermissions360

# OpenSearch cluster
OPENSEARCH_ENDPOINT=search-your-domain.eu-west-1.es.amazonaws.com
OPENSEARCH_AUTH_MODE=basic
OPENSEARCH_USERNAME=your-opensearch-user
OPENSEARCH_PASSWORD=your-opensearch-password
OPENSEARCH_LOGIN_INDEX=login-prep
OPENSEARCH_STEPUP_INDEX=step-up-dev
OPENSEARCH_HOTEL_INDEX=hotels

# Performance tuning (optional)
EXPORT_WORKERS=8
DYNAMO_SCAN_SEGMENTS=8
OPENSEARCH_SCROLL_SLICES=4
```

### 3. Run Complete Pipeline

```bash
# From repository root - runs everything
./batchjobs/csv_export/run_all.sh

# Or with Python directly
cd batchjobs/csv_export
python run_all_exports.py

# Skip specific sources if needed
./run_all.sh --skip-opensearch  # SQL + DynamoDB only
python run_all_exports.py --skip-sql --skip-dynamo  # OpenSearch only
```

### 4. Verify Output

```bash
# Check export summary
cat batchjobs/data/export-summary.txt

# Inspect final output structure
ls -la batchjobs/data/final/
ls -la batchjobs/data/final/vertices/
ls -la batchjobs/data/final/edges/

# Load order for Neptune
cat batchjobs/data/final/load-manifest.txt
```

## 📁 Project Structure

```
Synapse/
├── README.md                           # This file
├── .gitignore                          # Git ignore rules (includes large data files)
├── batchjobs/
│   ├── csv_export/                     # Main ETL pipeline
│   │   ├── .env.example               # Configuration template  
│   │   ├── requirements.txt           # Python dependencies
│   │   ├── README.md                  # Detailed pipeline documentation
│   │   ├── run_all.sh                 # Shell orchestrator (recommended entry point)
│   │   ├── run_all_exports.py         # Python orchestrator
│   │   │
│   │   ├── sql_export_job.py          # SQL Server → CSV export
│   │   ├── dynamo_export_job.py       # DynamoDB → CSV export  
│   │   ├── opensearch_export_job.py   # OpenSearch → CSV export
│   │   ├── combine_exports_job.py     # Merge all sources → final/
│   │   │
│   │   ├── generate_synthetic_opensearch.py  # Generate test data
│   │   ├── load_neptune_csv_to_gremlin.py    # Load CSVs to Gremlin/Neptune
│   │   ├── opensearch_discover_indices.py    # Find available indices
│   │   │
│   │   └── common/                    # Shared utilities
│   │       ├── coerce.py             # Data type conversion
│   │       ├── csv_writer.py         # Neptune CSV formatting
│   │       ├── concurrency.py        # Threading & parallel execution
│   │       ├── storage.py            # Local/S3 file management
│   │       ├── mail.py               # Email notifications
│   │       └── ...
│   │
│   └── data/                          # Output directory (auto-created)
│       ├── sql/                       # SQL export output
│       ├── dynamo/                    # DynamoDB export output
│       ├── opensearch/                # OpenSearch export output
│       ├── final/                     # Combined Neptune-ready CSVs
│       ├── export-summary.json        # Machine-readable report
│       └── export-summary.txt         # Human-readable summary
│
├── docs/                              # Documentation
├── infrasetup/                        # AWS infrastructure (CloudFormation)
└── lambdas/                          # AWS Lambda functions
```

## ⚠️ Large Files & Git LFS

**Important**: This repository excludes large data files (>50MB) from Git to comply with GitHub's size limits.

### Files Excluded from Git (in `.gitignore`)

The following large data files are automatically excluded and will be regenerated when you run the pipeline:

```bash
# Large data files (>50MB) - use Git LFS or download separately
batchjobs/data/opensearch/vertices/hotels.csv           # ~53MB - Hotel metadata
batchjobs/data/dynamo/vertices/email_preferences.csv    # ~72MB - Email preferences  
batchjobs/data/final/vertices/dynamo-email_preferences.csv  # ~72MB - Final email prefs
batchjobs/data/sql/vertices/hotel_itineraries.csv       # ~51MB - Hotel bookings
batchjobs/data/sql/edges/edges-fraud.csv               # ~116MB - Fraud detection edges
```

### Regenerating Large Files

When you clone this repository on a new system, these files will be missing. To regenerate them:

```bash
# Full pipeline regenerates all files
./batchjobs/csv_export/run_all.sh

# Or regenerate specific sources:
cd batchjobs/csv_export
source .venv/bin/activate

# Regenerate SQL data (includes hotel_itineraries.csv and edges-fraud.csv)
python sql_export_job.py --output-root ../data

# Regenerate DynamoDB data (includes email_preferences.csv)  
python dynamo_export_job.py --output-root ../data

# Regenerate OpenSearch data (includes hotels.csv)
python opensearch_export_job.py --output-root ../data

# Recombine all sources
python combine_exports_job.py \
  --input-roots ../data/sql ../data/dynamo ../data/opensearch \
  --output-root ../data/final
```

## 🔧 Advanced Usage

### Individual Source Exports

```bash
cd batchjobs/csv_export
source .venv/bin/activate

# SQL only (agencies, members, bookings, ledgers + core/fraud edges)
python sql_export_job.py --output-root ../data

# DynamoDB only (Customer360 vertices/edges)
python dynamo_export_job.py --output-root ../data

# OpenSearch only (auth events, devices, hotels)
python opensearch_export_job.py --output-root ../data

# Discover available OpenSearch indices
python opensearch_discover_indices.py

# Generate synthetic test data
python generate_synthetic_opensearch.py --stepup-count 150 --fill-login-gaps 50

# Load to Gremlin server for testing
python load_neptune_csv_to_gremlin.py --data-dir ../data/final --limit 1000
```

### Performance Tuning

Adjust concurrency in `.env` based on your system:

```env
# Laptop (8 cores)
EXPORT_WORKERS=8
DYNAMO_SCAN_SEGMENTS=8
OPENSEARCH_SCROLL_SLICES=4
EXPORT_SOURCES_PARALLEL=false

# Server (16+ cores) 
EXPORT_WORKERS=16
DYNAMO_SCAN_SEGMENTS=16
OPENSEARCH_SCROLL_SLICES=8
EXPORT_SOURCES_PARALLEL=true

# Large batch system (32+ cores)
EXPORT_WORKERS=32
DYNAMO_SCAN_SEGMENTS=32
OPENSEARCH_SCROLL_SLICES=16
EXPORT_SOURCES_PARALLEL=true
```

### S3 Output for Production

```env
# Enable S3 output instead of local files
EXPORT_TO_S3=true
S3_OUTPUT_BUCKET=synapse-neptune-csv-export-prep-696292622193
S3_OUTPUT_PREFIX=neptune-csv-export
EXPORT_RUN_ID=20260608T094514Z
EXPORT_DISCARD_LOCAL=false  # Keep local copies

# Email notifications
TRIGGER_MAIL_ON_COMPLETION=true
SENDGRID_API_KEY=SG.xxx
MAIL_FROM=reports@yourdomain.com
MAIL_TO=data-team@yourdomain.com
```

## 🔬 Development & Testing

### Running on Fresh System

When setting up on a completely new system:

```bash
# 1. Clone repository
git clone https://github.com/TTL-EntArch/Synapse.git
cd Synapse

# 2. Install system dependencies (macOS)
brew install unixodbc
HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18

# 3. Setup Python environment (automatic)
cd batchjobs/csv_export
./run_all.sh  # Creates .venv, installs deps, runs full pipeline

# 4. Configure for your environment
cp .env.example .env
# Edit .env with your credentials and endpoints

# 5. Run pipeline
./run_all.sh
```

### Synthetic Data for Testing

Generate realistic test data when real production data isn't available:

```bash
cd batchjobs/csv_export
source .venv/bin/activate

# Generate synthetic OpenSearch data
python generate_synthetic_opensearch.py --stepup-count 200 --fill-login-gaps 100

# Re-combine with synthetic data
python combine_exports_job.py \
  --input-roots ../data/sql ../data/dynamo ../data/opensearch \
  --output-root ../data/final
```

### Local Graph Testing

Load generated CSVs into local Gremlin for testing:

```bash
# Start local Gremlin server (Docker)
docker run -p 8182:8182 tinkerpop/gremlin-server:latest

# Load subset of data
python load_neptune_csv_to_gremlin.py \
  --data-dir ../data/final \
  --limit 1000 \
  --files vertices/sql-members.csv,vertices/sql-agencies.csv,edges/sql-edges-core.csv

# Dry run (see Gremlin queries without execution)
python load_neptune_csv_to_gremlin.py --data-dir ../data/final --limit 5 --dry-run
```

## 📈 Expected Output

A successful pipeline run produces:

### Data Volume (typical production)
- **Total records**: ~4M rows
- **Vertices**: ~2.2M (members, agencies, hotels, devices, etc.)
- **Edges**: ~1.8M (bookings, associations, auth events, fraud signals)

### File Structure
```
batchjobs/data/final/
├── vertices/                    # All entity CSVs
│   ├── sql-members.csv         # 13,444 rows
│   ├── sql-agencies.csv        # 12,017 rows  
│   ├── sql-hotel_itineraries.csv  # 390,090 rows (LARGE FILE)
│   ├── dynamo-email_preferences.csv  # 522,940 rows (LARGE FILE)
│   ├── opensearch-hotels.csv   # 1,174,741 rows (LARGE FILE)
│   └── ...
├── edges/                      # All relationship CSVs
│   ├── sql-edges-core.csv      # 66,527 rows
│   ├── sql-edges-fraud.csv     # 1,183,508 rows (LARGE FILE)
│   ├── dynamo-edges-customer360.csv  # 563,024 rows
│   └── ...
└── load-manifest.txt           # Load order for Neptune
```

### Summary Reports
- `export-summary.json` - Machine-readable metrics
- `export-summary.txt` - Human-readable summary with timings and row counts

## 🛠️ Troubleshooting

### Common Issues

| Error | Solution |
|-------|----------|
| `No module named 'pyodbc'` | Run `./run_all.sh` (creates venv) or `pip install -r requirements.txt` |
| `Can't open lib 'ODBC Driver 18'` | Install Microsoft ODBC drivers: `brew install msodbcsql18` |
| `index_not_found_exception` | Check OpenSearch indices: `python opensearch_discover_indices.py` |
| `certificate verify failed` | Set `OPENSEARCH_VERIFY_CERTS=false` in `.env` |
| Large files not found after clone | Run full pipeline: `./run_all.sh` (regenerates all data) |
| GitHub push fails with large files | Files >50MB are gitignored - they'll regenerate on new systems |

### Debug Mode

```bash
# Verbose logging
EXPORT_DEBUG=true ./run_all.sh

# Dry run (no actual exports)
python run_all_exports.py --help

# Test connections only
python -c "
import os; os.chdir('batchjobs/csv_export')
from pathlib import Path
exec(Path('.env').read_text())  # Load env vars
# Test SQL connection
import pyodbc; print('SQL OK')
# Test AWS
import boto3; boto3.client('dynamodb', region_name=os.getenv('AWS_REGION')).list_tables()
print('AWS OK')
"
```

## 🤝 Contributing

### Development Setup

```bash
# Fork and clone
git clone https://github.com/your-fork/Synapse.git
cd Synapse

# Create feature branch from dev
git checkout dev
git checkout -b feature/your-feature-name

# Make changes, test locally
./batchjobs/csv_export/run_all.sh

# Commit and push (large files automatically excluded)
git add .
git commit -m "feat: your feature description"
git push -u origin feature/your-feature-name
```

### Code Standards

- **Python**: Follow PEP 8, use type hints, add docstrings
- **Configuration**: Use `.env` for all environment-specific settings
- **Error handling**: Provide actionable error messages
- **Performance**: Consider concurrency impact, add tuning parameters

## 📧 Support & Contact

- **Issues**: [GitHub Issues](https://github.com/TTL-EntArch/Synapse/issues)
- **Documentation**: See `batchjobs/csv_export/README.md` for detailed pipeline docs
- **Architecture**: See `docs/` directory for system design documents

## 📄 License

This project is proprietary software owned by TTL Enterprise Architecture team.

---

**Note**: This README covers the complete Synapse pipeline. For detailed technical documentation of the ETL jobs, see [`batchjobs/csv_export/README.md`](batchjobs/csv_export/README.md).