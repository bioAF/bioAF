/**
 * The authenticated client accepts only application-relative paths. Browsers normalize
 * backslashes and strip tab/newline/control characters, so "/\evil.example" or "/\t/evil.example"
 * would resolve to another origin while still carrying the session credential (owner review).
 */
import { apiUrl } from "./apiUrl";

const realEnv = process.env.NEXT_PUBLIC_API_URL;
afterEach(() => {
  if (realEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = realEnv;
});

test.each([
  "/\\evil.example/x",
  "/\\\\evil.example/x",
  "/\t/evil.example/x",
  "/\n/evil.example/x",
  "/\r/evil.example/x",
  "/\u0000/evil.example",
  "/\u001f/evil.example",
  "/\u007f/evil.example",
  "\t//evil.example/x",
  "//evil.example/x",
  "https://evil.example/x",
  "api/projects",
  "",
])("refuses %p, which a browser could send to another origin", (path) => {
  for (const base of ["", "https://api.lab.org", undefined]) {
    if (base === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = base;
    expect(() => apiUrl(path)).toThrow();
  }
});

test.each([
  "/api/projects",
  "/api/projects?search=a b&status=active",
  "/api/backups/tfstate-download/envs%2Fprod%2Fdefault.tfstate",
  "/api/files/7/content?token=abc",
  "/api/projects?search=C:\\data",
])("accepts the application path %p", (path) => {
  process.env.NEXT_PUBLIC_API_URL = "";
  expect(apiUrl(path)).toBe(path);
});
