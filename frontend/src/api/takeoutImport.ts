export interface MutableFlag {
  current: boolean;
}

export async function runExclusiveOperation(
  flag: MutableFlag,
  setLoading: (loading: boolean) => void,
  operation: () => Promise<void>,
): Promise<boolean> {
  if (flag.current) return false;
  flag.current = true;
  setLoading(true);
  try {
    await operation();
    return true;
  } finally {
    flag.current = false;
    setLoading(false);
  }
}

interface PollableJob {
  status: string;
  message: string;
  errorCode: string | null;
}

interface PollOptions<T extends PollableJob> {
  signal: AbortSignal;
  timeoutMs?: number;
  intervalMs?: number;
  networkFailureLimit?: number;
  onStatus?: (status: T) => void;
  isComplete?: (status: T) => boolean;
}

interface ChainedJob extends PollableJob {
  continueQueued?: boolean | null;
}

interface ChainedPollOptions<T extends ChainedJob> extends Omit<PollOptions<T>, "isComplete"> {
  batchDelayMs?: number;
  onBatchComplete?: (status: T) => void | Promise<void>;
}

export async function pollTakeoutImport<T extends PollableJob>(
  getStatus: (signal: AbortSignal) => Promise<T>,
  {
    signal,
    timeoutMs = 10 * 60 * 1000,
    intervalMs = 1000,
    networkFailureLimit = 8,
    onStatus,
    isComplete = (status) => status.status === "complete",
  }: PollOptions<T>,
): Promise<T> {
  const startedAt = Date.now();
  let networkFailures = 0;
  while (Date.now() - startedAt < timeoutMs) {
    if (signal.aborted) throw new DOMException("Takeout import polling was cancelled.", "AbortError");
    try {
      const status = await getStatus(signal);
      networkFailures = 0;
      onStatus?.(status);
      if (isComplete(status)) return status;
      if (status.status === "failed") {
        throw new Error(`${status.message}${status.errorCode ? ` (${status.errorCode})` : ""}`);
      }
    } catch (error) {
      if (signal.aborted || (error instanceof DOMException && error.name === "AbortError")) throw error;
      if (error instanceof Error && /\([a-z_]+\)$/.test(error.message)) throw error;
      networkFailures += 1;
      if (networkFailures >= networkFailureLimit) {
        throw new Error("The server stayed unavailable while processing metadata. Your saved listening data is safe; refresh and resume in a moment.");
      }
    }
    await abortableDelay(intervalMs, signal);
  }
  throw new Error("Local music processing timed out. Your previous profile is still available; retry after checking the backend.");
}

/**
 * Run one hosted-safe batch at a time, yielding between batches so normal
 * dashboard requests are never starved by a continuous enrichment loop.
 * The browser may stop at any point: each completed batch is already durable.
 */
export async function pollChainedJob<T extends ChainedJob>(
  startBatch: () => Promise<T>,
  getStatus: (signal: AbortSignal) => Promise<T>,
  {
    signal,
    timeoutMs = 30 * 60 * 1000,
    intervalMs = 1500,
    networkFailureLimit = 12,
    batchDelayMs = 4000,
    onStatus,
    onBatchComplete,
  }: ChainedPollOptions<T>,
): Promise<T> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (signal.aborted) throw new DOMException("Metadata enrichment polling was cancelled.", "AbortError");
    let queued: T | null = null;
    let startFailures = 0;
    while (!queued && Date.now() - startedAt < timeoutMs) {
      try {
        queued = await startBatch();
      } catch (error) {
        if (signal.aborted || (error instanceof DOMException && error.name === "AbortError")) throw error;
        if (error instanceof Error && /\([a-z_]+\)$/.test(error.message)) throw error;
        startFailures += 1;
        if (startFailures >= networkFailureLimit) {
          throw new Error("The server stayed unavailable before the next metadata batch could start. Saved progress will resume on the next visit.");
        }
        await abortableDelay(intervalMs, signal);
      }
    }
    if (!queued) break;
    let batch: T;
    try {
      batch = queued.status === "complete"
        ? queued
        : await pollTakeoutImport(getStatus, {
            signal,
            timeoutMs: Math.max(1, timeoutMs - (Date.now() - startedAt)),
            intervalMs,
            networkFailureLimit,
            onStatus,
          });
    } catch (error) {
      if (error instanceof Error && /\(backend_restarted\)$/.test(error.message)) {
        await abortableDelay(batchDelayMs, signal);
        continue;
      }
      throw error;
    }
    if (batch.status === "failed") {
      if (batch.errorCode === "backend_restarted") {
        await abortableDelay(batchDelayMs, signal);
        continue;
      }
      throw new Error(`${batch.message}${batch.errorCode ? ` (${batch.errorCode})` : ""}`);
    }
    await onBatchComplete?.(batch);
    if (!batch.continueQueued) return batch;
    await abortableDelay(batchDelayMs, signal);
  }
  throw new Error("Metadata enrichment paused after its safe processing window. Saved progress will resume on the next visit.");
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        globalThis.clearTimeout(timer);
        reject(new DOMException("Takeout import polling was cancelled.", "AbortError"));
      },
      { once: true },
    );
  });
}
