// Child-process half of src/__tests__/pdf-parser-bounds.test.ts.
//
// Reads {data: base64 PDF bytes, options: load options} on stdin, asks the
// installed pdf.js to do what a viewer does with a document (open it, then
// parse each page's content into drawing operations), and prints one JSON
// line with the outcome. The parent kills this process at its deadline, which
// is the only way to stop a parser that never yields.
//
// pdf.js 6.5 requires Node 22.13 or later and no longer polyfills
// Promise.withResolvers, which Node 20 (CI and the image today) lacks. Every
// browser the viewers support has it. Supply it here, before pdf.js loads, so
// the parser can be exercised on the current runtime; delete this once the
// frontend runtime is Node 22 or later.
if (typeof Promise.withResolvers !== "function") {
  Promise.withResolvers = function withResolvers() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };
}

const { getDocument } = await import("pdfjs-dist/legacy/build/pdf.mjs");

const MAX_PAGES = 50;

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

const { data, options } = await readStdin();
const task = getDocument({
  data: new Uint8Array(Buffer.from(data, "base64")),
  ...options,
  verbosity: 0,
});

let result;
try {
  const pdf = await task.promise;
  let pagesParsed = 0;
  for (let n = 1; n <= Math.min(pdf.numPages, MAX_PAGES); n++) {
    const page = await pdf.getPage(n);
    await page.getOperatorList();
    pagesParsed++;
  }
  result = { outcome: "opened", numPages: pdf.numPages, pagesParsed };
} catch (error) {
  result = {
    outcome: "rejected",
    name: error?.name ?? "Error",
    message: String(error?.message ?? error),
  };
} finally {
  await task.destroy();
}

process.stdout.write(`${JSON.stringify(result)}\n`);
