import pytest
from pydantic import ValidationError

from app.agent_management.catalog import (
    ENV_ALLOWLIST,
    ENV_DENYLIST,
    MAX_TIMEOUT_SECONDS,
    Diagnostic,
    environment_catalog,
    evaluate_version_constraint,
    validate_config,
    validate_env_names,
    validate_harness_type,
    validate_version_constraint,
)


def test_validate_harness_type_claude_code_ok():
    diags = validate_harness_type("claude_code")
    assert diags == []


def test_validate_harness_type_unknown_rejected():
    diags = validate_harness_type("codex_cli")
    assert any(d.code == "unsupported_harness" for d in diags)


def test_validate_config_rejects_bypass_permissions():
    diags = validate_config({"permission_mode": "bypassPermissions"})
    assert any(d.code == "bypass_permissions_forbidden" for d in diags)


def test_validate_config_accepts_default_permission():
    diags = validate_config({"permission_mode": "default"})
    assert diags == []


def test_validate_config_rejects_secret_keys():
    diags = validate_config({"api_key": "xxx"})
    assert any(d.code == "secret_value_forbidden" for d in diags)


def test_validate_config_rejects_token_password_secret_keys():
    for key in ("token", "password", "secret", "Authorization"):
        diags = validate_config({key: "xxx"})
        assert any(d.code == "secret_value_forbidden" for d in diags), key


def test_validate_config_rejects_shell_metachar_strings():
    diags = validate_config({"some_flag": "ls; rm -rf /"})
    assert any(d.code == "shell_metachar_forbidden" for d in diags)


def test_validate_config_rejects_timeout_over_max():
    diags = validate_config({"timeout_seconds": MAX_TIMEOUT_SECONDS + 1})
    assert any(d.code == "timeout_exceeds_max" for d in diags)


def test_validate_config_rejects_non_positive_timeout():
    diags = validate_config({"timeout_seconds": 0})
    assert any(d.code == "timeout_invalid" for d in diags)


def test_validate_config_rejects_unknown_permission_mode():
    diags = validate_config({"permission_mode": "yolo"})
    assert any(d.code == "unknown_permission_mode" for d in diags)


def test_validate_env_names_rejects_denylist():
    diags = validate_env_names(["LD_PRELOAD"])
    assert any(d.code == "env_denied" for d in diags)


def test_validate_env_names_denylist_overrides_allowlist():
    # PATH is in denylist; even if present, must be rejected.
    diags = validate_env_names(["PATH"])
    assert any(d.code == "env_denied" for d in diags)


def test_validate_env_names_rejects_neomua_prefix():
    diags = validate_env_names(["NEOMUA_SECRET"])
    assert any(d.code == "env_denied" for d in diags)


def test_validate_env_names_rejects_non_allowlisted():
    diags = validate_env_names(["HOME"])
    assert any(d.code == "env_not_allowlisted" for d in diags)


def test_validate_env_names_accepts_allowlisted():
    diags = validate_env_names(["ANTHROPIC_MODEL"])
    assert diags == []


def test_environment_catalog_returns_three_lists():
    cat = environment_catalog()
    assert "allowlist" in cat and "reserved" in cat and "denylist" in cat
    assert "LD_PRELOAD" in cat["denylist"]
    assert "ANTHROPIC_MODEL" in cat["allowlist"]


def test_validate_version_constraint_accepts_semver_expr():
    assert validate_version_constraint(">=1.0.0") == []
    assert validate_version_constraint(">=1.0.0,<2.0.0") == []


def test_validate_version_constraint_rejects_empty():
    diags = validate_version_constraint("")
    assert any(d.code == "invalid_version_constraint" for d in diags)


def test_validate_version_constraint_rejects_garbage():
    diags = validate_version_constraint("rm -rf /")
    assert any(d.code == "invalid_version_constraint" for d in diags)


def test_evaluate_version_constraint_geq_ok():
    assert evaluate_version_constraint("1.0.0", ">=1.0.0") is True


def test_evaluate_version_constraint_geq_violated():
    assert evaluate_version_constraint("1.0.0", ">=2.0.0") is False


def test_evaluate_version_constraint_range_ok():
    assert evaluate_version_constraint("1.5.0", ">=1.0.0,<2.0.0") is True


def test_evaluate_version_constraint_range_violated():
    assert evaluate_version_constraint("2.5.0", ">=1.0.0,<2.0.0") is False


def test_evaluate_version_constraint_eq_ok():
    assert evaluate_version_constraint("1.0.0", "==1.0.0") is True


def test_evaluate_version_constraint_bare_version_is_eq():
    # A bare version with no operator is treated as ==.
    assert evaluate_version_constraint("1.0.0", "1.0.0") is True


def test_evaluate_version_constraint_neq_violated():
    assert evaluate_version_constraint("1.0.0", "!=1.0.0") is False


def test_evaluate_version_constraint_empty_constraint_passes():
    # An empty constraint imposes no restriction.
    assert evaluate_version_constraint("1.0.0", "") is True


def test_diagnostic_model_fields():
    d = Diagnostic(code="x", field="f", message="m")
    assert d.code == "x" and d.field == "f" and d.message == "m"
