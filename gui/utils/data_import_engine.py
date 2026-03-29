"""
Dirracuda - Data Import Engine

Centralized data import system supporting CSV/JSON formats for team collaboration.
Provides database write capabilities with conflict resolution and data validation.

Design Decision: Centralized import engine ensures data integrity and provides
consistent import behavior across all components. Handles the team collaboration
workflow where colleagues export data and share it for import.
"""

import csv
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Callable, Tuple
import tempfile
import os
import hashlib

from shared.config import normalize_db_timestamp

_TS_FIELDS = frozenset({
    "first_seen", "last_seen", "created_at", "updated_at",
    "last_updated", "last_accessed",
    "test_timestamp", "last_verified_at", "last_probe_at",
})


class DataImportEngine:
    """
    Centralized data import engine for Dirracuda.
    
    Handles importing data from CSV/JSON formats with database write capabilities.
    Provides validation, conflict resolution, and progress feedback for team
    collaboration workflows.
    """
    
    def __init__(self, db_path: str):
        """
        Initialize the data import engine.
        
        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        
        # Import modes
        self.import_modes = {
            'merge': 'Add new records, update existing ones',
            'replace': 'Replace all existing records of this type',
            'append': 'Add new records only, skip existing ones'
        }
        
        # Database schemas for different data types
        self.db_schemas = {
            'servers': {
                'table': 'servers',
                'key_fields': ['ip_address'],
                'required_fields': ['ip_address', 'country', 'auth_method'],
                'optional_fields': ['country_code', 'accessible_shares', 'vulnerabilities', 
                                   'last_seen', 'scan_count', 'status', 'port', 'os_version'],
                'sql_create': """
                    CREATE TABLE IF NOT EXISTS servers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ip_address TEXT NOT NULL UNIQUE,
                        country TEXT NOT NULL,
                        country_code TEXT,
                        auth_method TEXT NOT NULL,
                        accessible_shares INTEGER DEFAULT 0,
                        vulnerabilities INTEGER DEFAULT 0,
                        last_seen DATETIME,
                        scan_count INTEGER DEFAULT 1,
                        status TEXT DEFAULT 'active',
                        port INTEGER DEFAULT 445,
                        os_version TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """
            },
            'vulnerabilities': {
                'table': 'vulnerabilities',
                'key_fields': ['server_ip', 'vulnerability_type', 'description'],
                'required_fields': ['server_ip', 'severity', 'vulnerability_type', 'description'],
                'optional_fields': ['first_seen', 'last_updated', 'status', 'cve_id', 
                                   'remediation', 'details', 'affected_services'],
                'sql_create': """
                    CREATE TABLE IF NOT EXISTS vulnerabilities (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_ip TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        vulnerability_type TEXT NOT NULL,
                        description TEXT NOT NULL,
                        first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                        last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                        status TEXT DEFAULT 'open',
                        cve_id TEXT,
                        remediation TEXT,
                        details TEXT,
                        affected_services TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (server_ip) REFERENCES servers (ip_address)
                    )
                """
            },
            'shares': {
                'table': 'shares',
                'key_fields': ['server_ip', 'share_name'],
                'required_fields': ['server_ip', 'share_name', 'access_level'],
                'optional_fields': ['description', 'last_accessed', 'file_count', 
                                   'size_mb', 'permissions', 'share_type'],
                'sql_create': """
                    CREATE TABLE IF NOT EXISTS shares (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_ip TEXT NOT NULL,
                        share_name TEXT NOT NULL,
                        access_level TEXT NOT NULL,
                        description TEXT,
                        last_accessed DATETIME,
                        file_count INTEGER DEFAULT 0,
                        size_mb REAL DEFAULT 0,
                        permissions TEXT,
                        share_type TEXT DEFAULT 'disk',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (server_ip) REFERENCES servers (ip_address),
                        UNIQUE(server_ip, share_name)
                    )
                """
            }
        }
    
    def import_data(self, file_path: str, data_type: str, import_mode: str = 'merge',
                   validate_only: bool = False, 
                   progress_callback: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
        """
        Import data from file with validation and conflict resolution.
        
        Args:
            file_path: Path to import file (CSV, JSON, or ZIP)
            data_type: Type of data (servers, vulnerabilities, shares)
            import_mode: How to handle conflicts (merge, replace, append)
            validate_only: Only validate data without importing
            progress_callback: Optional callback for progress updates
            
        Returns:
            Dictionary with import results and statistics
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Import file not found: {file_path}")
        
        if data_type not in self.db_schemas:
            raise ValueError(f"Unsupported data type: {data_type}")
        
        if import_mode not in self.import_modes:
            raise ValueError(f"Unsupported import mode: {import_mode}")
        
        try:
            if progress_callback:
                progress_callback(0, "Starting import...")
            
            # Determine file format and extract data
            file_ext = Path(file_path).suffix.lower()
            if file_ext == '.csv':
                data = self._read_csv_file(file_path, data_type, progress_callback)
            elif file_ext == '.json':
                data = self._read_json_file(file_path, data_type, progress_callback)
            elif file_ext == '.zip':
                data = self._read_zip_file(file_path, data_type, progress_callback)
            else:
                raise ValueError(f"Unsupported file format: {file_ext}")
            
            if progress_callback:
                progress_callback(25, f"Loaded {len(data)} records, validating...")
            
            # Validate data structure
            validation_result = self._validate_data(data, data_type)
            if not validation_result['valid']:
                return {
                    'success': False,
                    'error': 'Data validation failed',
                    'validation_errors': validation_result['errors'],
                    'records_processed': 0
                }
            
            if validate_only:
                return {
                    'success': True,
                    'validation_only': True,
                    'records_validated': len(data),
                    'validation_result': validation_result
                }
            
            if progress_callback:
                progress_callback(50, "Data validated, preparing database...")
            
            # Initialize database schema
            self._ensure_database_schema(data_type)
            
            if progress_callback:
                progress_callback(75, f"Importing {len(data)} records...")
            
            # Import data to database
            import_result = self._import_to_database(data, data_type, import_mode, progress_callback)
            
            if progress_callback:
                progress_callback(100, "Import completed successfully")
            
            return import_result
            
        except Exception as e:
            if progress_callback:
                progress_callback(-1, f"Import failed: {str(e)}")
            raise
    
    def _read_csv_file(self, file_path: str, data_type: str, 
                      progress_callback: Optional[Callable[[int, str], None]]) -> List[Dict[str, Any]]:
        """Read and parse CSV file."""
        data = []
        
        with open(file_path, 'r', newline='', encoding='utf-8') as csvfile:
            # Skip comment lines that start with #
            lines = [line for line in csvfile if not line.strip().startswith('#')]
            
            # Reset file pointer and create reader
            csvfile.seek(0)
            # Skip comment lines again
            for line in csvfile:
                if not line.strip().startswith('#'):
                    break
            
            # Get current position and read from there
            csvfile.seek(0)
            content = csvfile.read()
            lines = content.split('\n')
            
            # Find first non-comment line for header
            header_line = None
            data_start = 0
            for i, line in enumerate(lines):
                if not line.strip().startswith('#') and line.strip():
                    header_line = line
                    data_start = i + 1
                    break
            
            if not header_line:
                raise ValueError("No data found in CSV file")
            
            # Parse header and data
            reader = csv.DictReader([header_line] + lines[data_start:])
            
            for i, row in enumerate(reader):
                # Clean up row data
                clean_row = {}
                for key, value in row.items():
                    if key:  # Skip empty keys
                        # Handle JSON-encoded fields
                        if value.startswith('[') or value.startswith('{'):
                            try:
                                clean_row[key.lower().replace(' ', '_')] = json.loads(value)
                            except json.JSONDecodeError:
                                clean_row[key.lower().replace(' ', '_')] = value
                        else:
                            clean_row[key.lower().replace(' ', '_')] = value
                
                if clean_row:  # Only add non-empty rows
                    data.append(clean_row)
                
                # Progress update
                if progress_callback and i % 100 == 0:
                    progress = 10 + int((i / 1000) * 10)  # 10-20% for reading
                    progress_callback(min(progress, 20), f"Reading row {i+1}")
        
        return data
    
    def _read_json_file(self, file_path: str, data_type: str,
                       progress_callback: Optional[Callable[[int, str], None]]) -> List[Dict[str, Any]]:
        """Read and parse JSON file."""
        with open(file_path, 'r', encoding='utf-8') as jsonfile:
            json_data = json.load(jsonfile)
        
        # Handle different JSON structures
        if isinstance(json_data, list):
            # Direct array of records
            data = json_data
        elif isinstance(json_data, dict):
            if 'data' in json_data:
                # SMBSeek export format with metadata
                data = json_data['data']
            elif 'records' in json_data:
                # Alternative format
                data = json_data['records']
            else:
                # Assume the dict contains the records directly
                data = [json_data]
        else:
            raise ValueError("Invalid JSON structure for import")
        
        return data
    
    def _read_zip_file(self, file_path: str, data_type: str,
                      progress_callback: Optional[Callable[[int, str], None]]) -> List[Dict[str, Any]]:
        """Read and parse ZIP file containing CSV/JSON."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(file_path, 'r') as zipf:
                zipf.extractall(temp_dir)
                
                # Look for CSV or JSON files
                temp_path = Path(temp_dir)
                csv_files = list(temp_path.glob('*.csv'))
                json_files = list(temp_path.glob('*.json'))
                
                # Prefer JSON over CSV for more complete data
                if json_files:
                    for json_file in json_files:
                        if 'metadata' not in json_file.name.lower():
                            return self._read_json_file(str(json_file), data_type, progress_callback)
                elif csv_files:
                    return self._read_csv_file(str(csv_files[0]), data_type, progress_callback)
                else:
                    raise ValueError("No CSV or JSON files found in ZIP archive")
        
        return []
    
    def _validate_data(self, data: List[Dict[str, Any]], data_type: str) -> Dict[str, Any]:
        """
        Validate imported data structure and content.
        
        Args:
            data: List of data records to validate
            data_type: Type of data being validated
            
        Returns:
            Validation result dictionary
        """
        if not data:
            return {'valid': False, 'errors': ['No data to validate']}
        
        schema = self.db_schemas[data_type]
        errors = []
        warnings = []
        
        for i, record in enumerate(data):
            record_errors = []
            
            # Check required fields
            for field in schema['required_fields']:
                if field not in record or not record[field]:
                    record_errors.append(f"Missing required field: {field}")
            
            # Check key field uniqueness (within this dataset)
            key_values = tuple(str(record.get(field, '')) for field in schema['key_fields'])
            
            # Basic data type validation
            for field, value in record.items():
                if field.endswith('_count') or field.endswith('_mb') or field == 'port':
                    if value and not str(value).replace('.', '').isdigit():
                        record_errors.append(f"Invalid numeric value for {field}: {value}")
            
            if record_errors:
                errors.append(f"Record {i+1}: " + "; ".join(record_errors))
        
        # Check for duplicate keys in dataset
        key_counts = {}
        for record in data:
            key_values = tuple(str(record.get(field, '')) for field in schema['key_fields'])
            key_counts[key_values] = key_counts.get(key_values, 0) + 1
        
        duplicates = [key for key, count in key_counts.items() if count > 1]
        if duplicates:
            warnings.append(f"Found {len(duplicates)} duplicate records based on key fields")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'records_validated': len(data),
            'duplicate_keys': len(duplicates)
        }
    
    def _ensure_database_schema(self, data_type: str) -> None:
        """Ensure database tables exist for the data type."""
        schema = self.db_schemas[data_type]
        
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(schema['sql_create'])
            
            # Create indexes for performance
            if data_type == 'servers':
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_servers_ip 
                    ON servers (ip_address)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_servers_country 
                    ON servers (country_code)
                """)
            elif data_type == 'vulnerabilities':
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_vulnerabilities_server 
                    ON vulnerabilities (server_ip)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_vulnerabilities_severity 
                    ON vulnerabilities (severity)
                """)
            elif data_type == 'shares':
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_shares_server 
                    ON shares (server_ip)
                """)
            
            conn.commit()
    
    def _import_to_database(self, data: List[Dict[str, Any]], data_type: str, 
                          import_mode: str, progress_callback: Optional[Callable[[int, str], None]]) -> Dict[str, Any]:
        """
        Import validated data to database.
        
        Args:
            data: Validated data to import
            data_type: Type of data
            import_mode: Import mode (merge, replace, append)
            progress_callback: Progress callback
            
        Returns:
            Import result statistics
        """
        schema = self.db_schemas[data_type]
        table = schema['table']
        
        stats = {
            'success': True,
            'records_processed': 0,
            'records_inserted': 0,
            'records_updated': 0,
            'records_skipped': 0,
            'errors': []
        }
        
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            
            # Handle replace mode
            if import_mode == 'replace':
                cursor.execute(f"DELETE FROM {table}")
                if progress_callback:
                    progress_callback(80, f"Cleared existing {data_type} records")
            
            # Prepare fields for insertion
            all_fields = schema['required_fields'] + schema['optional_fields']
            
            for i, record in enumerate(data):
                try:
                    # Build field lists and values
                    fields = []
                    values = []
                    placeholders = []
                    
                    for field in all_fields:
                        if field in record and record[field] is not None:
                            v = record[field]
                            if field in _TS_FIELDS:
                                v = normalize_db_timestamp(v)
                            fields.append(field)
                            values.append(v)
                            placeholders.append('?')
                    
                    # Add timestamp fields
                    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    if 'created_at' not in fields:
                        fields.append('created_at')
                        values.append(current_time)
                        placeholders.append('?')
                    
                    fields.append('updated_at')
                    values.append(current_time)
                    placeholders.append('?')
                    
                    # Check if record exists (for merge/append modes)
                    if import_mode in ['merge', 'append']:
                        key_conditions = []
                        key_values = []
                        for key_field in schema['key_fields']:
                            if key_field in record:
                                key_conditions.append(f"{key_field} = ?")
                                key_values.append(record[key_field])
                        
                        if key_conditions:
                            cursor.execute(
                                f"SELECT id FROM {table} WHERE {' AND '.join(key_conditions)}",
                                key_values
                            )
                            existing_record = cursor.fetchone()
                            
                            if existing_record:
                                if import_mode == 'append':
                                    # Skip existing records
                                    stats['records_skipped'] += 1
                                    continue
                                else:
                                    # Update existing record
                                    update_fields = [f"{field} = ?" for field in fields if field != 'created_at']
                                    update_values = [val for field, val in zip(fields, values) if field != 'created_at']
                                    update_values.append(existing_record[0])  # Add ID for WHERE clause
                                    
                                    cursor.execute(
                                        f"UPDATE {table} SET {', '.join(update_fields)} WHERE id = ?",
                                        update_values
                                    )
                                    stats['records_updated'] += 1
                                    continue
                    
                    # Insert new record
                    cursor.execute(
                        f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})",
                        values
                    )
                    stats['records_inserted'] += 1
                    
                except Exception as e:
                    error_msg = f"Record {i+1}: {str(e)}"
                    stats['errors'].append(error_msg)
                    continue
                
                finally:
                    stats['records_processed'] += 1
                    
                    # Progress update
                    if progress_callback and i % 50 == 0:
                        progress = 75 + int((i / len(data)) * 20)
                        progress_callback(progress, f"Processed {i+1}/{len(data)} records")
            
            conn.commit()
        
        return stats
    
    def get_import_modes(self) -> Dict[str, str]:
        """Get available import modes with descriptions."""
        return self.import_modes.copy()
    
    def get_supported_data_types(self) -> List[str]:
        """Get list of supported data types for import."""
        return list(self.db_schemas.keys())
    
    def validate_file_format(self, file_path: str) -> Dict[str, Any]:
        """
        Validate if file format is supported for import.
        
        Args:
            file_path: Path to file to validate
            
        Returns:
            Validation result with file format information
        """
        if not os.path.exists(file_path):
            return {'valid': False, 'error': 'File not found'}
        
        file_ext = Path(file_path).suffix.lower()
        supported_formats = ['.csv', '.json', '.zip']
        
        if file_ext not in supported_formats:
            return {
                'valid': False,
                'error': f'Unsupported file format: {file_ext}',
                'supported_formats': supported_formats
            }
        
        # Basic file structure validation
        try:
            if file_ext == '.csv':
                with open(file_path, 'r', encoding='utf-8') as f:
                    # Check if it looks like a CSV
                    sample = f.read(1024)
                    if ',' not in sample and '\t' not in sample:
                        return {'valid': False, 'error': 'File does not appear to be a valid CSV'}
            
            elif file_ext == '.json':
                with open(file_path, 'r', encoding='utf-8') as f:
                    json.load(f)  # This will raise exception if invalid JSON
            
            elif file_ext == '.zip':
                with zipfile.ZipFile(file_path, 'r') as zipf:
                    files = zipf.namelist()
                    if not any(f.endswith('.csv') or f.endswith('.json') for f in files):
                        return {'valid': False, 'error': 'ZIP file contains no CSV or JSON files'}
            
            return {
                'valid': True,
                'format': file_ext[1:],  # Remove the dot
                'file_size': os.path.getsize(file_path)
            }
            
        except Exception as e:
            return {'valid': False, 'error': f'File validation failed: {str(e)}'}
    
    def preview_import_data(self, file_path: str, data_type: str, 
                           max_records: int = 10) -> Dict[str, Any]:
        """
        Preview import data without actually importing.
        
        Args:
            file_path: Path to import file
            data_type: Type of data expected
            max_records: Maximum records to preview
            
        Returns:
            Preview result with sample data
        """
        try:
            # Read a sample of the data
            file_ext = Path(file_path).suffix.lower()
            if file_ext == '.csv':
                data = self._read_csv_file(file_path, data_type, None)
            elif file_ext == '.json':
                data = self._read_json_file(file_path, data_type, None)
            elif file_ext == '.zip':
                data = self._read_zip_file(file_path, data_type, None)
            else:
                raise ValueError(f"Unsupported file format: {file_ext}")
            
            # Limit to preview size
            preview_data = data[:max_records]
            
            # Validate the preview data
            validation = self._validate_data(preview_data, data_type)
            
            # Generate summary
            if data:
                fields_found = set()
                for record in preview_data:
                    fields_found.update(record.keys())
                
                schema = self.db_schemas[data_type]
                missing_required = set(schema['required_fields']) - fields_found
                extra_fields = fields_found - set(schema['required_fields'] + schema['optional_fields'])
            else:
                fields_found = set()
                missing_required = set()
                extra_fields = set()
            
            return {
                'success': True,
                'total_records': len(data),
                'preview_records': len(preview_data),
                'sample_data': preview_data,
                'fields_found': list(fields_found),
                'missing_required_fields': list(missing_required),
                'extra_fields': list(extra_fields),
                'validation_result': validation
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }


def get_import_engine(db_path: str) -> DataImportEngine:
    """
    Create a data import engine instance.
    
    Args:
        db_path: Path to the database file
        
    Returns:
        DataImportEngine instance
    """
    return DataImportEngine(db_path)
