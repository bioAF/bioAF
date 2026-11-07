### Security

- Adding a Lab Document "From URL" now refuses an internal address written directly into the URL, such as `http://10.0.0.5/`, even when the server's DNS resolver answers that address with a public-looking one. Previously the server checked the resolver's answer instead and could go on to fetch the address.
- URL imports, and the open-access PDF downloads for literature papers that use the same downloader, now also refuse the shared address space `100.64.0.0/10`, both written into a URL and as a hostname's DNS answer. A hostname is refused if any of its DNS answers is not public.
- A malformed URL, such as an unclosed IPv6 bracket or a port that is not a number or is out of range, now gets an immediate "URL is malformed" refusal (HTTP 400) and creates no import job. Malformed redirect targets are refused the same way, before anything else is requested.
- When a paper's open-access PDF link is malformed, or its server sends an unreadable size header, bioAF now skips that copy and tries the paper's next open-access copy. Previously the error stopped the download attempt.
- Known limit: the address bioAF checks for a hostname is not pinned for the download itself, so a hostname that changes its DNS answer between the check and the download (DNS rebinding) is not yet blocked.
