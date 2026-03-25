"""
Dirracuda Unified Workflow

Orchestrates the complete discovery → access workflow with centralized session management.
Replaces the multi-command interface with a single streamlined pipeline.
"""

import json
import sys
import os
from dataclasses import dataclass
from typing import Set, List, Dict, Any, Optional

# Add project paths for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.config import load_config
from shared.database import create_workflow_database
from shared.output import create_output_manager
from shared.results import DiscoverResult, AccessResult


@dataclass
class WorkflowSummary:
    """Final workflow summary for rollup display"""
    shodan_query: str
    hosts_scanned: int
    hosts_accessible: int
    accessible_shares: int
    session_id: int
    cautious_mode: bool


class UnifiedWorkflow:
    """
    Unified Dirracuda workflow orchestrator.

    Executes discovery → access pipeline with single session tracking
    and provides structured summary output.
    """

    def __init__(self, config, output, database, cautious_mode=False):
        """
        Initialize unified workflow.

        Args:
            config: SMBSeekConfig instance
            output: SMBSeekOutput instance
            database: SMBSeekWorkflowDatabase instance
            cautious_mode: Enable modern security hardening if True
        """
        self.config = config
        self.output = output
        self.database = database
        self.cautious_mode = cautious_mode
        self.session_id = None

    def run(self, args) -> WorkflowSummary:
        """
        Execute discovery → access workflow with single session tracking.

        Args:
            args: Parsed command line arguments

        Returns:
            WorkflowSummary with rollup statistics

        Raises:
            Exception: If workflow fails at any step
        """
        try:
            # Validate configuration
            if not self.config.validate_configuration():
                self.output.error("Configuration validation failed")
                raise RuntimeError("Configuration validation failed")

            # Show database status
            self.database.show_database_status()

            # Create single scan session for entire workflow
            self.session_id = self.database.create_session('smbseek_unified')
            self.output.print_if_verbose(f"Created workflow session {self.session_id}")

            # Execute workflow steps
            self.output.header("Dirracuda Security Assessment")

            # Display security mode banner
            if self.cautious_mode:
                self.output.info("🔒 Cautious mode enabled: requiring signed SMB sessions, SMB2+/3 only")

            # Step 1: Discovery
            discover_result = self._execute_discovery(args)

            # Step 2: Access verification (only if we found hosts)
            if discover_result.host_ips:
                access_result = self._execute_access_verification(args, discover_result.host_ips)
            else:
                self.output.warning("No authenticated hosts found - skipping share enumeration")
                access_result = AccessResult(
                    accessible_hosts=0,
                    accessible_shares=0,
                    share_details=[]
                )

            # Return structured summary
            summary = WorkflowSummary(
                shodan_query=discover_result.query_used,
                hosts_scanned=discover_result.total_hosts,
                hosts_accessible=access_result.accessible_hosts,
                accessible_shares=access_result.accessible_shares,
                session_id=self.session_id,
                cautious_mode=self.cautious_mode
            )

            self.output.success("Workflow completed successfully")
            return summary

        except Exception as e:
            self.output.error(f"Workflow failed: {e}")
            raise

        finally:
            # Cleanup database connection
            if hasattr(self.database, 'close'):
                self.database.close()

    def _execute_discovery(self, args) -> DiscoverResult:
        """
        Execute discovery operation.

        Args:
            args: Command line arguments

        Returns:
            DiscoverResult with discovery statistics
        """
        self.output.subheader("Step 1: Discovery & Authentication")

        try:
            # Import and create discovery operation
            from commands.discover import DiscoverOperation

            discover_op = DiscoverOperation(
                self.config,
                self.output,
                self.database,
                self.session_id,
                self.cautious_mode
            )

            # Execute discovery with parsed arguments
            discover_result = discover_op.execute(
                country=getattr(args, 'country', None),
                rescan_all=getattr(args, 'rescan_all', False),
                rescan_failed=getattr(args, 'rescan_failed', False),
                force_hosts=getattr(args, 'force_hosts', set()),
                custom_filters=getattr(args, 'filters', '')
            )

            self.output.success(f"Discovery completed: {discover_result.total_hosts} hosts processed, {len(discover_result.host_ips)} authenticated")

            return discover_result

        except Exception as e:
            self.output.error(f"Discovery failed: {e}")
            raise

    def _execute_access_verification(self, args, target_ips: Set[str]) -> AccessResult:
        """
        Execute access verification operation.

        Args:
            args: Command line arguments
            target_ips: Set of IPs to test for share access

        Returns:
            AccessResult with share enumeration statistics
        """
        self.output.subheader("Step 2: Share Access Verification")

        try:
            # Import and create access operation
            from commands.access import AccessOperation

            access_op = AccessOperation(
                self.config,
                self.output,
                self.database,
                self.session_id,
                self.cautious_mode,
                getattr(args, 'check_rce', False)
            )

            # Execute access verification with parsed arguments
            access_result = access_op.execute(
                target_ips=target_ips,
                recent_hours=getattr(args, 'recent', None)
            )

            self.output.success(f"Access verification completed: {access_result.accessible_hosts} hosts accessible, {access_result.accessible_shares} shares enumerated")

            return access_result

        except Exception as e:
            self.output.error(f"Access verification failed: {e}")
            raise


def create_unified_workflow(args) -> UnifiedWorkflow:
    """
    Factory function to create a configured UnifiedWorkflow instance.

    Args:
        args: Parsed command line arguments

    Returns:
        Configured UnifiedWorkflow instance
    """
    # Load configuration and shared components
    config = load_config(getattr(args, 'config', None))

    output = create_output_manager(
        config,
        quiet=getattr(args, 'quiet', False),
        verbose=getattr(args, 'verbose', False),
        no_colors=getattr(args, 'no_colors', False)
    )

    database = create_workflow_database(config, getattr(args, 'verbose', False))

    return UnifiedWorkflow(config, output, database, getattr(args, 'cautious', False))
