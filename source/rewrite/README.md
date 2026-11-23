# Smooth Prime 0.2.39 rollback

Restores the complete 0.2.37 runtime and security policy, reversing the application/security changes in0.2.38. Every retained runtime file equals the original0.2.37 ZIP byte for byte; only release metadata/docs differ. The TLS-policy module added in0.2.38 is removed. The existing interface and0.2.37 unused-file cleanup remain. Restart Kodi after installing so loaded services use the restored code. Historical archives remain immutable.
