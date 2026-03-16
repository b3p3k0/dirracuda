# Frontend Integration Recommendations for SMBSeek

> **Note**
> This document targets the legacy multi-subcommand CLI (SMBSeek 2.x). For SMBSeek 3.x, prefer the unified command `./smbseek.py` (global scan) or `./smbseek.py --country XX` (country-specific) and treat the guidance below as historical reference when integrating older workflows.

## **Document Purpose**

This document provides critical implementation recommendations for frontend/GUI teams integrating with the SMBSeek backend. Created after identifying performance issues with duplicate scanning during access verification phases.

**Target Audience**: Agentic coders and frontend developers integrating with SMBSeek backend  
**Last Updated**: 2025-01-05  
**Backend Version Compatibility**: SMBSeek 2.0.0+

---

## **CRITICAL ISSUE RESOLVED: Duplicate Scanning During Access Phase**

### **Root Cause** 
The SMBSeek access command was not respecting the `--recent` parameter, causing all authenticated hosts to be rescanned regardless of when they were last tested. This led to significant time waste during access verification.

### **Resolution Applied**
- **Backend Fix**: Access command now implements `--recent N` parameter filtering
- **Database Enhancement**: Added `get_authenticated_hosts(recent_hours)` method
- **Configuration**: Added `workflow.access_recent_hours` setting (default: 2 hours)

---

## **INTEGRATION BEST PRACTICES**

### **1. Recent Host Filtering (CRITICAL)**

**Problem**: Without recent filtering, you'll retest shares on hosts scanned days/weeks ago.

**Solution**: Always use recent parameters when calling SMBSeek commands:

```python
# ✅ CORRECT: Use recent filtering for access verification
result = backend_interface.execute_command([
    "smbseek", "access", 
    "--recent", "2",  # Only test hosts from last 2 hours
    "--verbose"
])

# ❌ INCORRECT: This will test ALL authenticated hosts
result = backend_interface.execute_command([
    "smbseek", "access", "--verbose"
])
```

**Recommended Recent Timeframes**:
- **Access verification**: 2-6 hours (configurable via `workflow.access_recent_hours`)
- **File collection**: 4-12 hours 
- **Discovery**: Use existing `rescan_after_days` (90 days default)

### **2. Configuration-Driven Timeouts**

**Issue**: Frontend operations can hang indefinitely without proper timeout configuration.

**Implementation**:

```python
class BackendInterface:
    def _load_timeout_configuration(self):
        """Load timeout with environment override support."""
        # Priority: ENV_VAR > config.json > default
        env_timeout = os.environ.get('SMBSEEK_GUI_TIMEOUT')
        if env_timeout:
            self.default_timeout = int(env_timeout) if env_timeout != '0' else None
        else:
            config = self.load_config()
            self.default_timeout = config.get('gui', {}).get('operation_timeout_seconds')
    
    def execute_with_timeout(self, cmd, timeout_override=None):
        """Execute with configurable timeout."""
        timeout = timeout_override or self.default_timeout
        return subprocess.run(cmd, timeout=timeout, ...)
```

**Configuration Example** (`conf/config.json`):
```json
{
  "gui": {
    "operation_timeout_seconds": 3600,
    "enable_debug_timeouts": false
  },
  "workflow": {
    "access_recent_hours": 2
  }
}
```

### **3. Database Coordination Patterns**

**Lock File Management**:
```python
class ScanManager:
    def create_lock_file(self, scan_type="access_verification"):
        """Prevent concurrent scans that could interfere."""
        lock_data = {
            "scan_type": scan_type,
            "start_time": datetime.now().isoformat(),
            "process_id": os.getpid()
        }
        # Implementation handles stale lock cleanup
```

**Recent Activity Checking**:
```python
def should_skip_recent_scan(self, hours=2):
    """Check if recent scan makes new scan redundant."""
    last_scan = self.get_last_scan_time()
    if last_scan and (datetime.now() - last_scan).total_seconds() < hours * 3600:
        return True
    return False
```

### **4. Command Sequencing and Dependencies**

**Proper Workflow Order**:
```python
class WorkflowManager:
    def execute_scan_sequence(self):
        # 1. Discovery (creates authenticated hosts)
        discovery_result = self.run_discovery()
        
        # 2. Access verification (recent filtering critical here)
        access_result = self.run_access_verification(recent_hours=2)
        
        # 3. Collection (only on accessible shares)
        collection_result = self.run_collection()
```

**Command Dependencies**:
- `smbseek access` requires successful `smbseek discover` results
- `smbseek collect` requires successful `smbseek access` results
- Each phase should check for prerequisites before execution

---

## **SPECIFIC CLI INTEGRATION PATTERNS**

### **Access Verification Command Usage**

```python
# ✅ Frontend team should use:
def run_access_verification(self, recent_hours=2):
    cmd = [
        "smbseek", "access",
        "--recent", str(recent_hours),  # CRITICAL: Recent filtering
        "--verbose",
        "--quiet" if self.quiet_mode else "--verbose"
    ]
    return self.execute_with_timeout(cmd)

# Alternative: Target specific servers
def run_access_on_servers(self, ip_list):
    cmd = [
        "smbseek", "access", 
        "--servers", ",".join(ip_list),
        "--verbose"
    ]
    return self.execute_with_timeout(cmd)
```

### **Progress Tracking Integration**

**Enhanced Progress Parsing**:
```python
def _parse_progress_indicators(self, output_line):
    """Parse SMBSeek progress with recent filtering awareness."""
    # Progress pattern: "📊 Progress: 25/100 (25.0%) | Success: 5, Failed: 20 (20%)"
    progress_match = re.search(
        r'📊 Progress: (\d+)/(\d+) \((\d+(?:\.\d+)?)%\) \| Success: (\d+), Failed: (\d+) \((\d+)%\)',
        output_line
    )

    if progress_match:
        current, total, percentage, success_count, failed_count, success_pct = progress_match.groups()

        # Enhanced context for recent filtering
        if "recent" in output_line.lower():
            status = f"Testing recent hosts: {current}/{total} (success rate {success_pct}%)"
        else:
            status = f"Testing hosts: {current}/{total} (success rate {success_pct}%)"

        return float(percentage), status
    
    return None, None
```

### **Error Handling and Recovery**

**Common Issues and Solutions**:

```python
class SMBSeekErrorHandler:
    def handle_access_errors(self, error_output):
        """Handle common access verification errors."""
        if "No authenticated hosts found from the last" in error_output:
            return {
                "error_type": "no_recent_hosts",
                "suggestion": "Increase --recent parameter or run discovery first",
                "retry_with": {"recent_hours": 24}
            }
        
        if "None of the specified servers are authenticated" in error_output:
            return {
                "error_type": "servers_not_authenticated", 
                "suggestion": "Run discovery on specified servers first"
            }
        
        return {"error_type": "unknown", "raw_error": error_output}
```

---

## **PERFORMANCE OPTIMIZATION GUIDELINES**

### **1. Intelligent Caching Strategy**

```python
class ResultsCache:
    def __init__(self):
        self.access_results = {}
        self.cache_duration = 3600  # 1 hour default
    
    def get_cached_access_results(self, ip_address):
        """Get cached results if still valid."""
        if ip_address in self.access_results:
            cache_entry = self.access_results[ip_address]
            age = time.time() - cache_entry['timestamp']
            if age < self.cache_duration:
                return cache_entry['results']
        return None
    
    def cache_access_results(self, ip_address, results):
        """Cache results with timestamp."""
        self.access_results[ip_address] = {
            'results': results,
            'timestamp': time.time()
        }
```

### **2. Batch Processing for Large Datasets**

```python
def process_large_host_lists(self, host_ips, batch_size=50):
    """Process large host lists in batches to prevent timeouts."""
    for i in range(0, len(host_ips), batch_size):
        batch = host_ips[i:i + batch_size]
        batch_cmd = [
            "smbseek", "access",
            "--servers", ",".join(batch),
            "--recent", "2"  # Still apply recent filtering
        ]
        yield self.execute_with_timeout(batch_cmd)
```

### **3. Resource Management**

```python
class ResourceManager:
    def __init__(self):
        self.max_concurrent_scans = 1  # SMBSeek uses lock files
        self.cleanup_stale_locks_on_startup()
    
    def cleanup_stale_locks_on_startup(self):
        """Clean up stale lock files from crashed processes."""
        lock_file = Path(".scan_lock")
        if lock_file.exists():
            try:
                with open(lock_file, 'r') as f:
                    lock_data = json.load(f)
                pid = lock_data.get('process_id')
                if pid and not self._process_exists(pid):
                    lock_file.unlink()  # Remove stale lock
            except Exception:
                lock_file.unlink()  # Remove corrupted lock
```

---

## **DEBUGGING AND TROUBLESHOOTING**

### **Common Issues and Diagnostics**

**1. "Operation timed out" Errors**:
```bash
# Check timeout configuration
export SMBSEEK_GUI_TIMEOUT=7200  # 2 hours
# Or set in config.json under gui.operation_timeout_seconds
```

**2. "No recent hosts found" Warnings**:
```python
# Diagnostic: Check what hosts exist
result = subprocess.run(["smbseek", "db", "query", "--summary"])
print("Database status:", result.stdout)

# Solution: Increase recent window or run discovery
recent_hours = 24  # Increase from 2 to 24 hours
```

**3. Lock File Issues**:
```bash
# Check for stale locks
ls -la .scan_lock
# Remove if process is dead
rm .scan_lock
```

### **Debug Output Analysis**

**Enable Verbose Logging**:
```python
def enable_debug_mode(self):
    self.debug_timeouts = True
    self.verbose_output = True
    
def log_command_execution(self, cmd, start_time, end_time, result):
    """Log detailed execution information."""
    duration = end_time - start_time
    print(f"Command: {' '.join(cmd)}")
    print(f"Duration: {duration:.2f}s")
    print(f"Return Code: {result.returncode}")
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
```

---

## **CONFIGURATION RECOMMENDATIONS**

### **Production Settings** (`conf/config.json`):
```json
{
  "workflow": {
    "rescan_after_days": 90,
    "access_recent_hours": 2,
    "pause_between_steps": false,
    "skip_failed_hosts": true
  },
  "connection": {
    "timeout": 30,
    "rate_limit_delay": 1,
    "share_access_delay": 1
  },
  "gui": {
    "operation_timeout_seconds": 3600,
    "enable_debug_timeouts": false
  }
}
```

### **Development/Testing Settings**:
```json
{
  "workflow": {
    "access_recent_hours": 24,
    "pause_between_steps": true
  },
  "gui": {
    "operation_timeout_seconds": 300,
    "enable_debug_timeouts": true
  }
}
```

---

## **TESTING AND VALIDATION**

### **Integration Test Patterns**

```python
class SMBSeekIntegrationTests:
    def test_recent_filtering_works(self):
        """Test that recent filtering prevents duplicate scanning."""
        # Run discovery
        discovery_result = self.backend.run_discovery(['US'])
        assert discovery_result['success']
        
        # Run access with recent=1 hour
        access_result1 = self.backend.run_access_verification(recent_hours=1)
        hosts_tested1 = access_result1.get('hosts_tested', 0)
        
        # Run access again immediately - should test fewer hosts
        access_result2 = self.backend.run_access_verification(recent_hours=1) 
        hosts_tested2 = access_result2.get('hosts_tested', 0)
        
        # Recent filtering should prevent retesting the same hosts
        assert hosts_tested2 <= hosts_tested1, "Recent filtering should reduce duplicate scanning"
    
    def test_timeout_configuration(self):
        """Test that timeout configuration works properly."""
        # Set short timeout
        old_timeout = self.backend.default_timeout
        self.backend.default_timeout = 5  # 5 seconds
        
        with pytest.raises(subprocess.TimeoutExpired):
            # This should timeout on long operations
            self.backend.run_discovery(['US'])
        
        self.backend.default_timeout = old_timeout
```

### **Performance Benchmarking**

```python
def benchmark_scanning_performance(self):
    """Benchmark scanning with and without recent filtering."""
    start_time = time.time()
    
    # Without recent filtering (legacy behavior)
    result_all = self.backend.execute_command(["smbseek", "access", "--verbose"])
    time_without_filtering = time.time() - start_time
    
    start_time = time.time()
    
    # With recent filtering
    result_recent = self.backend.execute_command(["smbseek", "access", "--recent", "2"])  
    time_with_filtering = time.time() - start_time
    
    print(f"Time without filtering: {time_without_filtering:.2f}s")
    print(f"Time with filtering: {time_with_filtering:.2f}s") 
    print(f"Time saved: {time_without_filtering - time_with_filtering:.2f}s")
```

---

## **MIGRATION FROM LEGACY IMPLEMENTATIONS**

### **Update Checklist for Existing Frontend Code**

- [ ] **Add recent parameter to all access verification calls**
- [ ] **Implement timeout configuration loading**  
- [ ] **Update progress parsing to handle recent filtering messages**
- [ ] **Add error handling for "no recent hosts" scenarios**
- [ ] **Implement lock file cleanup on startup**
- [ ] **Update test suites to validate recent filtering**
- [ ] **Configure appropriate `access_recent_hours` in config**

### **Code Update Examples**

**Before (Legacy)**:
```python
def run_access_scan(self):
    cmd = ["smbseek", "access", "--verbose"]
    return subprocess.run(cmd, timeout=None)  # No timeout!
```

**After (Recommended)**:
```python  
def run_access_scan(self, recent_hours=2):
    cmd = [
        "smbseek", "access", 
        "--recent", str(recent_hours),  # Recent filtering
        "--verbose"
    ]
    timeout = self.config.get('gui', {}).get('operation_timeout_seconds', 3600)
    return subprocess.run(cmd, timeout=timeout)
```

---

## **APPENDIX: TECHNICAL REFERENCES**

### **Backend Command Reference**

| Command | Recent Support | Usage |
|---------|---------------|-------|
| `smbseek discover` | Via `--rescan-all` | Respects `workflow.rescan_after_days` |
| `smbseek access` | **NEW**: `--recent N` | Filter to hosts from last N hours |
| `smbseek collect` | Via workflow | Inherits from access results |
| `smbseek run` | **UPDATED** | Uses `workflow.access_recent_hours` |

### **Database Schema Notes**

- `smb_servers.last_seen`: Used for recent filtering
- `share_access.timestamp`: Share test timestamps
- Recent filtering uses `datetime('now', '-N hours')` SQLite function

### **Configuration Schema**

```json
{
  "workflow": {
    "rescan_after_days": "integer (discovery phase filtering)",
    "access_recent_hours": "integer (access phase filtering)", 
    "pause_between_steps": "boolean",
    "skip_failed_hosts": "boolean"
  },
  "gui": {
    "operation_timeout_seconds": "integer or null",
    "enable_debug_timeouts": "boolean"
  }
}
```

---

## **SUPPORT AND FEEDBACK**

**Issues**: Report integration problems and performance issues through your standard channels.

**Performance Monitoring**: Track these metrics in your frontend:
- Time spent in access verification phase
- Number of hosts tested vs. skipped due to recent filtering  
- Timeout occurrences and duration
- Lock file conflicts

**Best Practice Updates**: This document will be updated as new integration patterns are identified and validated.

---

**Document Revision**: 1.0  
**Backend Compatibility**: SMBSeek 2.0.0+  
**Critical Issue Status**: ✅ RESOLVED - Recent filtering implemented
