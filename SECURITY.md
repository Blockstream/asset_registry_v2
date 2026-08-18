# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities privately to
[security@blockstream.com](mailto:security@blockstream.com). Do not open a
public issue for a suspected vulnerability.

PGP-encrypted email is preferred for sensitive reports. The current public key
is available at [blockstream.com/pgp.txt](https://blockstream.com/pgp.txt) with
fingerprint:

```text
1176 542D A98E 71E1 3372 2EF7 4AC8 CC88 6844 A2D6
```

You can import the key with:

```bash
gpg --keyserver hkps://keys.openpgp.org --recv-keys "1176 542D A98E 71E1 3372 2EF7 4AC8 CC88 6844 A2D6"
```

Include as much of the following as possible:

- A description of the vulnerability and how it could be exploited.
- Its potential impact, such as a privacy leak, denial of service, or theft of funds.
- Steps or code that reproduce the issue.
- A proposed remediation, if available.
- A secure way to contact you with follow-up questions.

Take care not to include private keys, personally identifiable information, or
other user data in stack traces, reproduction code, or supporting material.

Please allow at least one week for investigation and up to 90 days for a fix.
Provide reasonable advance notice before disclosing the vulnerability to
others. Investigate and report issues in good faith without disrupting the
service, its users, or dependent projects. We will coordinate with maintainers
of affected dependent projects when appropriate.
