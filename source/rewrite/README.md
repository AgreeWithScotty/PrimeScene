# Prime Scene application sources

Prime Scene 0.1.27 uses a direct xbmc.python.script application entry with provides=video, retaining permanent ID plugin.video.primeinterface. Kodi lists it in Video add-ons and executes its dedicated window without opening the native Videos directory. Settings and service actions use script dispatch. Genuine plugin playback callback arguments and handles are preserved.

The launch design was checked against the official latest stable Kodi release, 21.3-Omega: AddonBuiltins RunAddon, AddonsDirectory script video listing, PluginDirectory resolver fallback, and ScriptRunner argument/handle handling. Existing playback Python source is unchanged. The supplied recording shows the previous native blue screen before the window opens. No mocked Kodi or real-device launch tests were run for this change.

Launch diagnostics record invocation kind, active window IDs, UI readiness and true failures. Legacy saved shortcuts explicitly activating Videos are identified separately. Detailed sign-in diagnostics and account controls remain included. Full real-account login success remains unverified. Complete application source and license are in the release ZIP; other inherited backend components remain present.
