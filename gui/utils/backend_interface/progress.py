"""
Progress parsing utilities for BackendInterface.

Contains methods for parsing CLI output, detecting phases, calculating percentages,
and mapping progress to workflow ranges. Includes all regex patterns and constants
used for progress tracking.
"""

import os
import re
from typing import Dict, List, Optional, Callable, Tuple, Any

from ..logging_config import get_logger

_logger = get_logger("backend_interface.progress")


def parse_output_stream(interface, stdout, output_lines: List[str],
                        progress_callback: Optional[Callable],
                        log_callback: Optional[Callable[[str], None]] = None) -> None:
    """
    Parse CLI output stream for progress indicators.

    Args:
        interface: BackendInterface instance
        stdout: Process stdout stream
        output_lines: List to append output lines to
        progress_callback: Function to call with progress updates
        log_callback: Function to call with raw CLI output lines (ANSI preserved)

    Design Decision: Regex patterns match the specific progress format
    used by the backend CLI for consistent progress tracking.
    """
    # Reset phase tracking for new scan
    interface.last_known_phase = None

    # Enhanced progress patterns matching real backend output format
    # Formats: "\033[96mℹ 📊 Progress: 45/120 (37.5%)\033[0m" OR "📊 Progress: 25/100 (25.0%) | Success: 5, Failed: 20"
    # Also handles recent filtering: "Testing recent hosts: 25/100 (25.0%)"
    # Made info symbol optional to capture authentication testing progress
    progress_pattern = re.compile(r'(?:\033\[\d+m)?(?:ℹ\s*)?(?:📊\s*Progress:|Testing\s+recent\s+hosts?:)\s*(\d+)/(\d+)\s*\((\d+(?:\.\d+)?)\%\)(?:\s*\|.*?)?(?:\033\[\d+m)?')

    # Workflow step detection for phase transitions
    # Format: "\033[94m[1/4] Discovery & Authentication\033[0m"
    workflow_pattern = re.compile(r'(?:\033\[\d+m)?\[(\d+)/(\d+)\]\s*(.+?)(?:\033\[\d+m)?$')

    # General status pattern with ANSI color support
    status_pattern = re.compile(r'(?:\033\[\d+m)?([ℹ✓⚠✗🚀])\s*(.+?)(?:\033\[\d+m)?$')

    # Early-stage patterns for immediate feedback
    shodan_pattern = re.compile(r'(?:Shodan|Query|Discovery|API).*?(\d+).*?(?:results?|found|hosts?|entries)', re.IGNORECASE)
    database_pattern = re.compile(r'(?:Database|DB).*?(\d+).*?(?:servers?|hosts?|known)', re.IGNORECASE)

    # Recent filtering specific patterns (as per backend team recommendations)
    recent_filtering_pattern = re.compile(r'(?:Loading|Found|Testing).*?(?:from\s+last|within\s+last|recent).*?(\d+).*?(?:days?|hours?).*?(\d+)?.*?(?:hosts?|servers?)', re.IGNORECASE)
    skipped_hosts_pattern = re.compile(r'(?:Skipped|Skipping).*?(\d+).*?(?:hosts?|servers?).*?(?:recent|within|last)', re.IGNORECASE)

    # Authentication testing detection (for phase transition)
    auth_testing_start_pattern = re.compile(r'Testing SMB authentication on (\d+) hosts', re.IGNORECASE)

    # Enhanced detailed progress patterns
    host_progress_pattern = re.compile(r'(?:Testing|Processing|Checking).*?(?:host|server).*?(\d+).*?of.*?(\d+)', re.IGNORECASE)
    share_progress_pattern = re.compile(r'(?:Enumerating|Checking).*?share.*?(\d+).*?of.*?(\d+)', re.IGNORECASE)
    auth_success_pattern = re.compile(r'Success:\s*(\d+),?\s*Failed:\s*(\d+)', re.IGNORECASE)

    # Individual host testing pattern - matches: "[1/10] Testing 213.217.247.165..."
    individual_host_pattern = re.compile(r'\[(\d+)/(\d+)\]\s*Testing\s+([\d.]+)', re.IGNORECASE)

    # Phase detection patterns with workflow step support
    phase_patterns = {
        'discovery': re.compile(r'(?:Discovery|Shodan|Query|Found.*SMB.*servers|Step\s*1)', re.IGNORECASE),
        'authentication': re.compile(r'(?:Testing SMB authentication|Authentication testing)', re.IGNORECASE),
        'access_testing': re.compile(r'(?:Access|Share.*Verification|Step\s*2)', re.IGNORECASE),
        'collection': re.compile(r'(?:Collection|Enumeration|File|Step\s*3)', re.IGNORECASE),
        'reporting': re.compile(r'(?:Report|Intelligence|Step\s*4)', re.IGNORECASE)
    }

    for raw_line in stdout:
        stripped_line = raw_line.rstrip("\n")
        line = stripped_line.strip()
        output_lines.append(line)

        if log_callback:
            try:
                log_callback(stripped_line)
            except Exception:
                pass

        if not progress_callback:
            continue

        # Parse workflow step transitions first (gives us phase context)
        workflow_match = workflow_pattern.search(line)
        if workflow_match:
            step_num, total_steps, step_name = workflow_match.groups()
            step_percentage = calculate_workflow_step_percentage(int(step_num), int(total_steps))
            progress_callback(step_percentage, f"Step {step_num}/{total_steps}: {step_name}")
            continue

        # Parse explicit progress indicators (main progress tracking)
        progress_match = progress_pattern.search(line)
        if progress_match:
            current, total, percentage = progress_match.groups()

            # Detect current phase for progress mapping
            current_phase = detect_phase(interface, line, phase_patterns)

            # Map backend percentage to workflow step range
            raw_percentage = float(percentage)
            mapped_percentage = map_progress_to_workflow_range(raw_percentage, current_phase)

            # Enhanced progress capping to prevent 100% during active scans
            # Detect if we're testing the final host (X/X pattern with 100%)
            is_final_host_testing = (raw_percentage >= 100.0 and current == total)

            # Apply comprehensive progress capping
            # Only allow 100% if: in reporting phase AND phase detected AND not testing final host
            allow_100_percent = (current_phase == 'reporting' and current_phase is not None and not is_final_host_testing)
            if not allow_100_percent and mapped_percentage >= 99.0:
                mapped_percentage = 98.5

            # Extract additional context if present
            auth_match = auth_success_pattern.search(line)
            if auth_match:
                success, failed = auth_match.groups()
                # Check if this is recent filtering context
                if "recent" in line.lower() or "Testing recent hosts:" in line:
                    message = f"Testing recent hosts: {current}/{total} (Success: {success}, Failed: {failed})"
                else:
                    message = f"Testing hosts: {current}/{total} (Success: {success}, Failed: {failed})"
            else:
                # Check if this is recent filtering progress
                if "Testing recent hosts:" in line or "recent hosts:" in line.lower():
                    message = f"Testing recent hosts: {current}/{total}"
                else:
                    message = f"Processing {current}/{total} hosts"

            # Validate host count parsing
            try:
                current_count = int(current)
                total_count = int(total)
                if total_count <= 0:
                    # Fallback message for invalid counts
                    message += " (⚠ Unable to determine total host count)"
            except ValueError:
                # Progress parsing worked but counts are invalid
                message += " (⚠ Progress parsing issue - check logs)"

            progress_callback(mapped_percentage, message)
            continue

        # Parse early-stage activity for immediate feedback
        shodan_match = shodan_pattern.search(line)
        if shodan_match:
            count = shodan_match.group(1)
            progress_callback(10.0, f"Shodan query found {count} potential targets")
            continue

        database_match = database_pattern.search(line)
        if database_match:
            count = database_match.group(1)
            progress_callback(5.0, f"Database loaded: {count} known servers")
            continue

        # Detect authentication testing start
        auth_start_match = auth_testing_start_pattern.search(line)
        if auth_start_match:
            count = auth_start_match.group(1)
            progress_callback(15.0, f"Starting authentication tests on {count} hosts...")
            continue

        # Parse recent filtering activity
        recent_filter_match = recent_filtering_pattern.search(line)
        if recent_filter_match:
            # Extract numbers - first is timeframe, second (if present) is host count
            numbers = recent_filter_match.groups()
            timeframe = numbers[0]
            host_count = numbers[1] if len(numbers) > 1 and numbers[1] else "some"

            if "loading" in line.lower():
                progress_callback(8.0, f"Loading hosts from last {timeframe} days...")
            elif "found" in line.lower():
                progress_callback(12.0, f"Found {host_count} hosts within recent timeframe")
            elif "testing" in line.lower():
                progress_callback(20.0, f"Testing {host_count} recent hosts...")
            continue

        # Parse skipped hosts due to recent filtering
        skipped_match = skipped_hosts_pattern.search(line)
        if skipped_match:
            count = skipped_match.group(1)
            progress_callback(5.0, f"Skipped {count} hosts (scanned within recent timeframe)")
            continue

        # Parse individual host testing for granular progress (e.g., "[5/100] Testing 192.168.1.5...")
        individual_host_match = individual_host_pattern.search(line)
        if individual_host_match:
            current, total, ip_address = individual_host_match.groups()

            try:
                current_count = int(current)
                total_count = int(total)

                # Calculate percentage within the current phase (assume authentication for individual testing)
                if total_count > 0:
                    raw_percentage = (current_count / total_count) * 100

                    # Enhanced capping for individual host testing
                    # If testing final host (X/X), cap at 99% to prevent premature 100%
                    if current_count == total_count and raw_percentage >= 100.0:
                        raw_percentage = 99.0

                    mapped_percentage = map_progress_to_workflow_range(raw_percentage, 'authentication')

                    # Cap progress to avoid reaching phase end
                    if mapped_percentage >= 24.5:  # Authentication phase ends at 25%
                        mapped_percentage = 24.0

                    message = f"Testing {current}/{total}: {ip_address}"
                    progress_callback(mapped_percentage, message)
                    continue

            except ValueError:
                # Invalid counts - continue without error
                pass

        # Determine current phase for context
        current_phase = detect_phase(interface, line, phase_patterns)

        # Parse detailed progress based on enhanced patterns
        detailed_progress = parse_detailed_progress(line, {
            'host_progress': host_progress_pattern,
            'share_progress': share_progress_pattern,
            'auth_success': auth_success_pattern
        })

        if detailed_progress:
            percentage, message = detailed_progress
            progress_callback(percentage, message)
            continue

        # Parse general status messages with improved context
        status_match = status_pattern.search(line)
        if status_match:
            icon, message = status_match.groups()
            # Estimate progress based on phase, icon, and keywords
            percentage = estimate_progress_from_status(message, current_phase, icon)
            # Only report if we have meaningful progress to show
            if percentage is not None and percentage > 0:
                progress_callback(percentage, message)


def detect_phase(interface, line: str, phase_patterns: Dict) -> Optional[str]:
    """
    Enhanced phase detection with persistence and inference.

    Args:
        interface: BackendInterface instance
        line: Output line to analyze
        phase_patterns: Dictionary of phase patterns

    Returns:
        Detected phase name, persisted phase, or inferred phase
    """
    # Try direct pattern matching first
    for phase, pattern in phase_patterns.items():
        if pattern.search(line):
            interface.last_known_phase = phase  # Update persistent phase
            return phase

    # If no direct match, try to infer from progress indicators and context
    if "📊 Progress:" in line:
        # Infer phase from progress context
        if "Testing SMB authentication" in line or "authentication" in line.lower():
            interface.last_known_phase = 'authentication'
            return 'authentication'
        elif "Testing" in line or "Processing" in line:
            # Most likely access testing if we're testing/processing hosts
            interface.last_known_phase = 'access_testing'
            return 'access_testing'

    # Use persisted phase if available (phases tend to persist for multiple lines)
    if interface.last_known_phase:
        return interface.last_known_phase

    # Fallback: infer phase from percentage if no context available
    return infer_phase_from_context(line)


def infer_phase_from_context(line: str) -> Optional[str]:
    """
    Infer phase from line context when direct detection fails.

    Args:
        line: Output line to analyze

    Returns:
        Inferred phase or None
    """
    line_lower = line.lower()

    # Simple keyword-based inference for common cases
    if any(keyword in line_lower for keyword in ['shodan', 'query', 'discovery']):
        return 'discovery'
    elif any(keyword in line_lower for keyword in ['authentication', 'auth', 'login']):
        return 'authentication'
    elif any(keyword in line_lower for keyword in ['testing', 'processing', 'host']):
        return 'access_testing'  # Most common phase
    elif any(keyword in line_lower for keyword in ['collection', 'enumeration', 'share']):
        return 'collection'
    elif any(keyword in line_lower for keyword in ['report', 'complete', 'summary']):
        return 'reporting'

    return None  # Let caller handle this case


def calculate_workflow_step_percentage(step_num: int, total_steps: int) -> float:
    """
    Calculate progress percentage based on workflow step.

    Maps workflow steps to progress ranges (Updated for realistic timing):
    - Step 1 (Discovery): 5-25% (includes Shodan: 5-15%, Authentication: 15-25%)
    - Step 2 (Access Testing): 25-80% (Expanded - longest phase)
    - Step 3 (Collection): 80-95%
    - Step 4 (Reporting): 95-100%

    Args:
        step_num: Current step number (1-based)
        total_steps: Total number of steps

    Returns:
        Progress percentage for step start
    """
    if total_steps == 4:  # Standard workflow
        step_ranges = {1: 5.0, 2: 25.0, 3: 80.0, 4: 95.0}  # Updated ranges
        return step_ranges.get(step_num, 0.0)
    else:
        # Generic calculation for non-standard workflows
        return ((step_num - 1) / total_steps) * 100


def parse_detailed_progress(line: str, patterns: Dict) -> Optional[Tuple[float, str]]:
    """
    Parse detailed progress information from output line.

    Args:
        line: Output line to analyze
        patterns: Dictionary of progress patterns

    Returns:
        Tuple of (percentage, message) or None if no match
    """
    # Host processing progress (access testing phase)
    host_match = patterns['host_progress'].search(line)
    if host_match:
        current, total = host_match.groups()
        percentage = 25 + ((int(current) / int(total)) * 35)  # 25-60% range for access testing
        return percentage, f"Testing host {current}/{total}"

    # Share enumeration progress (collection phase)
    share_match = patterns['share_progress'].search(line)
    if share_match:
        current, total = share_match.groups()
        percentage = 60 + ((int(current) / int(total)) * 30)  # 60-90% range for collection
        return percentage, f"Enumerating share {current}/{total}"

    # Authentication success/failure tracking
    auth_match = patterns['auth_success'].search(line)
    if auth_match:
        success, failed = auth_match.groups()
        total_processed = int(success) + int(failed)
        # Return context but let main progress pattern handle percentage
        return None, f"Auth results: {success} success, {failed} failed"

    return None


def estimate_progress_from_status(message: str, phase: Optional[str], icon: str = "") -> Optional[float]:
    """
    Estimate progress percentage from status message, phase, and icon.

    Args:
        message: Status message
        phase: Current detected phase
        icon: Status icon (ℹ✓⚠✗🚀)

    Returns:
        Estimated percentage or None if no meaningful progress
    """
    message_lower = message.lower()

    # Phase-based base percentages
    phase_bases = {
        'discovery': 5,
        'authentication': 15,
        'access_testing': 30,
        'collection': 70,
        'reporting': 90
    }

    base_percentage = phase_bases.get(phase, 0)

    # Keyword-based adjustments
    if "starting" in message_lower or "initializing" in message_lower:
        return base_percentage
    elif "complete" in message_lower or "finished" in message_lower:
        return min(95, base_percentage + 20)
    elif "processing" in message_lower or "working" in message_lower:
        return base_percentage + 10
    elif "found" in message_lower:
        return base_percentage + 5
    elif "failed" in message_lower or "error" in message_lower:
        return None  # Don't estimate for errors

    return base_percentage


def map_progress_to_workflow_range(backend_percentage: float, phase: Optional[str]) -> float:
    """
    Map backend progress percentage (0-100%) to workflow step range based on detected phase.

    Workflow step ranges (Updated for realistic timing):
    - Discovery/Shodan: 0-100% → 5-15%
    - Authentication: 0-100% → 15-25%
    - Access Testing: 0-100% → 25-80% (Expanded - this is the longest phase)
    - Collection: 0-100% → 80-95%
    - Reporting: 0-100% → 95-100%

    Args:
        backend_percentage: Raw percentage from backend (0-100)
        phase: Detected phase name

    Returns:
        Mapped percentage for GUI workflow display
    """
    # Phase-specific ranges (start, end) - Updated for realistic timing
    phase_ranges = {
        'discovery': (5.0, 15.0),
        'authentication': (15.0, 25.0),
        'access_testing': (25.0, 80.0),  # Expanded - longest phase
        'collection': (80.0, 95.0),      # Reduced range
        'reporting': (95.0, 100.0)       # Reduced range
    }

    if phase not in phase_ranges:
        # Fallback behavior: never return 100% during active scans
        # Assume we're in access_testing phase (most common case) if phase unknown
        if backend_percentage >= 100.0:
            # Cap at high percentage in access_testing range to prevent premature 100%
            return 79.0  # Near end of access_testing phase (25-80%)
        else:
            # Map to access_testing range for unknown phases
            start, end = 25.0, 80.0
            range_size = end - start
            mapped = start + (backend_percentage / 100.0) * range_size
            return min(end, max(start, mapped))

    start, end = phase_ranges[phase]
    range_size = end - start

    # Map backend 0-100% to phase range
    mapped_percentage = start + (backend_percentage / 100.0) * range_size

    # Ensure we don't exceed the phase range
    return min(end, max(start, mapped_percentage))


def parse_final_results(output: str) -> Dict:
    """
    Parse final results from CLI output.

    Args:
        output: Complete CLI output text

    Returns:
        Dictionary with parsed results

    Implementation: Regex patterns extract key statistics from the
    "Discovery Results" section of CLI output.
    """
    # Strip ANSI escape sequences once for both regex extraction and success detection
    cleaned_output = re.sub(r'\x1B\[[0-9;]*m', '', output)

    results = {
        "success": False,
        "shodan_results": 0,
        "hosts_tested": 0,
        "successful_auth": 0,
        "failed_auth": 0,
        "session_id": None,
        # New keys for modern SMBSeek output format
        "hosts_scanned": 0,
        "hosts_accessible": 0,
        "accessible_shares": 0,
        "raw_output": output
    }

    # Detect explicit Shodan credit errors and surface them
    shodan_error_match = re.search(r'Shodan API error:\s*(.+)', cleaned_output, re.IGNORECASE)
    if shodan_error_match:
        results["error"] = shodan_error_match.group(0).lstrip('✗❌ ').strip()
        return results

    # Parse results section - patterns accept both emoji and plain-text variants.
    patterns = {
        # New patterns matching current output format (emoji optional)
        "hosts_scanned": r'(?:📊\s*)?Hosts Scanned:\s*(\d[\d,]*)',
        "hosts_accessible": r'(?:🔓\s*)?Hosts Accessible:\s*(\d[\d,]*)',
        "accessible_shares": r'(?:📁\s*)?Accessible (?:Shares|Directories):\s*(\d[\d,]*)',

        # Legacy patterns (for backward compatibility with older SMBSeek versions)
        "shodan_results": r'Shodan Results:\s*(\d[\d,]*)',
        "hosts_tested": r'Hosts Tested:\s*(\d[\d,]*)',
        "successful_auth": r'Successful Auth:\s*(\d[\d,]*)',
        "failed_auth": r'Failed Auth:\s*(\d[\d,]*)',
        "session_id": r'session:\s*(\d+)'
    }

    matched_stat_fields = set()
    for key, pattern in patterns.items():
        match = re.search(pattern, cleaned_output)
        if match:
            value = match.group(1).replace(',', '')  # Strip commas before int conversion
            results[key] = int(value) if value.isdigit() else value
            if key in {"hosts_scanned", "hosts_accessible", "accessible_shares", "hosts_tested", "successful_auth"}:
                matched_stat_fields.add(key)

    # Create compatibility mappings for backward compatibility and flexible field access
    # Map new format fields to legacy field names for existing code
    if results["hosts_scanned"] > 0 and results["hosts_tested"] == 0:
        results["hosts_tested"] = results["hosts_scanned"]

    if results["hosts_accessible"] > 0 and results["successful_auth"] == 0:
        results["successful_auth"] = results["hosts_accessible"]

    # Map legacy fields to new format if only legacy fields were found
    if results["hosts_tested"] > 0 and results["hosts_scanned"] == 0:
        results["hosts_scanned"] = results["hosts_tested"]

    if results["successful_auth"] > 0 and results["hosts_accessible"] == 0:
        results["hosts_accessible"] = results["successful_auth"]

    # Ensure shares_discovered field exists for scan manager compatibility
    if "shares_discovered" not in results:
        results["shares_discovered"] = results["accessible_shares"]

    # Check for success indicators using cleaned output (emoji optional).
    if ("SMBSeek security assessment completed successfully" in cleaned_output or
        ("✓ Found" in cleaned_output and "accessible SMB servers" in cleaned_output) or
        "✓ Discovery completed:" in cleaned_output or
        "FTP scan completed successfully" in cleaned_output or
        "HTTP scan completed successfully" in cleaned_output):
        results["success"] = True

    # Add validation and debug logging for parsing results
    debug_enabled = os.getenv("XSMBSEEK_DEBUG_PARSING")
    parsing_success = bool(matched_stat_fields) or any(
        results[key] > 0 for key in ["hosts_scanned", "hosts_tested", "hosts_accessible", "successful_auth"]
    )

    if debug_enabled or not parsing_success:
        # Log parsing results for debugging
        parsed_fields = {k: v for k, v in results.items() if k != "raw_output" and isinstance(v, (int, str))}

        if debug_enabled:
            _logger.debug("Parse results: %s", parsed_fields)

        if not parsing_success:
            _logger.warning("CLI output parsing failed to extract meaningful statistics.")
            _logger.warning("Parsed values: %s", parsed_fields)
            # Show a snippet of the output for debugging
            output_lines = cleaned_output.split('\n')
            relevant_lines = [line for line in output_lines if any(keyword in line.lower()
                             for keyword in ['hosts', 'scanned', 'accessible', 'shares', 'found', 'results'])]
            if relevant_lines:
                _logger.warning("Relevant output lines: %s", relevant_lines[:5])

    # Fallback: also consider it success if we actually parsed scan results
    # This handles cases where CLI output format changed but data was parsed
    if not results["success"] and (results["hosts_scanned"] > 0 or results["hosts_accessible"] > 0):
        results["success"] = True
        if debug_enabled:
            _logger.debug("Set success=True via fallback (hosts_scanned=%d, hosts_accessible=%d)",
                         results['hosts_scanned'], results['hosts_accessible'])

    if debug_enabled:
        _logger.debug("Final success value: %s", results['success'])

    return results


def parse_summary_output(output: str) -> Dict:
    """
    Parse database summary output.

    Args:
        output: CLI summary output

    Returns:
        Dictionary with summary statistics
    """
    # Default values
    summary = {
        "total_servers": 0,
        "accessible_shares": 0,
        "vulnerabilities": 0,
        "recent_discoveries": {
            "display": "--",
            "warning": "Cannot load recent scan results"
        }
    }

    # Parse summary statistics from output
    # This would need to match the actual CLI output format
    lines = output.split('\n')
    for line in lines:
        if "servers" in line.lower():
            numbers = re.findall(r'\d+', line)
            if numbers:
                summary["total_servers"] = int(numbers[0])
        elif "shares" in line.lower():
            numbers = re.findall(r'\d+', line)
            if numbers:
                summary["accessible_shares"] = int(numbers[0])
        elif "vulnerabilities" in line.lower():
            numbers = re.findall(r'\d+', line)
            if numbers:
                summary["vulnerabilities"] = int(numbers[0])

    return summary
