#!/usr/bin/env python3
"""
Static ordering test for MEZO workflows.
Verifies that terminal failure/cancel reporting comes BEFORE final cleanup.
"""
import re
import sys
from pathlib import Path

# Workflows that should have terminal reporting before cleanup
WORKFLOWS_WITH_TERMINAL_REPORTING = [
    "port.yml",
    "port-core.yml",
    "port-coloros.yml",
    "port-oxygenos.yml",
    "port-realmeui.yml",
    "lite-core.yml",
    "custom-ninja.yml",
    "custom-legend.yml",
    "custom-gamingplus.yml",
]

def get_step_order(workflow_path: Path) -> list:
    """Extract step names and their line numbers."""
    content = workflow_path.read_text(encoding='utf-8')
    steps = []
    
    # Find all step names with their line numbers
    pattern = r'^      - name: (.+)$'
    for i, line in enumerate(content.split('\n'), 1):
        match = re.match(pattern, line)
        if match:
            steps.append({
                'name': match.group(1).strip(),
                'line': i
            })
    return steps

def check_ordering(workflow_name: str, workflow_path: Path) -> tuple:
    """Check if failure/cancel reporting comes before cleanup."""
    steps = get_step_order(workflow_path)
    
    cleanup_line = None
    failure_report_line = None
    cancel_report_line = None
    
    for step in steps:
        name = step['name'].lower()
        line = step['line']
        
        if 'cleanup' in name:
            cleanup_line = line
            
        if 'report failed' in name or 'failed notification' in name:
            failure_report_line = line
            
        if 'report cancelled' in name or 'cancelled notification' in name:
            cancel_report_line = line
    
    # Check ordering
    issues = []
    
    if cleanup_line and failure_report_line:
        if cleanup_line < failure_report_line:
            issues.append(f"FAILURE REPORT (line {failure_report_line}) comes AFTER CLEANUP (line {cleanup_line})")
    
    if cleanup_line and cancel_report_line:
        if cleanup_line < cancel_report_line:
            issues.append(f"CANCEL REPORT (line {cancel_report_line}) comes AFTER CLEANUP (line {cleanup_line})")
    
    if issues:
        return False, "; ".join(issues)
    
    return True, "PASS"

def main():
    # Get the workflows directory relative to this script
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent.parent  # Go up from .github/scripts to repo root
    workflows_dir = repo_root / ".github" / "workflows"
    
    print("=" * 60)
    print("  MEZO Workflow Static Ordering Test")
    print("=" * 60)
    print()
    print(f"  Workflows directory: {workflows_dir}")
    print()
    
    all_passed = True
    
    for workflow_name in WORKFLOWS_WITH_TERMINAL_REPORTING:
        workflow_path = workflows_dir / workflow_name
        
        if not workflow_path.exists():
            print(f"  {workflow_name}: FILE NOT FOUND")
            all_passed = False
            continue
        
        passed, message = check_ordering(workflow_name, workflow_path)
        
        status = "PASS" if passed else "FAIL"
        print(f"  {workflow_name}: {status}")
        if not passed:
            print(f"    -> {message}")
            all_passed = False
    
    print()
    print("=" * 60)
    
    if all_passed:
        print("  ALL WORKFLOWS PASSED ORDERING TEST")
        return 0
    else:
        print("  SOME WORKFLOWS FAILED ORDERING TEST")
        return 1

if __name__ == "__main__":
    sys.exit(main())
