### Internal

- Rewrite the stack-deploy error sanitizer to return the matched allowlist constant rather than the input string. Output to the client is identical, but CodeQL now recognises the result as untainted.
