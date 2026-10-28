# Changelog

## Unreleased

- New v2 registrations require `contract.version: 2` and a non-null
  `contract.initial_issuer_pubkey`. A missing or null contract key now returns
  HTTP 422 instead of HTTP 409.
- V2 registration rejects `contract.issuer_pubkey` and body-level
  `initial_issuer_pubkey`. Supply the issuer key only as
  `contract.initial_issuer_pubkey`. Legacy version 0 contracts continue to use
  `issuer_pubkey` through the legacy registration endpoint.
- Fixed SDK test compilation for the literal contract version type.
- Updated the SDK's transitive Handlebars test dependency to 4.7.10 to clear
  the dependency audit failure.
