# Wallycore Alpine build support

Wallycore 1.5.6's PyPI source distribution omits the secp256k1-zkp
`autotools-aux/m4/bitcoin_secp.m4` file, although its build invokes
Autoreconf after deleting the generated configure files. Without the macro,
the regenerated configure script contains unexpanded macro calls and fails.

`bitcoin_secp.m4` comes from libwally-core's secp256k1-zkp submodule at commit
`45f6f0f158c5ae80a2c8a53398ea4adbf19af6dc`, with trailing whitespace removed:

- Repository: `https://github.com/BlockstreamResearch/secp256k1-zkp`
- Git blob: `1428d4d9b295a8265fa1cb0455fbbeec7c7d366c`
- License: MIT

The Docker build verifies both the unmodified wallycore 1.5.6 source
distribution (against the SHA-256 recorded in `requirements.txt`) and this
macro before compiling the wheel.
