# SMBSeek GUI Integration Guide

**Target Audience**: AI Coding Agents and GUI Developers  
**Last Updated**: 2025-01-04  
**SMBSeek Version**: 1.0+  

> **Note**
> This guide documents the legacy multi-command workflow (SMBSeek 1.x/2.x). Modern SMBSeek (3.x) exposes a single command entry point (`./smbseek.py [--country ...]`). Use this guide only when integrating with historical releases or maintaining backward compatibility.

## Overview

SMBSeek provides comprehensive share discovery and accessibility data through its database API. This guide covers the enhanced share tracking functionality and provides practical examples for GUI integration.

## Database API Methods

### 1. Existing Methods (Already Available)

#### `get_hosts_with_accessible_shares()`
Returns hosts that have at least one accessible SMB share.

```python
from shared.database import create_workflow_database
from shared.config import load_config

# Initialize database connection
config = load_config()
database = create_workflow_database(config, verbose=True)

# Get hosts with accessible shares
accessible_hosts = database.get_hosts_with_accessible_shares()

# Data format returned:
# [
#   {
#     'ip_address': '192.168.1.100',
#     'country': 'United States', 
#     'auth_method': 'Guest/Blank',
#     'accessible_shares': ['home', 'files', 'documents']
#   },
#   {
#     'ip_address': '10.0.0.50',
#     'country': 'Germany',
#     'auth_method': 'Anonymous', 
#     'accessible_shares': ['shared', 'public']
#   }
# ]

database.close()
```

#### `get_authenticated_hosts()`
Returns all hosts where SMB authentication succeeded (may or may not have accessible shares).

```python
# Get all authenticated hosts
authenticated_hosts = database.get_authenticated_hosts()

# Get authenticated hosts from the last 24 hours
recent_hosts = database.get_authenticated_hosts(recent_hours=24)

# Get authenticated hosts filtered by specific IP addresses
filtered_hosts = database.get_authenticated_hosts(ip_filter=['192.168.1.1', '10.0.0.5'])

# Combine time and IP filtering
specific_recent_hosts = database.get_authenticated_hosts(
    recent_hours=12,
    ip_filter=['192.168.1.1']
)
# Similar format but includes hosts without accessible shares
```

### 2. New Enhanced Methods (Requires View Installation)

#### `get_all_discovered_shares_per_host()`
Returns ALL shares discovered during enumeration (accessible + non-accessible).

```python
# Get all discovered shares per host
all_shares_data = database.get_all_discovered_shares_per_host()

# Data format returned:
# [
#   {
#     'ip_address': '192.168.1.100',
#     'country': 'United States',
#     'auth_method': 'Guest/Blank', 
#     'all_shares': ['home', 'files', 'documents', 'admin$', 'c$']
#   }
# ]
```

#### `get_complete_share_summary()`  
Returns comprehensive share statistics with counts and lists.

```python
# Get complete share summary (requires v_host_share_summary view)
complete_summary = database.get_complete_share_summary()

# Data format returned:
# [
#   {
#     'ip_address': '192.168.1.100',
#     'country': 'United States',
#     'auth_method': 'Guest/Blank',
#     'first_seen': '2025-01-04T10:30:00',
#     'last_seen': '2025-01-04T14:20:00',
#     'total_shares_discovered': 5,
#     'accessible_shares_count': 3,
#     'all_shares_list': ['home', 'files', 'documents', 'admin$', 'c$'],
#     'accessible_shares_list': ['home', 'files', 'documents'],
#     'last_share_test': '2025-01-04T14:20:15'
#   }
# ]
```

## GUI Implementation Examples

### Example 1: Simple Host-Share Table

```python
def create_host_share_table():
    """Create a simple table showing hosts and their accessible shares."""
    config = load_config()
    database = create_workflow_database(config)
    
    try:
        hosts = database.get_hosts_with_accessible_shares()
        
        table_data = []
        for host in hosts:
            row = {
                'host': host['ip_address'],
                'country': host.get('country', 'Unknown'),
                'shares': ', '.join(host['accessible_shares'])
            }
            table_data.append(row)
        
        return table_data
        
    finally:
        database.close()

# Usage:
# table_data = create_host_share_table()
# Output: [{'host': '1.2.3.4', 'country': 'US', 'shares': 'home,files,docs'}]
```

### Example 2: Enhanced Statistics Dashboard

```python
def create_share_statistics():
    """Create comprehensive share statistics for dashboard."""
    config = load_config()
    database = create_workflow_database(config)
    
    try:
        # Try enhanced method first, fall back if view not available
        try:
            summary_data = database.get_complete_share_summary()
            enhanced_available = True
        except:
            summary_data = database.get_hosts_with_accessible_shares()
            enhanced_available = False
        
        if enhanced_available:
            # Use enhanced data with totals
            stats = {
                'total_hosts': len(summary_data),
                'total_shares_discovered': sum(h['total_shares_discovered'] for h in summary_data),
                'total_accessible_shares': sum(h['accessible_shares_count'] for h in summary_data),
                'hosts_with_accessible_shares': len([h for h in summary_data if h['accessible_shares_count'] > 0])
            }
            
            # Calculate access rates
            if stats['total_shares_discovered'] > 0:
                stats['share_access_rate'] = (stats['total_accessible_shares'] / stats['total_shares_discovered']) * 100
            else:
                stats['share_access_rate'] = 0.0
                
        else:
            # Use basic data
            stats = {
                'total_hosts': len(summary_data),
                'hosts_with_accessible_shares': len(summary_data),
                'total_accessible_shares': sum(len(h['accessible_shares']) for h in summary_data),
                'enhanced_data_available': False
            }
        
        return stats
        
    finally:
        database.close()
```

### Example 3: Host Details View

```python
def get_host_details(ip_address):
    """Get detailed information for a specific host."""
    config = load_config()
    database = create_workflow_database(config)
    
    try:
        # Get complete summary data
        all_hosts = database.get_complete_share_summary()
        host_data = next((h for h in all_hosts if h['ip_address'] == ip_address), None)
        
        if not host_data:
            return {'error': f'Host {ip_address} not found'}
        
        # Format for GUI display
        details = {
            'ip_address': host_data['ip_address'],
            'country': host_data.get('country', 'Unknown'),
            'auth_method': host_data.get('auth_method', 'Unknown'),
            'first_seen': host_data.get('first_seen'),
            'last_seen': host_data.get('last_seen'),
            'last_share_test': host_data.get('last_share_test'),
            'share_summary': {
                'total_discovered': host_data['total_shares_discovered'],
                'accessible_count': host_data['accessible_shares_count'],
                'access_rate': (host_data['accessible_shares_count'] / host_data['total_shares_discovered']) * 100 if host_data['total_shares_discovered'] > 0 else 0
            },
            'shares': {
                'all_discovered': host_data['all_shares_list'],
                'accessible': host_data['accessible_shares_list'],
                'non_accessible': [s for s in host_data['all_shares_list'] if s not in host_data['accessible_shares_list']]
            }
        }
        
        return details
        
    finally:
        database.close()
```

## Database View Installation

### Required Setup
Before using enhanced methods, install the database view:

```bash
# Install the enhanced view
python3 tools/add_share_summary_view.py --database smbseek.db

# Verify installation
python3 tools/add_share_summary_view.py --dry-run
```

### Fallback Handling
The enhanced methods automatically fall back to manual queries if the view is not available:

```python
def safe_get_share_data():
    """Safely get share data with automatic fallback."""
    config = load_config()
    database = create_workflow_database(config, verbose=True)
    
    try:
        # This will automatically use view if available, or fallback query if not
        return database.get_complete_share_summary()
    finally:
        database.close()
```

## Error Handling Best Practices

```python
def robust_share_query():
    """Example of robust error handling for share queries."""
    config = load_config()
    database = create_workflow_database(config)
    
    try:
        # Always wrap database calls in try-except
        share_data = database.get_hosts_with_accessible_shares()
        
        if not share_data:
            return {'message': 'No hosts with accessible shares found'}
        
        # Validate data structure
        processed_data = []
        for host in share_data:
            if 'ip_address' in host and 'accessible_shares' in host:
                processed_data.append({
                    'ip': host['ip_address'],
                    'shares': host['accessible_shares'] if isinstance(host['accessible_shares'], list) else [],
                    'country': host.get('country', 'Unknown')
                })
        
        return {'hosts': processed_data}
        
    except Exception as e:
        return {'error': f'Database query failed: {str(e)}'}
    finally:
        database.close()
```

## Configuration and Performance

### Database Connection Management
```python
# Always use try/finally for proper cleanup
config = load_config()
database = create_workflow_database(config, verbose=False)  # Set verbose=True for debugging

try:
    # Your database operations here
    pass
finally:
    database.close()  # Essential for proper cleanup
```

### Performance Notes
- Enhanced methods with views are optimized for large datasets
- Use `get_hosts_with_accessible_shares()` for simple accessible-only queries
- Use `get_complete_share_summary()` for comprehensive statistics
- The system automatically handles GROUP_CONCAT limitations in SQLite

## Integration Checklist

- [ ] Test with existing database before installing enhanced view
- [ ] Install database view using provided migration script  
- [ ] Implement fallback handling for missing view
- [ ] Add proper error handling around all database calls
- [ ] Use try/finally blocks for database cleanup
- [ ] Test with empty database (no share data)
- [ ] Validate data types and handle edge cases
- [ ] Consider caching for frequently accessed data

## Support and Troubleshooting

### Common Issues
1. **Empty Results**: Check if SMBSeek has discovered any shares (`./smbseek.py discover`)
2. **View Not Found**: Install enhanced view (`python3 tools/add_share_summary_view.py`)
3. **Connection Errors**: Verify database file path and permissions
4. **Data Format Issues**: Always validate data structure and handle missing keys

### Debugging
```python
# Enable verbose mode for detailed database operation logging
database = create_workflow_database(config, verbose=True)
```

This guide provides all necessary information for integrating SMBSeek's enhanced share tracking into GUI applications while maintaining robust error handling and backward compatibility.
