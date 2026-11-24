import {
  PDF_DEADLINES,
  createPdfEngine,
  PdfDeadlineError,
  openPdfSession,
  drawPageWithDeadline,
  type PdfEngine,
} from "./pdfSession";

/**
 * pdf.js stops a document by asking its worker to terminate and waiting for
 * the answer. A worker stuck inside a hostile file never answers, so a plain
 * destroy() leaves it running. The session owns the worker and terminates it
 * itself when the polite stop does not finish in time.
 */

function fakeEngine(opts: { opens?: Promise<unknown>; destroy?: () => Promise<void> } = {}) {
  const worker = { destroy: jest.fn() };
  const task = {
    promise: opts.opens ?? Promise.resolve({ numPages: 1 }),
    destroy: jest.fn(opts.destroy ?? (() => Promise.resolve())),
  };
  const engine: PdfEngine<unknown> = {
    createWorker: jest.fn(() => worker),
    open: jest.fn(() => task),
  };
  return { engine, worker, task };
}

const never = <T>() => new Promise<T>(() => {});

beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("the document opens on the session's own worker", async () => {
  const { engine, worker } = fakeEngine();
  const session = openPdfSession(engine, new ArrayBuffer(1));
  await expect(session.document).resolves.toEqual({ numPages: 1 });
  expect(engine.open).toHaveBeenCalledWith(expect.any(ArrayBuffer), worker);
});

test("stopping destroys the document, then terminates the worker", async () => {
  const { engine, worker, task } = fakeEngine();
  const session = openPdfSession(engine, new ArrayBuffer(1));
  await session.document;
  session.stop();
  expect(task.destroy).toHaveBeenCalled();
  await jest.advanceTimersByTimeAsync(0);
  expect(worker.destroy).toHaveBeenCalledTimes(1);
});

test("a worker that never answers the stop is terminated after the grace period", async () => {
  const { engine, worker, task } = fakeEngine({ destroy: never });
  const session = openPdfSession(engine, new ArrayBuffer(1));
  await session.document;
  session.stop();
  expect(task.destroy).toHaveBeenCalled();

  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs - 1);
  expect(worker.destroy).not.toHaveBeenCalled();
  await jest.advanceTimersByTimeAsync(1);
  expect(worker.destroy).toHaveBeenCalledTimes(1);
});

test("a stop that fails still terminates the worker and logs the failure", async () => {
  const { engine, worker } = fakeEngine({ destroy: () => Promise.reject(new Error("port closed")) });
  const session = openPdfSession(engine, new ArrayBuffer(1));
  await session.document;
  session.stop();
  await jest.advanceTimersByTimeAsync(0);
  expect(worker.destroy).toHaveBeenCalledTimes(1);
  expect(console.error).toHaveBeenCalledWith(expect.stringContaining("stopping"), expect.any(Error));
});

test("stopping twice terminates the worker once", async () => {
  const { engine, worker, task } = fakeEngine();
  const session = openPdfSession(engine, new ArrayBuffer(1));
  session.stop();
  session.stop();
  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs);
  expect(task.destroy).toHaveBeenCalledTimes(1);
  expect(worker.destroy).toHaveBeenCalledTimes(1);
});

test("a document that does not open in time fails and its worker is terminated", async () => {
  const { engine, worker } = fakeEngine({ opens: never(), destroy: never });
  const session = openPdfSession(engine, new ArrayBuffer(1));
  const outcome = session.document.catch((e) => e);

  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.loadMs);
  expect(await outcome).toBeInstanceOf(PdfDeadlineError);
  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs);
  expect(worker.destroy).toHaveBeenCalledTimes(1);
});

test("a document that fails to open terminates its worker", async () => {
  const { engine, worker } = fakeEngine({ opens: Promise.reject(new Error("Invalid PDF structure.")) });
  const session = openPdfSession(engine, new ArrayBuffer(1));
  await expect(session.document).rejects.toThrow("Invalid PDF structure.");
  await jest.advanceTimersByTimeAsync(0);
  expect(worker.destroy).toHaveBeenCalledTimes(1);
});

test("an engine that throws while opening fails the document and terminates the worker", async () => {
  const { engine, worker } = fakeEngine();
  (engine.open as jest.Mock).mockImplementation(() => {
    throw new TypeError("Cannot perform Construct on a detached ArrayBuffer");
  });
  let session: ReturnType<typeof openPdfSession> | undefined;
  expect(() => (session = openPdfSession(engine, new ArrayBuffer(1)))).not.toThrow();
  await expect(session!.document).rejects.toThrow("detached ArrayBuffer");
  await jest.advanceTimersByTimeAsync(0);
  expect(worker.destroy).toHaveBeenCalledTimes(1);
  session!.stop();
  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs);
  expect(worker.destroy).toHaveBeenCalledTimes(1);
});

test("an engine that cannot create a worker fails the document instead of throwing", async () => {
  const { engine } = fakeEngine();
  (engine.createWorker as jest.Mock).mockImplementation(() => {
    throw new Error("Worker blocked");
  });
  let session: ReturnType<typeof openPdfSession> | undefined;
  expect(() => (session = openPdfSession(engine, new ArrayBuffer(1)))).not.toThrow();
  await expect(session!.document).rejects.toThrow("Worker blocked");
  expect(engine.open).not.toHaveBeenCalled();
  expect(() => session!.stop()).not.toThrow();
});

test("a page whose extraction never finishes fails at the page deadline", async () => {
  const outcome = drawPageWithDeadline(async () => {
    await never<void>(); // getPage() stuck in the worker
  }).catch((e) => e);
  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.renderMs);
  expect(await outcome).toBeInstanceOf(PdfDeadlineError);
});

test("one page deadline covers extraction and drawing together", async () => {
  const render = { promise: never<void>(), cancel: jest.fn() };
  const outcome = drawPageWithDeadline(async (track) => {
    await new Promise((r) => setTimeout(r, PDF_DEADLINES.renderMs - 10)); // slow getPage()
    track(render);
    await render.promise;
  }).catch((e) => e);
  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.renderMs - 1);
  expect(render.cancel).not.toHaveBeenCalled();
  await jest.advanceTimersByTimeAsync(1);
  expect(await outcome).toBeInstanceOf(PdfDeadlineError);
  expect(render.cancel).toHaveBeenCalled();
});

test("a page drawing that does not finish in time is cancelled and fails", async () => {
  const render = { promise: never<void>(), cancel: jest.fn() };
  const outcome = drawPageWithDeadline(async (track) => {
    track(render);
    await render.promise;
  }).catch((e) => e);
  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.renderMs);
  expect(await outcome).toBeInstanceOf(PdfDeadlineError);
  expect(render.cancel).toHaveBeenCalled();
});

test("a page drawing that finishes in time succeeds", async () => {
  const render = { promise: Promise.resolve(), cancel: jest.fn() };
  await expect(
    drawPageWithDeadline(async (track) => {
      track(render);
      await render.promise;
    }),
  ).resolves.toBeUndefined();
  expect(render.cancel).not.toHaveBeenCalled();
});

describe("createPdfEngine", () => {
  const webWorkers: Array<{ url: unknown; options: unknown; terminate: jest.Mock }> = [];
  const realWorker = global.Worker;
  beforeAll(() => {
    global.Worker = class {
      terminate = jest.fn();
      constructor(
        public url: unknown,
        public options: unknown,
      ) {
        webWorkers.push(this);
      }
    } as unknown as typeof Worker;
  });
  afterAll(() => {
    global.Worker = realWorker;
  });
  beforeEach(() => (webWorkers.length = 0));

  function fakePdfJs(destroyThrows = false) {
    const pdfWorkers: Array<{ port: unknown; destroy: jest.Mock }> = [];
    const pdfjs = {
      GlobalWorkerOptions: { workerSrc: "/_next/static/media/pdf.worker.min.mjs" },
      PDFWorker: class {
        port: unknown;
        destroy = jest.fn(() => {
          if (destroyThrows) throw new Error("already destroyed");
        });
        constructor({ port }: { port: unknown }) {
          this.port = port;
          pdfWorkers.push(this);
        }
      },
      getDocument: jest.fn(() => ({ promise: Promise.resolve({}), destroy: async () => {} })),
    };
    return { pdfjs, pdfWorkers };
  }

  test("each worker is a module Web Worker from the configured source, given to pdf.js as its port", () => {
    const { pdfjs, pdfWorkers } = fakePdfJs();
    const worker = createPdfEngine(pdfjs).createWorker();
    expect(webWorkers).toHaveLength(1);
    expect(webWorkers[0].url).toBe("/_next/static/media/pdf.worker.min.mjs");
    expect(webWorkers[0].options).toEqual({ type: "module" });
    expect(pdfWorkers[0].port).toBe(webWorkers[0]);
    expect(worker.pdfWorker).toBe(pdfWorkers[0]);
  });

  test("documents open with the hardened options on that worker", () => {
    const { pdfjs } = fakePdfJs();
    const engine = createPdfEngine(pdfjs);
    const worker = engine.createWorker();
    const data = new ArrayBuffer(2);
    engine.open(data, worker);
    expect(pdfjs.getDocument).toHaveBeenCalledWith({
      data,
      worker: worker.pdfWorker,
      enableScripting: false,
      isEvalSupported: false,
    });
  });

  test("destroying the worker terminates the Web Worker, even when pdf.js's cleanup throws", () => {
    const { pdfjs } = fakePdfJs(true);
    const worker = createPdfEngine(pdfjs).createWorker();
    expect(() => worker.destroy()).toThrow("already destroyed");
    expect(webWorkers[0].terminate).toHaveBeenCalled();
  });

  test("a session whose stop is never acknowledged terminates the Web Worker", async () => {
    const { pdfjs } = fakePdfJs();
    pdfjs.getDocument.mockReturnValue({ promise: Promise.resolve({}), destroy: never });
    const session = openPdfSession(createPdfEngine(pdfjs), new ArrayBuffer(1));
    await session.document;
    session.stop();
    await jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs);
    expect(webWorkers[0].terminate).toHaveBeenCalled();
  });
});
