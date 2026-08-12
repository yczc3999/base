import { toValue } from 'vue'
import { fetchArtifactContent, fetchArtifactMetadata } from '@/api/v2/artifacts'
import type { ArtifactByteRange } from '@/api/v2/types'
import { useDetailQuery, type ReactiveValue, type V2QueryOptions } from './page'

export function useArtifactMetadata(
  contentHash: ReactiveValue<string>,
  options?: V2QueryOptions,
) {
  return useDetailQuery(
    'artifacts',
    'metadata',
    [contentHash],
    (signal) => fetchArtifactMetadata(toValue(contentHash), signal),
    options,
  )
}

export function useArtifactContent(
  contentHash: ReactiveValue<string>,
  range: ReactiveValue<ArtifactByteRange>,
  options?: V2QueryOptions,
) {
  return useDetailQuery(
    'artifacts',
    'content',
    [
      contentHash,
      () => toValue(range).start,
      () => toValue(range).end,
    ],
    (signal) => fetchArtifactContent(toValue(contentHash), toValue(range), signal),
    { staleTime: 0, gcTime: 0, ...options },
  )
}
