import { requestRawV2, requestV2 } from '../request'
import { pathSegment } from './path'
import type { ArtifactByteRange, ArtifactContent, ArtifactMetadata } from './types'

export function fetchArtifactMetadata(
  contentHash: string,
  signal?: AbortSignal,
): Promise<ArtifactMetadata> {
  return requestV2({
    url: `/admin/v2/artifacts/${pathSegment(contentHash)}/metadata`,
    signal,
  })
}

export async function fetchArtifactContent(
  contentHash: string,
  range: ArtifactByteRange,
  signal?: AbortSignal,
): Promise<ArtifactContent> {
  const response = await requestRawV2<ArrayBuffer>({
    url: `/admin/v2/artifacts/${pathSegment(contentHash)}/content`,
    signal,
    headers: { Range: `bytes=${range.start}-${range.end}` },
    responseType: 'arraybuffer',
  })
  return {
    data: response.data,
    content_type: String(response.headers['content-type'] ?? 'application/octet-stream'),
    content_range: String(response.headers['content-range'] ?? ''),
    accept_ranges: String(response.headers['accept-ranges'] ?? ''),
    etag: String(response.headers.etag ?? ''),
  }
}
