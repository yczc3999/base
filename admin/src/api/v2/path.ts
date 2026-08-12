/** Encode one opaque/decimal identity as exactly one URL path segment. */
export function pathSegment(value: string): string {
  return encodeURIComponent(value)
}
