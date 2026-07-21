"""Unit tests for OIDCProvisioner (feature 009, T045 + T046 + T048)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ado2gh.api.oidc_provisioner import OIDCProvisioner, ProvisioningResult


class TestOIDCProvisionerSuccess:
    """Tests for T045: Unit test for OIDCProvisioner success."""

    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_federated_credential")
    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_repo_secrets")
    def test_provision_azure_rm_success(self, mock_secrets, mock_credential):
        """Mock GitHub API, verify federated credential and secrets created for Azure RM SC type."""
        mock_credential.return_value = "cred-id-123"
        mock_secrets.return_value = ["AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"]

        provisioner = OIDCProvisioner()
        gh_client = MagicMock()
        result = provisioner.provision(
            sc_name="Azure-Prod",
            sc_type="azurerm",
            repo_target="org/repo",
            gh_client=gh_client,
        )

        assert result.success is True
        assert result.sc_name == "Azure-Prod"
        assert result.sc_type == "azurerm"
        assert len(result.provisioned_secrets) == 3
        assert result.failure_reason == ""
        mock_credential.assert_called_once()
        mock_secrets.assert_called_once()

    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_federated_credential")
    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_repo_secrets")
    def test_provision_kubernetes_success(self, mock_secrets, mock_credential):
        """Verify federated credential and secrets created for K8s SC type."""
        mock_credential.return_value = "cred-id-456"
        mock_secrets.return_value = ["KUBE_CONFIG"]

        provisioner = OIDCProvisioner()
        gh_client = MagicMock()
        result = provisioner.provision(
            sc_name="K8s-Prod",
            sc_type="kubernetes",
            repo_target="org/repo",
            gh_client=gh_client,
        )

        assert result.success is True
        assert result.sc_type == "kubernetes"
        assert "KUBE_CONFIG" in result.provisioned_secrets

    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_federated_credential")
    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_repo_secrets")
    def test_provision_acr_success(self, mock_secrets, mock_credential):
        """Verify federated credential and secrets created for ACR SC type."""
        mock_credential.return_value = "cred-id-789"
        mock_secrets.return_value = ["ACR_USERNAME", "ACR_PASSWORD"]

        provisioner = OIDCProvisioner()
        gh_client = MagicMock()
        result = provisioner.provision(
            sc_name="ACR-Prod",
            sc_type="dockerregistry",
            repo_target="org/repo",
            gh_client=gh_client,
        )

        assert result.success is True
        assert result.sc_type == "dockerregistry"
        assert len(result.provisioned_secrets) == 2


class TestOIDCProvisionerFailure:
    """Tests for T046: Unit test for OIDCProvisioner failure fallback."""

    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_federated_credential")
    def test_api_failure_returns_failure_result(self, mock_credential):
        """Mock API failure, verify ProvisioningResult(success=False, failure_reason=...) returned."""
        mock_credential.side_effect = Exception("GitHub API error: 403 Forbidden")

        provisioner = OIDCProvisioner()
        gh_client = MagicMock()
        result = provisioner.provision(
            sc_name="Azure-Prod",
            sc_type="azurerm",
            repo_target="org/repo",
            gh_client=gh_client,
        )

        assert result.success is False
        assert result.failure_reason != ""
        assert "GitHub API error" in result.failure_reason
        assert result.operator_required is True

    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_federated_credential")
    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_repo_secrets")
    def test_secret_creation_failure_returns_failure_result(self, mock_secrets, mock_credential):
        """Verify failure when secret creation fails after credential succeeds."""
        mock_credential.return_value = "cred-id-123"
        mock_secrets.side_effect = Exception("Secret creation failed")

        provisioner = OIDCProvisioner()
        gh_client = MagicMock()
        result = provisioner.provision(
            sc_name="Azure-Prod",
            sc_type="azurerm",
            repo_target="org/repo",
            gh_client=gh_client,
        )

        assert result.success is False
        assert result.failure_reason != ""
        assert result.operator_required is True


class TestOperatorOverride:
    """Tests for T048: Unit test for operator override."""

    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_federated_credential")
    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_repo_secrets")
    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._has_operator_override")
    def test_pre_configured_mapping_skips_auto_provisioning(self, mock_override, mock_secrets, mock_credential):
        """Verify pre-configured mapping in profile skips auto-provisioning."""
        mock_override.return_value = True  # Override exists

        provisioner = OIDCProvisioner()
        gh_client = MagicMock()
        result = provisioner.provision(
            sc_name="Azure-Prod",
            sc_type="azurerm",
            repo_target="org/repo",
            gh_client=gh_client,
            profile_id="profile-1",
        )

        assert result.success is True
        assert result.skipped_override is True
        assert result.operator_required is False
        # Verify credential/secrets were NOT called
        mock_credential.assert_not_called()
        mock_secrets.assert_not_called()

    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_federated_credential")
    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._create_repo_secrets")
    @patch("ado2gh.api.oidc_provisioner.OIDCProvisioner._has_operator_override")
    def test_no_override_proceeds_with_auto_provisioning(self, mock_override, mock_secrets, mock_credential):
        """Verify auto-provisioning proceeds when no override exists."""
        mock_override.return_value = False  # No override
        mock_credential.return_value = "cred-id-123"
        mock_secrets.return_value = ["AZURE_CLIENT_ID"]

        provisioner = OIDCProvisioner()
        gh_client = MagicMock()
        result = provisioner.provision(
            sc_name="Azure-Prod",
            sc_type="azurerm",
            repo_target="org/repo",
            gh_client=gh_client,
            profile_id="profile-1",
        )

        assert result.success is True
        assert result.skipped_override is False
        # Verify credential/secrets WERE called
        mock_credential.assert_called_once()
        mock_secrets.assert_called_once()
