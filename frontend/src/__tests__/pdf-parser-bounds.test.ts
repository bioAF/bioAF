/**
 * @jest-environment node
 */
import { spawn } from "child_process";
import { join } from "path";
import { HARDENED_PDF_OPTIONS } from "@/lib/pdfSecurity";

/**
 * Every PDF the viewers open is untrusted: lab documents are user uploads and
 * papers come from external publishers. The viewer tests mock pdf.js, so they
 * cannot show what the real parser does with a hostile or broken file. This
 * suite feeds the installed pdf.js the same work a viewer asks of it (open the
 * document with the hardened options, then parse every page's content) and
 * requires each file to finish, opened or rejected, inside a fixed deadline.
 *
 * The parse runs in a child process, not in Jest's thread: a parser stuck in a
 * loop or a backtracking regular expression never yields, so only killing the
 * process can enforce the deadline. pdf.js's legacy build is the one that runs
 * in Node; the browser build shares its parser.
 */

const DEADLINE_MS = 5000;
const PROBE = join(__dirname, "..", "testing", "pdfParseProbe.mjs");

type ProbeResult =
  | { outcome: "opened"; numPages: number; pagesParsed: number }
  | { outcome: "rejected"; name: string; message: string }
  | { outcome: "deadline" }
  | { outcome: "crashed"; code: number | null; stderr: string };

function probe(bytes: Uint8Array): Promise<ProbeResult & { elapsedMs: number }> {
  const started = Date.now();
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [PROBE], { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, DEADLINE_MS);
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("close", (code) => {
      clearTimeout(timer);
      const elapsedMs = Date.now() - started;
      if (timedOut) return resolve({ outcome: "deadline", elapsedMs });
      const line = stdout.trim().split("\n").pop() ?? "";
      try {
        resolve({ ...(JSON.parse(line) as ProbeResult), elapsedMs });
      } catch {
        resolve({ outcome: "crashed", code, stderr, elapsedMs });
      }
    });
    child.stdin.end(
      JSON.stringify({ data: Buffer.from(bytes).toString("base64"), options: HARDENED_PDF_OPTIONS }),
    );
  });
}

/** Assemble a PDF from numbered object bodies, with a correct xref table. */
function buildPdf(objects: string[], trailerExtra = ""): Uint8Array {
  let out = "%PDF-1.7\n";
  const offsets: number[] = [];
  objects.forEach((body, i) => {
    offsets.push(Buffer.byteLength(out, "latin1"));
    out += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = Buffer.byteLength(out, "latin1");
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) out += `${String(off).padStart(10, "0")} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R ${trailerExtra}>>\nstartxref\n${xref}\n%%EOF\n`;
  return new Uint8Array(Buffer.from(out, "latin1"));
}

function stream(content: string, dict = ""): string {
  return `<< /Length ${Buffer.byteLength(content, "latin1")} ${dict}>>\nstream\n${content}\nendstream`;
}

const TEXT_PAGE = (n: number) => `BT /F1 24 Tf 72 720 Td (Page ${n}) Tj ET`;

/** A plain two-page document with text in a standard font. */
function validTwoPagePdf(): Uint8Array {
  return buildPdf([
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R /Resources << /Font << /F1 7 0 R >> >> >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R /Resources << /Font << /F1 7 0 R >> >> >>",
    stream(TEXT_PAGE(1)),
    stream(TEXT_PAGE(2)),
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ]);
}

function expectWithinDeadline(result: { outcome: string; elapsedMs: number }) {
  expect(result.outcome).not.toBe("deadline");
  expect(result.outcome).not.toBe("crashed");
  expect(result.elapsedMs).toBeLessThan(DEADLINE_MS);
}

// Each case spawns Node and loads pdf.js, which takes about a second cold.
jest.setTimeout(DEADLINE_MS * 3);

test("a well-formed document opens and every page parses", async () => {
  const result = await probe(validTwoPagePdf());
  expectWithinDeadline(result);
  expect(result).toMatchObject({ outcome: "opened", numPages: 2, pagesParsed: 2 });
});

test("bytes that are not a PDF are rejected with a PDF error", async () => {
  const result = await probe(new TextEncoder().encode("%PDF-1.4\nthis is not a pdf\n%%EOF"));
  expectWithinDeadline(result);
  expect(result).toMatchObject({ outcome: "rejected", name: "InvalidPDFException" });
});

test("an empty file is rejected", async () => {
  const result = await probe(new Uint8Array());
  expectWithinDeadline(result);
  expect(result.outcome).toBe("rejected");
});

test("a document cut off half way finishes inside the deadline", async () => {
  const whole = validTwoPagePdf();
  const result = await probe(whole.slice(0, Math.floor(whole.length / 2)));
  expectWithinDeadline(result);
});

test("a page tree that contains itself finishes inside the deadline", async () => {
  const result = await probe(
    buildPdf([
      "<< /Type /Catalog /Pages 2 0 R >>",
      "<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>",
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]),
  );
  expectWithinDeadline(result);
});

test("a form field whose parent chain loops finishes inside the deadline", async () => {
  // Widget 4 -> field 5 -> field 6 -> field 5 ... The viewers paint widget
  // appearances on the canvas, so every page with a form walks this chain.
  const result = await probe(
    buildPdf([
      "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] >> >>",
      "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Annots [4 0 R] >>",
      "<< /Type /Annot /Subtype /Widget /Rect [72 72 200 100] /Parent 5 0 R /P 3 0 R >>",
      "<< /T (a) /FT /Tx /Parent 6 0 R /Kids [4 0 R] >>",
      "<< /T (b) /Parent 5 0 R /Kids [5 0 R] >>",
    ]),
  );
  expectWithinDeadline(result);
});

test("content nested thousands of levels deep finishes inside the deadline", async () => {
  const depth = 20000;
  const content = "[".repeat(depth) + "]".repeat(depth) + " pop";
  const result = await probe(
    buildPdf([
      "<< /Type /Catalog /Pages 2 0 R >>",
      "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
      stream(content),
    ]),
  );
  expectWithinDeadline(result);
});
