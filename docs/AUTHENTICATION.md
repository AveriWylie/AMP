# Authentication status

AMP 1.0 supports direct Minecraft Java servers running in offline mode. Microsoft-authenticated online-mode servers and Java Realms are disabled.

## Why the functionality is disabled

AMP implements Microsoft device authorization, Xbox and XSTS exchange, the Minecraft launcher-token exchange, Java ownership and profile checks, encrypted online-mode login, session-server join, and Realm discovery. The implementation and its offline tests remain in the repository.

The live release gate cannot pass with AMP's Microsoft application, however. Microsoft accepts its OAuth, Xbox, and XSTS requests, but Minecraft Services rejects the application at the launcher-token exchange with HTTP 403 because the client ID has not been approved for Minecraft Services. This authorization is controlled outside Microsoft Entra; adding API permissions, redirect URIs, or a client secret does not grant it.

Microsoft no longer provides a public self-service Minecraft application-registration form. AMP will not ship another application's approved client ID, present itself as another launcher or game, or advertise authenticated server and Realm support that its own identity cannot use.

## Activation gate

Authenticated servers and Realms may be enabled only after all of the following are true:

1. Microsoft or Mojang explicitly authorizes AMP's client ID for Minecraft Services.
2. The authenticated dedicated-server check reaches Play using an entitled Java account.
3. The non-destructive Realm check resolves an accessible Realm, reaches a positioned Play state, and disconnects without gameplay actions.
4. The supported-version manifest and release documentation record the live evidence.

Until then, `tools/check_online_login.py` and `tools/check_realm.py` exit with an explanatory error. Their retained gate implementations are not part of AMP 1.0's supported interface.
