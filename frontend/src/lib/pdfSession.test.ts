import {
  PDF_DEADLINES,
  PdfDeadlineError,
  openPdfSession,
  renderWithDeadline,
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

test("a page drawing that does not finish in time is cancelled and fails", async () => {
  const render = { promise: never<void>(), cancel: jest.fn() };
  const outcome = renderWithDeadline(render).catch((e) => e);
  await jest.advanceTimersByTimeAsync(PDF_DEADLINES.renderMs);
  expect(await outcome).toBeInstanceOf(PdfDeadlineError);
  expect(render.cancel).toHaveBeenCalled();
});

test("a page drawing that finishes in time succeeds", async () => {
  const render = { promise: Promise.resolve(), cancel: jest.fn() };
  await expect(renderWithDeadline(render)).resolves.toBeUndefined();
  expect(render.cancel).not.toHaveBeenCalled();
});
