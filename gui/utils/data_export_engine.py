"""
Dirracuda - Data Export Engine

Centralized data export system supporting multiple formats for team collaboration.
Handles CSV and JSON exports with consistent formatting and metadata.

Design Decision: Centralized export engine ensures consistent data formats
across all components and simplifies maintenance of export functionality.
"""

import csv
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Callable
import tempfile
import os


class DataExportEngine:
    """
    Centralized data export engine for Dirracuda.
    
    Provides consistent export functionality across all application components
    with support for CSV, JSON, and compressed formats. Includes metadata
    and validation for team collaboration workflows.
    """
    
    def __init__(self):
        """Initialize the data export engine."""
        self.export_formats = {
            'csv': self._export_csv,
            'json': self._export_json,
            'zip': self._export_zip
        }
        
        # Standard field mappings for different data types
        self.field_mappings = {
            'servers': {
                'required_fields': ['ip_address', 'country', 'auth_method', 'accessible_shares', 'vulnerabilities'],
                'optional_fields': ['country_code', 'last_seen', 'scan_count', 'status', 'port', 'os_version'],
                'display_names': {
                    'ip_address': 'IP Address',
                    'country': 'Country',
                    'country_code': 'Country Code', 
                    'auth_method': 'Authentication Method',
                    'accessible_shares': 'Accessible Shares',
                    'vulnerabilities': 'Vulnerabilities',
                    'last_seen': 'Last Seen',
                    'scan_count': 'Scan Count',
                    'status': 'Status',
                    'port': 'Port',
                    'os_version': 'OS Version'
                }
            },
            'vulnerabilities': {
                'required_fields': ['severity', 'type', 'description', 'affected_servers'],
                'optional_fields': ['first_seen', 'last_updated', 'status', 'cve_id', 'remediation', 'details'],
                'display_names': {
                    'severity': 'Severity',
                    'type': 'Vulnerability Type', 
                    'description': 'Description',
                    'affected_servers': 'Affected Servers',
                    'first_seen': 'First Seen',
                    'last_updated': 'Last Updated',
                    'status': 'Status',
                    'cve_id': 'CVE ID',
                    'remediation': 'Remediation',
                    'details': 'Additional Details'
                }
            },
            'shares': {
                'required_fields': ['server_ip', 'share_name', 'access_level'],
                'optional_fields': ['description', 'last_accessed', 'file_count', 'size_mb', 'permissions'],
                'display_names': {
                    'server_ip': 'Server IP',
                    'share_name': 'Share Name',
                    'access_level': 'Access Level',
                    'description': 'Description',
                    'last_accessed': 'Last Accessed',
                    'file_count': 'File Count',
                    'size_mb': 'Size (MB)',
                    'permissions': 'Permissions'
                }
            },
            'scan_results': {
                'required_fields': ['scan_id', 'timestamp', 'country_codes', 'total_servers'],
                'optional_fields': ['duration_seconds', 'discovery_method', 'success_rate', 'errors'],
                'display_names': {
                    'scan_id': 'Scan ID',
                    'timestamp': 'Scan Timestamp',
                    'country_codes': 'Country Codes',
                    'total_servers': 'Total Servers',
                    'duration_seconds': 'Duration (seconds)',
                    'discovery_method': 'Discovery Method', 
                    'success_rate': 'Success Rate',
                    'errors': 'Errors'
                }
            }
        }
    
    def export_data(self, data: List[Dict[str, Any]], data_type: str, 
                   export_format: str, output_path: str,
                   include_metadata: bool = True,
                   filters_applied: Optional[Dict[str, Any]] = None,
                   progress_callback: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
        """
        Export data in the specified format.
        
        Args:
            data: List of data dictionaries to export
            data_type: Type of data (servers, vulnerabilities, shares, scan_results)
            export_format: Format to export (csv, json, zip)
            output_path: Path to save the exported file
            include_metadata: Whether to include export metadata
            filters_applied: Dictionary of filters that were applied
            progress_callback: Optional callback for progress updates
            
        Returns:
            Dictionary with export results and statistics
        """
        if not data:
            raise ValueError("No data provided for export")
        
        if data_type not in self.field_mappings:
            raise ValueError(f"Unsupported data type: {data_type}")
        
        if export_format not in self.export_formats:
            raise ValueError(f"Unsupported export format: {export_format}")
        
        try:
            if progress_callback:
                progress_callback(0, "Starting export...")
            
            # Validate and normalize data
            validated_data = self._validate_and_normalize_data(data, data_type)
            
            if progress_callback:
                progress_callback(25, "Data validated, preparing export...")
            
            # Prepare export metadata
            metadata = self._create_export_metadata(
                data_type, export_format, len(validated_data), 
                filters_applied
            ) if include_metadata else None
            
            if progress_callback:
                progress_callback(50, "Exporting data...")
            
            # Execute export
            export_result = self.export_formats[export_format](
                validated_data, data_type, output_path, metadata, progress_callback
            )
            
            if progress_callback:
                progress_callback(100, "Export completed successfully")
            
            return export_result
            
        except Exception as e:
            if progress_callback:
                progress_callback(-1, f"Export failed: {str(e)}")
            raise
    
    def _validate_and_normalize_data(self, data: List[Dict[str, Any]], 
                                    data_type: str) -> List[Dict[str, Any]]:
        """
        Validate data structure and normalize field names.
        
        Args:
            data: Raw data to validate
            data_type: Type of data being validated
            
        Returns:
            Validated and normalized data
        """
        mapping = self.field_mappings[data_type]
        required_fields = mapping['required_fields']
        all_fields = required_fields + mapping['optional_fields']
        
        validated_data = []
        
        for i, item in enumerate(data):
            # Check for required fields
            missing_fields = [field for field in required_fields if field not in item]
            if missing_fields:
                raise ValueError(f"Item {i} missing required fields: {missing_fields}")
            
            # Create normalized item with only known fields
            normalized_item = {}
            for field in all_fields:
                if field in item:
                    normalized_item[field] = item[field]
                elif field in required_fields:
                    # Set empty value for missing required fields that somehow passed check
                    normalized_item[field] = ""
            
            validated_data.append(normalized_item)
        
        return validated_data
    
    def _create_export_metadata(self, data_type: str, export_format: str, 
                               record_count: int, filters_applied: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create export metadata for traceability.
        
        Args:
            data_type: Type of data exported
            export_format: Format used for export
            record_count: Number of records exported
            filters_applied: Filters that were applied to data
            
        Returns:
            Metadata dictionary
        """
        metadata = {
            'export_info': {
                'timestamp': datetime.now().isoformat(),
                'data_type': data_type,
                'format': export_format,
                'record_count': record_count,
                'tool': 'Dirracuda',
                'version': '1.0.0'
            },
            'data_schema': {
                'required_fields': self.field_mappings[data_type]['required_fields'],
                'optional_fields': self.field_mappings[data_type]['optional_fields'],
                'field_descriptions': self.field_mappings[data_type]['display_names']
            }
        }
        
        if filters_applied:
            metadata['filters_applied'] = filters_applied
        
        return metadata
    
    def _export_csv(self, data: List[Dict[str, Any]], data_type: str, 
                   output_path: str, metadata: Optional[Dict[str, Any]], 
                   progress_callback: Optional[Callable[[int, str], None]]) -> Dict[str, Any]:
        """
        Export data to CSV format.
        
        Args:
            data: Validated data to export
            data_type: Type of data
            output_path: Output file path
            metadata: Export metadata
            progress_callback: Progress callback function
            
        Returns:
            Export result dictionary
        """
        mapping = self.field_mappings[data_type]
        all_fields = mapping['required_fields'] + mapping['optional_fields']
        
        # Determine which fields are actually present in the data
        present_fields = []
        for field in all_fields:
            if any(field in item for item in data):
                present_fields.append(field)
        
        # Use display names for headers
        headers = [mapping['display_names'].get(field, field) for field in present_fields]
        
        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            
            # Write metadata as comments if included
            if metadata:
                writer.writerow([f"# Dirracuda Export - {metadata['export_info']['timestamp']}"])
                writer.writerow([f"# Data Type: {data_type}"])
                writer.writerow([f"# Records: {len(data)}"])
                if metadata.get('filters_applied'):
                    filters_str = ', '.join(f"{k}={v}" for k, v in metadata['filters_applied'].items() if v)
                    writer.writerow([f"# Filters: {filters_str}"])
                writer.writerow([])  # Empty row separator
            
            # Write headers
            writer.writerow(headers)
            
            # Write data rows
            for i, item in enumerate(data):
                row = []
                for field in present_fields:
                    value = item.get(field, '')
                    
                    # Handle different data types
                    if isinstance(value, (list, dict)):
                        value = json.dumps(value) if value else ''
                    elif value is None:
                        value = ''
                    else:
                        value = str(value)
                    
                    row.append(value)
                
                writer.writerow(row)
                
                # Progress update
                if progress_callback and i % 100 == 0:
                    progress = 50 + int((i / len(data)) * 40)
                    progress_callback(progress, f"Writing row {i+1}/{len(data)}")
        
        return {
            'success': True,
            'output_path': output_path,
            'format': 'csv',
            'records_exported': len(data),
            'file_size': os.path.getsize(output_path)
        }
    
    def _export_json(self, data: List[Dict[str, Any]], data_type: str,
                    output_path: str, metadata: Optional[Dict[str, Any]], 
                    progress_callback: Optional[Callable[[int, str], None]]) -> Dict[str, Any]:
        """
        Export data to JSON format.
        
        Args:
            data: Validated data to export
            data_type: Type of data
            output_path: Output file path
            metadata: Export metadata
            progress_callback: Progress callback function
            
        Returns:
            Export result dictionary
        """
        export_structure = {
            'data': data
        }
        
        # Include metadata if provided
        if metadata:
            export_structure['metadata'] = metadata
        
        # Write JSON file
        with open(output_path, 'w', encoding='utf-8') as jsonfile:
            json.dump(export_structure, jsonfile, indent=2, default=str)
        
        if progress_callback:
            progress_callback(90, "JSON export completed")
        
        return {
            'success': True,
            'output_path': output_path,
            'format': 'json',
            'records_exported': len(data),
            'file_size': os.path.getsize(output_path)
        }
    
    def _export_zip(self, data: List[Dict[str, Any]], data_type: str,
                   output_path: str, metadata: Optional[Dict[str, Any]], 
                   progress_callback: Optional[Callable[[int, str], None]]) -> Dict[str, Any]:
        """
        Export data to ZIP format containing both CSV and JSON.
        
        Args:
            data: Validated data to export
            data_type: Type of data
            output_path: Output ZIP file path
            metadata: Export metadata
            progress_callback: Progress callback function
            
        Returns:
            Export result dictionary
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            if progress_callback:
                progress_callback(60, "Creating CSV file...")
            
            # Create CSV file
            csv_path = temp_path / f"{data_type}_export.csv"
            csv_result = self._export_csv(data, data_type, str(csv_path), metadata, None)
            
            if progress_callback:
                progress_callback(75, "Creating JSON file...")
            
            # Create JSON file
            json_path = temp_path / f"{data_type}_export.json"
            json_result = self._export_json(data, data_type, str(json_path), metadata, None)
            
            if progress_callback:
                progress_callback(90, "Creating ZIP archive...")
            
            # Create ZIP file
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(csv_path, csv_path.name)
                zipf.write(json_path, json_path.name)
                
                # Add metadata file if present
                if metadata:
                    metadata_path = temp_path / "export_metadata.json"
                    with open(metadata_path, 'w', encoding='utf-8') as metafile:
                        json.dump(metadata, metafile, indent=2, default=str)
                    zipf.write(metadata_path, metadata_path.name)
        
        return {
            'success': True,
            'output_path': output_path,
            'format': 'zip',
            'records_exported': len(data),
            'file_size': os.path.getsize(output_path),
            'contents': ['CSV', 'JSON', 'Metadata']
        }
    
    def get_supported_formats(self) -> List[str]:
        """Get list of supported export formats."""
        return list(self.export_formats.keys())
    
    def get_data_types(self) -> List[str]:
        """Get list of supported data types."""
        return list(self.field_mappings.keys())
    
    def get_field_info(self, data_type: str) -> Dict[str, Any]:
        """
        Get field information for a data type.
        
        Args:
            data_type: Type of data
            
        Returns:
            Dictionary with field information
        """
        if data_type not in self.field_mappings:
            raise ValueError(f"Unknown data type: {data_type}")
        
        return self.field_mappings[data_type].copy()
    
    def validate_export_path(self, output_path: str, export_format: str) -> bool:
        """
        Validate export path and format compatibility.
        
        Args:
            output_path: Proposed output path
            export_format: Export format
            
        Returns:
            True if path is valid for the format
        """
        path_obj = Path(output_path)
        
        # Check if directory exists
        if not path_obj.parent.exists():
            return False
        
        # Check file extension matches format
        expected_extensions = {
            'csv': ['.csv'],
            'json': ['.json'],
            'zip': ['.zip']
        }
        
        if export_format in expected_extensions:
            valid_extensions = expected_extensions[export_format]
            if path_obj.suffix.lower() not in valid_extensions:
                return False
        
        return True
    
    def estimate_export_size(self, data: List[Dict[str, Any]], 
                           data_type: str, export_format: str) -> Dict[str, Any]:
        """
        Estimate the size of the exported file.
        
        Args:
            data: Data to be exported
            data_type: Type of data
            export_format: Export format
            
        Returns:
            Dictionary with size estimates
        """
        if not data:
            return {'estimated_bytes': 0, 'estimated_mb': 0}
        
        # Sample first few records to estimate average size
        sample_size = min(10, len(data))
        sample_data = data[:sample_size]
        
        if export_format == 'csv':
            # Estimate CSV size based on string length of fields
            total_chars = 0
            for item in sample_data:
                for value in item.values():
                    if isinstance(value, str):
                        total_chars += len(value)
                    else:
                        total_chars += len(str(value))
                total_chars += len(item) * 2  # Commas and newlines
            
            avg_chars_per_record = total_chars / sample_size if sample_size > 0 else 0
            estimated_bytes = int(avg_chars_per_record * len(data) * 1.2)  # 20% padding
            
        elif export_format == 'json':
            # Estimate JSON size based on sample serialization
            sample_json = json.dumps(sample_data, default=str)
            avg_bytes_per_record = len(sample_json.encode('utf-8')) / sample_size if sample_size > 0 else 0
            estimated_bytes = int(avg_bytes_per_record * len(data) * 1.3)  # 30% padding for structure
            
        else:  # zip
            # Estimate as sum of CSV and JSON with compression
            csv_estimate = self.estimate_export_size(data, data_type, 'csv')['estimated_bytes']
            json_estimate = self.estimate_export_size(data, data_type, 'json')['estimated_bytes']
            estimated_bytes = int((csv_estimate + json_estimate) * 0.6)  # 40% compression ratio
        
        return {
            'estimated_bytes': estimated_bytes,
            'estimated_mb': round(estimated_bytes / (1024 * 1024), 2)
        }


# Global export engine instance
export_engine = DataExportEngine()


def get_export_engine() -> DataExportEngine:
    """Get the global export engine instance."""
    return export_engine