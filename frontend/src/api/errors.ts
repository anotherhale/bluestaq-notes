/**
 * Centralized API error model.
 *
 * Every failure from the backend is converted into an {@link ApiError} so that
 * components and the workspace hook never have to know about HTTP status codes
 * or the wire error envelope. The backend's envelope is:
 *
 *   { "error": { "code": ..., "message": ..., "details": [...] }, "request_id": ... }
 *
 * A network/parse failure is represented as an ApiError with `status === 0` so
 * callers can still render a useful message.
 */

/** A single field-validation detail from the backend error envelope. */
export interface ValidationDetail {
  loc: string[];
  msg: string;
  type: string;
}

export class ApiError extends Error {
  /** HTTP status, or 0 for a network/transport failure. */
  readonly status: number;
  /** Backend error code (e.g. "conflict", "not_found"), if provided. */
  readonly code: string | null;
  /** Correlation id echoed by the backend, useful for support. */
  readonly requestId: string | null;
  /** Field-level validation details, when the backend supplies them. */
  readonly details: ValidationDetail[];

  constructor(init: {
    message: string;
    status: number;
    code?: string | null;
    requestId?: string | null;
    details?: ValidationDetail[];
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code ?? null;
    this.requestId = init.requestId ?? null;
    this.details = init.details ?? [];
  }

  /** True for the optimistic-concurrency conflict (HTTP 409). */
  get isConflict(): boolean {
    return this.status === 409;
  }

  /** True when the caller is not authenticated (HTTP 401). */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** True when the resource is missing or hidden (HTTP 404). */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** True when the caller is authenticated but not permitted (HTTP 403). */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  /** True when the request body failed validation (HTTP 422). */
  get isValidation(): boolean {
    return this.status === 422;
  }

  /** A short, human-readable field name for a validation detail, if any. */
  get firstFieldError(): ValidationDetail | null {
    return this.details.length > 0 ? (this.details[0] ?? null) : null;
  }
}

/** Narrow an unknown thrown value to an ApiError. */
export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}
