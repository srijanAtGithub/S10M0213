import subprocess
import shutil
import pytest

from Tests.conftest import PROJECT_ROOT


# Trivy — vulnerability & secret scanning (external tool, optional gate)
#
# Trivy is a standalone binary, not a pip package — install separately
# (e.g. `brew install trivy`, or see https://aquasecurity.github.io/trivy).
# Both tests auto-skip with a clear reason if it isn't found, so the suite
# still runs fine on machines without it installed.
TRIVY_AVAILABLE = shutil.which("trivy") is not None
 
 
@pytest.mark.skipif(not TRIVY_AVAILABLE, reason="trivy is not installed — see https://aquasecurity.github.io/trivy")
def test_no_high_or_critical_dependency_vulnerabilities():
    """
    Scans dependency files (requirements.txt / pyproject.toml etc.) in the
    project for known CVEs. Fails if any HIGH or CRITICAL severity
    vulnerability is found in a dependency.
    """
    result = subprocess.run(
        ["trivy", "fs", "--scanners", "vuln", "--severity", "HIGH,CRITICAL",
         "--exit-code", "1", "--quiet", str(PROJECT_ROOT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Trivy found HIGH/CRITICAL vulnerabilities:\n{result.stdout}"
 
 
@pytest.mark.skipif(not TRIVY_AVAILABLE, reason="trivy is not installed — see https://aquasecurity.github.io/trivy")
def test_no_secrets_committed():
    """
    Scans the project for accidentally hardcoded secrets (API keys, tokens).
    Relevant here since this project handles OPENAI_API_KEY,
    TELEGRAM_BOT_TOKEN, and OAuth tokens across .env / settings.json / Auth/.
    """
    result = subprocess.run(
        ["trivy", "fs", "--scanners", "secret", "--exit-code", "1", "--quiet", str(PROJECT_ROOT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Trivy found exposed secrets:\n{result.stdout}"
