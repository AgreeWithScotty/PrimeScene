# Smooth Prime standard CDM bridge

These are the owned adapter/helper sources for the unchanged binary artifacts
in0.2.63. They use stockCDM10 and exact Chromium158.0.8127.37 Mojo contracts.
Kodi, ISA, Chrome and Google Widevine are not rebuilt or bundled.

The source fixture is b8c2be851f62f6a394ec57955d7f31335d7cb6d4;
CDM API d6c4e1ea4c8fc3dcd98ac3ab5a981f63067223a4;
BoringSSL ea9f3a34c3977144c45d272be81842fd891a0bdb.
The .py scripts retain their original workbench-relative paths. Place them in
an hd-license-rejection-20261125 work directory, these sources in
chrome-bridge/client-source, and prepare the exact SDK inputs with the build
scripts. Existing graph rebuilds use build_mojo_transport_client.py --incremental
--target prime_cdm_proxy and the default transport_probe target.

Chrome158 is an explicit installed-binary compatibility fixture, not a claim
that it is the newest browser. No unsupported browser ABI is enabled.
The application source license is LICENSE; third-party notices are adjacent.
