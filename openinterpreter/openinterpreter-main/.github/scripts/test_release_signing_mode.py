import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("resolve-release-signing-mode.sh")
VALID_SHA256 = "0123456789abcdef" * 4
MACOS_CONFIGURATION = {
    "AKV_CODESIGN_RCODESIGN_BLOB_URI": "az://account/container/rcodesign",
    "AKV_CODESIGN_RCODESIGN_SHA256": VALID_SHA256,
    "AKV_CODESIGN_PKCS11_LIBRARY_BLOB_URI": "az://account/container/provider",
    "AKV_CODESIGN_PKCS11_LIBRARY_SHA256": VALID_SHA256,
    "AKV_CODESIGN_AZURE_CLIENT_ID": "client-id",
    "AKV_CODESIGN_TENANT": "tenant-id",
    "AKV_CODESIGN_SUBSCRIPTION": "subscription-id",
    "AKV_CODESIGN_KEY_VAULT_NAME": "vault-name",
    "AKV_CODESIGN_KEY_NAME": "certificate-name",
    "AKV_NOTARIZATION_KEY_NAME": "notarization-key",
}
WINDOWS_CONFIGURATION = {
    "AZURE_ARTIFACT_SIGNING_CLIENT_ID": "client-id",
    "AZURE_ARTIFACT_SIGNING_TENANT_ID": "tenant-id",
    "AZURE_ARTIFACT_SIGNING_SUBSCRIPTION_ID": "subscription-id",
    "AZURE_ARTIFACT_SIGNING_ENDPOINT": "https://trusted-signing.example",
    "AZURE_ARTIFACT_SIGNING_ACCOUNT_NAME": "account-name",
    "AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE_NAME": "profile-name",
}


class ReleaseSigningModeTest(unittest.TestCase):
    def resolve(self, platform: str, configuration: dict[str, str]) -> str:
        environment = {"PATH": os.environ["PATH"], **configuration}
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "github-output"
            result = subprocess.run(
                ["bash", str(SCRIPT), platform, str(output_path)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return output_path.read_text(encoding="utf-8").strip()

    def test_complete_macos_configuration_selects_signed_mode(self) -> None:
        self.assertEqual(
            self.resolve("macos", MACOS_CONFIGURATION), "signing_mode=signed"
        )

    def test_complete_windows_configuration_selects_signed_mode(self) -> None:
        self.assertEqual(
            self.resolve("windows", WINDOWS_CONFIGURATION), "signing_mode=signed"
        )

    def test_missing_macos_configuration_selects_unsigned_mode(self) -> None:
        self.assertEqual(self.resolve("macos", {}), "signing_mode=unsigned")

    def test_partial_macos_configuration_selects_unsigned_mode(self) -> None:
        self.assertEqual(
            self.resolve(
                "macos",
                {
                    "AKV_CODESIGN_RCODESIGN_BLOB_URI": "az://account/container/rcodesign",
                    "AKV_CODESIGN_RCODESIGN_SHA256": "not-a-sha256",
                },
            ),
            "signing_mode=unsigned",
        )

    def test_partial_windows_configuration_selects_unsigned_mode(self) -> None:
        self.assertEqual(
            self.resolve("windows", {"AZURE_ARTIFACT_SIGNING_CLIENT_ID": "client-id"}),
            "signing_mode=unsigned",
        )


if __name__ == "__main__":
    unittest.main()
