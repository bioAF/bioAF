### Security

- Replace the unmaintained `python-jose` JWT library with `PyJWT`. The previous
  dependency pulled in `python-ecdsa`, which is subject to the Minerva timing
  attack on P-256 and has no upstream fix planned. The platform only signs
  HS256 tokens, so the swap is behaviour-preserving.
- Pin nested `postcss` to `>=8.5.10` via an npm `overrides` entry to clear the
  XSS-via-unescaped-`</style>` advisory that surfaced through Next.js's bundled
  copy of `postcss@8.4.31`.
