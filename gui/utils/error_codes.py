"""
Dirracuda - Centralized Error Code System

Provides unique error codes for all application errors to enable efficient
troubleshooting and user support. Error codes follow hierarchical numbering:

- DB001-DB099: Database file errors (missing, permissions, corruption)
- VAL001-VAL099: Validation errors (schema, structure, compatibility)
- IMP001-IMP099: Import process errors (data loading, conversion)
- CFG001-CFG099: Configuration errors (settings, paths)
- UI001-UI099: User interface errors (dialogs, components)
- SYS001-SYS099: System errors (threading, resources)

Design Decision: Centralized error management enables consistent user experience
and efficient debugging by providing unique identifiers for all error scenarios.
"""

from typing import Dict, Any, Optional
from enum import Enum


class ErrorCategory(Enum):
    """Error category enumeration for hierarchical error codes."""
    DATABASE = "DB"
    VALIDATION = "VAL" 
    IMPORT = "IMP"
    CONFIG = "CFG"
    UI = "UI"
    SYSTEM = "SYS"


class ErrorCode:
    """Individual error code with metadata."""
    
    def __init__(self, code: str, category: ErrorCategory, message: str, 
                 suggestion: Optional[str] = None, severity: str = "error"):
        """
        Initialize error code.
        
        Args:
            code: Unique error code (e.g., "DB001")
            category: Error category enum
            message: Default error message template
            suggestion: Optional suggestion for resolution
            severity: Error severity level (error, warning, info)
        """
        self.code = code
        self.category = category
        self.message = message
        self.suggestion = suggestion
        self.severity = severity
    
    def format_error(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Format error for display to user.
        
        Args:
            context: Optional context variables for message formatting
            
        Returns:
            Formatted error information
        """
        formatted_message = self.message
        if context:
            try:
                formatted_message = self.message.format(**context)
            except (KeyError, ValueError):
                # If formatting fails, use original message
                pass
        
        return {
            'code': self.code,
            'category': self.category.value,
            'message': formatted_message,
            'suggestion': self.suggestion,
            'severity': self.severity,
            'full_message': f"[{self.code}] {formatted_message}"
        }


class ErrorRegistry:
    """Registry of all application error codes."""
    
    def __init__(self):
        """Initialize error registry with all defined error codes."""
        self.errors = {}
        self._register_database_errors()
        self._register_validation_errors()
        self._register_import_errors()
        self._register_config_errors()
        self._register_ui_errors()
        self._register_system_errors()
    
    def _register_database_errors(self):
        """Register database-related error codes (DB001-DB099)."""
        
        # File access errors
        self.errors["DB001"] = ErrorCode(
            "DB001", ErrorCategory.DATABASE,
            "Database file not found at {path}",
            "Verify the database file path and ensure the file exists"
        )
        
        self.errors["DB002"] = ErrorCode(
            "DB002", ErrorCategory.DATABASE,
            "Cannot read database file: {path}",
            "Check file permissions and ensure you have read access"
        )
        
        self.errors["DB003"] = ErrorCode(
            "DB003", ErrorCategory.DATABASE,
            "Cannot write to database file: {path}",
            "Check file permissions and ensure you have write access"
        )
        
        # Database structure errors
        self.errors["DB010"] = ErrorCode(
            "DB010", ErrorCategory.DATABASE,
            "Database file is corrupted or invalid: {error}",
            "Try using a database backup or re-initialize the database"
        )
        
        self.errors["DB011"] = ErrorCode(
            "DB011", ErrorCategory.DATABASE,
            "Database connection failed: {error}",
            "Ensure the file is a valid SQLite database and not in use"
        )
        
        self.errors["DB012"] = ErrorCode(
            "DB012", ErrorCategory.DATABASE,
            "Database locked by another process",
            "Close other applications using the database and try again"
        )
    
    def _register_validation_errors(self):
        """Register validation-related error codes (VAL001-VAL099)."""
        
        # Schema validation errors
        self.errors["VAL001"] = ErrorCode(
            "VAL001", ErrorCategory.VALIDATION,
            "Database missing required core tables. Found: {tables_found}",
            "Use a complete Dirracuda database with smb_servers and scan_sessions tables"
        )
        
        self.errors["VAL002"] = ErrorCode(
            "VAL002", ErrorCategory.VALIDATION,
            "Database schema incompatible. Compatibility: {compatibility_level}",
            "Import only compatible Dirracuda databases or convert the schema"
        )
        
        self.errors["VAL003"] = ErrorCode(
            "VAL003", ErrorCategory.VALIDATION,
            "Invalid table structure in {table}: {error}",
            "Verify the database was created by Dirracuda"
        )
        
        # Data validation errors
        self.errors["VAL010"] = ErrorCode(
            "VAL010", ErrorCategory.VALIDATION,
            "No data found in database tables",
            "Use a database that contains scan results or perform a new scan"
        )
        
        self.errors["VAL011"] = ErrorCode(
            "VAL011", ErrorCategory.VALIDATION,
            "Data integrity check failed: {error}",
            "Database may be corrupted - try using a backup"
        )
    
    def _register_import_errors(self):
        """Register import process error codes (IMP001-IMP099)."""
        
        # Import process errors
        self.errors["IMP001"] = ErrorCode(
            "IMP001", ErrorCategory.IMPORT,
            "Database import failed during {stage}: {error}",
            "Check database file integrity and try again"
        )
        
        self.errors["IMP002"] = ErrorCode(
            "IMP002", ErrorCategory.IMPORT,
            "Import cancelled by user",
            "Import was stopped - no changes were made",
            severity="info"
        )
        
        self.errors["IMP003"] = ErrorCode(
            "IMP003", ErrorCategory.IMPORT,
            "Import timeout after {duration} seconds",
            "Try importing a smaller database or increase timeout"
        )
    
    def _register_config_errors(self):
        """Register configuration error codes (CFG001-CFG099)."""
        
        # Configuration file errors
        self.errors["CFG001"] = ErrorCode(
            "CFG001", ErrorCategory.CONFIG,
            "Configuration file not found: {path}",
            "Create a configuration file or check the path"
        )
        
        self.errors["CFG002"] = ErrorCode(
            "CFG002", ErrorCategory.CONFIG,
            "Invalid configuration format: {error}",
            "Check the configuration file syntax"
        )
        
        # Backend path errors
        self.errors["CFG010"] = ErrorCode(
            "CFG010", ErrorCategory.CONFIG,
            "Backend path not found: {path}",
            "Verify backend installation or use --backend-path argument"
        )
        
        self.errors["CFG011"] = ErrorCode(
            "CFG011", ErrorCategory.CONFIG,
            "Backend executable not found: {path}",
            "Ensure SMBSeek backend is properly installed"
        )
    
    def _register_ui_errors(self):
        """Register user interface error codes (UI001-UI099)."""
        
        # Dialog errors
        self.errors["UI001"] = ErrorCode(
            "UI001", ErrorCategory.UI,
            "Failed to create dialog: {error}",
            "Restart the application or check system resources"
        )
        
        self.errors["UI002"] = ErrorCode(
            "UI002", ErrorCategory.UI,
            "Theme loading failed: {error}",
            "Application will use default styling",
            severity="warning"
        )
    
    def _register_system_errors(self):
        """Register system-level error codes (SYS001-SYS099)."""
        
        # Threading and resource errors
        self.errors["SYS001"] = ErrorCode(
            "SYS001", ErrorCategory.SYSTEM,
            "Background operation failed: {error}",
            "Try the operation again or restart the application"
        )
        
        self.errors["SYS002"] = ErrorCode(
            "SYS002", ErrorCategory.SYSTEM,
            "Insufficient system resources: {error}",
            "Close other applications or free up system resources"
        )
    
    def get_error(self, code: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get formatted error by code.
        
        Args:
            code: Error code (e.g., "DB001")
            context: Optional context for message formatting
            
        Returns:
            Formatted error information
        """
        if code not in self.errors:
            return {
                'code': 'UNK001',
                'category': 'UNKNOWN',
                'message': f'Unknown error code: {code}',
                'suggestion': 'Report this issue to support',
                'severity': 'error',
                'full_message': f'[UNK001] Unknown error code: {code}'
            }
        
        return self.errors[code].format_error(context)
    
    def get_all_errors(self) -> Dict[str, ErrorCode]:
        """Get all registered error codes."""
        return self.errors.copy()
    
    def get_errors_by_category(self, category: ErrorCategory) -> Dict[str, ErrorCode]:
        """
        Get all errors for a specific category.
        
        Args:
            category: Error category to filter by
            
        Returns:
            Dictionary of error codes for the category
        """
        return {
            code: error for code, error in self.errors.items()
            if error.category == category
        }


# Global error registry instance
_error_registry = ErrorRegistry()


def get_error(code: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Get formatted error by code (convenience function).
    
    Args:
        code: Error code (e.g., "DB001")
        context: Optional context for message formatting
        
    Returns:
        Formatted error information
    """
    return _error_registry.get_error(code, context)


def format_error_message(code: str, message: str, context: Optional[Dict[str, Any]] = None) -> str:
    """
    Format error message with code prefix.
    
    Args:
        code: Error code
        message: Error message
        context: Optional context for formatting
        
    Returns:
        Formatted error message with code
    """
    if context:
        try:
            formatted_message = message.format(**context)
        except (KeyError, ValueError):
            formatted_message = message
    else:
        formatted_message = message
    
    return f"[{code}] {formatted_message}"


def get_error_registry() -> ErrorRegistry:
    """Get the global error registry instance."""
    return _error_registry