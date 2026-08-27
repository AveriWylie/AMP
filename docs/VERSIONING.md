# Versioning

AMP versions and Minecraft versions describe different things.

## AMP versions

AMP uses Semantic Versioning after 1.0.0:

- A major release changes or removes a supported AMP interface or behavior.
- A minor release adds backward-compatible functionality, including support for
  a newly verified Minecraft version.
- A patch release fixes existing behavior or data without adding a newly
  advertised Minecraft version.

Release tags use `vMAJOR.MINOR.PATCH`. The matching changelog heading omits the
`v` prefix.

## Minecraft compatibility

Minecraft compatibility is recorded separately in
`amp/protocol/version_support.json` and summarized in the root README. Support
is declared for an exact Minecraft release, not inferred from the AMP version.

Multiple Minecraft releases may share a protocol number or protocol-family
adapter while using different data versions and registries. Sharing a protocol
family does not make an untested release supported.

Snapshots, release candidates, and pre-releases are not supported targets. A
stable Minecraft release becomes supported only after its generated data,
protocol fixtures, offline tests, and live gameplay checks pass.

## New Minecraft releases

A Mojang release must not publish a new AMP release automatically. The scheduled
automation opens or updates a candidate pull request that:

1. Records the untracked stable Minecraft release and its official metadata.
2. Lists generated data, protocol review, offline tests, and live gameplay as
   mandatory promotion gates.

A maintainer must then review the protocol differences and run the complete
live gameplay matrix. Only a reviewed implementation change may move the release
from the candidate record into the support manifest. Publishing AMP remains a
separate maintainer decision governed by [Releasing](RELEASING.md).
