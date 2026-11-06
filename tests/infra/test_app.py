"""Tests for infra/app.py: the deploy's context (skipped without aws-cdk-lib, an infra-only dependency)."""
from __future__ import annotations

import pytest

cdk = pytest.importorskip("aws_cdk")
from infra.app import config_from  # noqa: E402


def test_house_and_alert_email_are_required_and_the_rest_default():
    with pytest.raises(SystemExit, match="bootstrap, synth and deploy need -c house=... -c alert_email=..."):
        config_from(cdk.App())
    config = config_from(cdk.App(context={"house": "113", "alert_email": "a@example.com"}))
    assert (config.market, config.ruleset, config.house, config.alert_email) == ("forever.normal.horde.us", 2, 113, "a@example.com")
    assert config.asset_path.endswith("build/lambda") or config.asset_path.endswith("build\\lambda")
