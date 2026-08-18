export interface RetryOptions {
  maxRetries?: number;
  retryDelay?: number;
  backoffMultiplier?: number;
}

export const DEFAULT_RETRY_OPTIONS: Required<RetryOptions> = {
  maxRetries: 3,
  retryDelay: 1000,
  backoffMultiplier: 2,
};

export function calculateRetryDelay(attempt: number, options: RetryOptions = {}): number {
  const baseDelay = options.retryDelay ?? DEFAULT_RETRY_OPTIONS.retryDelay;
  const multiplier = options.backoffMultiplier ?? DEFAULT_RETRY_OPTIONS.backoffMultiplier;
  return baseDelay * multiplier ** attempt;
}

export async function withRetry<T>(fn: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const maxRetries = options.maxRetries ?? DEFAULT_RETRY_OPTIONS.maxRetries;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    try {
      return await fn();
    } catch (error) {
      const retryable =
        error instanceof Error &&
        "isRetryable" in error &&
        typeof error.isRetryable === "function" &&
        error.isRetryable();
      if (!retryable || attempt >= maxRetries) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, calculateRetryDelay(attempt, options)));
    }
  }

  throw new Error("Retry loop terminated unexpectedly");
}
