import {
  calculateRetryDelay,
  withRetry,
  DEFAULT_RETRY_OPTIONS,
  type RetryOptions,
} from "../../src/utils/retry.ts";

describe("retry", () => {
  describe("DEFAULT_RETRY_OPTIONS", () => {
    it("has sensible defaults", () => {
      expect(DEFAULT_RETRY_OPTIONS.maxRetries).toBe(3);
      expect(DEFAULT_RETRY_OPTIONS.retryDelay).toBe(1000);
      expect(DEFAULT_RETRY_OPTIONS.backoffMultiplier).toBe(2);
    });
  });

  describe("calculateRetryDelay", () => {
    it("calculates delay with exponential backoff", () => {
      const options: RetryOptions = {
        retryDelay: 1000,
        backoffMultiplier: 2,
      };

      const delay0 = calculateRetryDelay(0, options);
      const delay1 = calculateRetryDelay(1, options);
      const delay2 = calculateRetryDelay(2, options);

      expect(delay0).toBe(1000); // 1000 * 2^0 = 1000
      expect(delay1).toBe(2000); // 1000 * 2^1 = 2000
      expect(delay2).toBe(4000); // 1000 * 2^2 = 4000
    });

    it("uses custom backoff multiplier", () => {
      const options: RetryOptions = {
        retryDelay: 1000,
        backoffMultiplier: 3,
      };

      const delay1 = calculateRetryDelay(1, options);
      expect(delay1).toBe(3000); // 1000 * 3^1 = 3000
    });

    it("uses default retryDelay when not specified", () => {
      const options: RetryOptions = {
        backoffMultiplier: 2,
      };

      const delay0 = calculateRetryDelay(0, options);
      expect(delay0).toBe(1000); // Uses default 1000ms
    });

    it("uses default backoffMultiplier when not specified", () => {
      const options: RetryOptions = {
        retryDelay: 500,
      };

      const delay1 = calculateRetryDelay(1, options);
      expect(delay1).toBe(1000); // 500 * 2^1 = 1000
    });
  });

  describe("withRetry", () => {
    it("returns successful result on first attempt", async () => {
      const successfulFn = jest.fn().mockResolvedValue("success");

      const result = await withRetry(() => successfulFn(), {
        maxRetries: 3,
        retryDelay: 100,
      });

      expect(result).toBe("success");
      expect(successfulFn).toHaveBeenCalledTimes(1);
    });

    it("retries on failure up to maxRetries", async () => {
      const error = new Error("Failed");
      (error as any).isRetryable = () => true;

      const failingFn = jest.fn().mockRejectedValue(error);

      await expect(
        withRetry(() => failingFn(), {
          maxRetries: 2,
          retryDelay: 10,
        })
      ).rejects.toThrow("Failed");

      // maxRetries of 2 means: 1 initial + 2 retries = 3 attempts
      expect(failingFn).toHaveBeenCalledTimes(3);
    });

    it("succeeds after retry", async () => {
      const error = new Error("Failed");
      (error as any).isRetryable = () => true;

      const eventuallySucceeds = jest
        .fn()
        .mockRejectedValueOnce(error)
        .mockResolvedValue("success");

      const result = await withRetry(() => eventuallySucceeds(), {
        maxRetries: 2,
        retryDelay: 10,
      });

      expect(result).toBe("success");
      expect(eventuallySucceeds).toHaveBeenCalledTimes(2);
    });

    it("respects retry predicate - non-retryable error", async () => {
      const error = new Error("Not retryable");
      (error as any).isRetryable = () => false;

      const failingFn = jest.fn().mockRejectedValue(error);

      await expect(
        withRetry(() => failingFn(), {
          maxRetries: 3,
          retryDelay: 10,
        })
      ).rejects.toThrow("Not retryable");

      // Should only attempt once since error is not retryable
      expect(failingFn).toHaveBeenCalledTimes(1);
    });

    it("retries when error is retryable", async () => {
      const error = new Error("Retryable");
      (error as any).isRetryable = () => true;

      const eventuallySucceeds = jest
        .fn()
        .mockRejectedValueOnce(error)
        .mockResolvedValue("success");

      const result = await withRetry(() => eventuallySucceeds(), {
        maxRetries: 2,
        retryDelay: 10,
      });

      expect(result).toBe("success");
      expect(eventuallySucceeds).toHaveBeenCalledTimes(2);
    });

    it("throws when max retries exceeded", async () => {
      const error = new Error("Failed");
      (error as any).isRetryable = () => true;

      const failingFn = jest.fn().mockRejectedValue(error);

      await expect(
        withRetry(() => failingFn(), {
          maxRetries: 2,
          retryDelay: 10,
        })
      ).rejects.toThrow("Failed");
    });
  });

  describe("integration", () => {
    it("uses default options when not provided", async () => {
      const successfulFn = jest.fn().mockResolvedValue("success");

      const result = await withRetry(() => successfulFn(), DEFAULT_RETRY_OPTIONS);

      expect(result).toBe("success");
    });

    it("handles non-error rejections", async () => {
      const failingFn = jest.fn().mockRejectedValue("string error");

      await expect(
        withRetry(() => failingFn(), {
          maxRetries: 1,
          retryDelay: 10,
        })
      ).rejects.toEqual("string error");

      // Non-errors don't have isRetryable, so won't retry
      expect(failingFn).toHaveBeenCalledTimes(1);
    });
  });
});
