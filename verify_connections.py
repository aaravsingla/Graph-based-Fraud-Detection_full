#!/usr/bin/env python3
"""
Connection verification script for all data sources:
- SQL Server (TBOMena)
- DynamoDB
- OpenSearch
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def load_env() -> None:
    """Load environment variables from .env file."""
    env_path = Path(__file__).parent / ".env"
    
    if not env_path.exists():
        print(f"❌ .env file not found at {env_path}")
        sys.exit(1)
    
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            # Parse KEY=VALUE
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()


def verify_sql_connection() -> bool:
    """Verify SQL Server connection."""
    print("\n" + "=" * 60)
    print("🔍 VERIFYING SQL SERVER CONNECTION")
    print("=" * 60)
    
    try:
        import pyodbc
    except ImportError:
        print("❌ pyodbc not installed. Install with: pip install pyodbc")
        return False
    
    try:
        host = os.getenv("SQL_HOST")
        port = os.getenv("SQL_PORT")
        database = os.getenv("SQL_DATABASE")
        username = os.getenv("SQL_USERNAME")
        password = os.getenv("SQL_PASSWORD")
        driver = os.getenv("SQL_DRIVER", "ODBC Driver 18 for SQL Server")
        encrypt = os.getenv("SQL_ENCRYPT", "yes")
        trust_cert = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "yes")
        
        print(f"Host: {host}")
        print(f"Port: {port}")
        print(f"Database: {database}")
        print(f"Driver: {driver}")
        
        connection_string = (
            f"Driver={{{driver}}};"
            f"Server={host},{port};"
            f"Database={database};"
            f"UID={username};"
            f"PWD={password};"
            f"Encrypt={encrypt};"
            f"TrustServerCertificate={trust_cert};"
        )
        
        conn = pyodbc.connect(connection_string, timeout=10)
        cursor = conn.cursor()
        
        # Test with a simple query
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        print("✅ SQL Server connection: SUCCESSFUL")
        return True
        
    except Exception as e:
        print(f"❌ SQL Server connection: FAILED")
        print(f"Error: {str(e)}")
        return False


def verify_dynamo_connection() -> bool:
    """Verify DynamoDB connection."""
    print("\n" + "=" * 60)
    print("🔍 VERIFYING DYNAMODB CONNECTION")
    print("=" * 60)
    
    try:
        import boto3
    except ImportError:
        print("❌ boto3 not installed. Install with: pip install boto3")
        return False
    
    try:
        region = os.getenv("AWS_REGION", "eu-west-1")
        access_key = os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        session_token = os.getenv("AWS_SESSION_TOKEN")
        
        print(f"Region: {region}")
        print(f"Access Key: {access_key[:10]}..." if access_key else "Access Key: Not set")
        print(f"Session Token: {'Set' if session_token else 'Not set'}")
        
        session = boto3.Session(
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
        )
        
        dynamodb = session.resource("dynamodb", region_name=region)
        
        # List tables as a connectivity test
        table_names = []
        for table in dynamodb.tables.all():
            table_names.append(table.name)
        
        print(f"✅ DynamoDB connection: SUCCESSFUL")
        print(f"   Available tables: {len(table_names)}")
        if table_names:
            print(f"   Sample tables: {', '.join(table_names[:3])}")
        return True
        
    except Exception as e:
        print(f"❌ DynamoDB connection: FAILED")
        print(f"Error: {str(e)}")
        return False


def verify_opensearch_connection() -> bool:
    """Verify OpenSearch connection."""
    print("\n" + "=" * 60)
    print("🔍 VERIFYING OPENSEARCH CONNECTION")
    print("=" * 60)
    
    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
    except ImportError:
        print("❌ opensearch-py not installed. Install with: pip install opensearch-py")
        return False
    
    try:
        import boto3
    except ImportError:
        print("❌ boto3 not installed. Install with: pip install boto3")
        return False
    
    try:
        endpoint = os.getenv("OPENSEARCH_ENDPOINT")
        username = os.getenv("OPENSEARCH_USERNAME")
        password = os.getenv("OPENSEARCH_PASSWORD")
        auth_mode = os.getenv("OPENSEARCH_AUTH_MODE", "basic")
        region = os.getenv("AWS_REGION", "eu-west-1")
        
        print(f"Endpoint: {endpoint}")
        print(f"Auth Mode: {auth_mode}")
        print(f"Region: {region}")
        
        if auth_mode.lower() == "sigv4":
            # Use AWS SigV4 authentication
            credentials = boto3.Session().get_credentials()
            auth = AWSV4SignerAuth(credentials, region, "es")
            
            client = OpenSearch(
                hosts=[{"host": endpoint, "port": 443}],
                http_auth=None,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                auth=auth,
            )
        else:
            # Use basic authentication
            client = OpenSearch(
                hosts=[{"host": endpoint, "port": 443}],
                http_auth=(username, password),
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
            )
        
        # Test connection with info endpoint
        info = client.info()
        version = info.get("version", {}).get("number", "Unknown")
        
        # List indices
        indices = client.indices.get_alias(index="*")
        index_names = list(indices.keys())
        
        print(f"✅ OpenSearch connection: SUCCESSFUL")
        print(f"   Version: {version}")
        print(f"   Available indices: {len(index_names)}")
        if index_names:
            print(f"   Sample indices: {', '.join(index_names[:5])}")
        
        return True
        
    except Exception as e:
        print(f"❌ OpenSearch connection: FAILED")
        print(f"Error: {str(e)}")
        return False


def main() -> None:
    """Run all connection verifications."""
    print("\n" + "=" * 60)
    print("📊 ENVIRONMENT CONNECTION VERIFICATION")
    print("=" * 60)
    
    load_env()
    
    results = {
        "SQL Server": verify_sql_connection(),
        "DynamoDB": verify_dynamo_connection(),
        "OpenSearch": verify_opensearch_connection(),
    }
    
    print("\n" + "=" * 60)
    print("📈 SUMMARY")
    print("=" * 60)
    
    for service, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{service}: {status}")
    
    all_passed = all(results.values())
    
    print("=" * 60)
    if all_passed:
        print("✅ All connections verified successfully!")
        print("🚀 Ready to run export functions")
        sys.exit(0)
    else:
        failed = [svc for svc, success in results.items() if not success]
        print(f"❌ Connection verification failed for: {', '.join(failed)}")
        print("Please fix the issues above before running export functions")
        sys.exit(1)


if __name__ == "__main__":
    main()
