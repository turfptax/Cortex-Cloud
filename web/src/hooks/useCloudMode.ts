import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

/** Cloud vs desktop deployment.
 *
 * The Hub SPA now runs in two places: the local desktop app (talking to
 * a local FastAPI backend) and the cloud gateway at cortex.turfptax.com.
 * /api/health reports `mode: "cloud"` only from the gateway facade; the
 * desktop backend omits it. Desktop-only surfaces (Local LM, Video,
 * Lemon egress, the desktop Settings cards) read this to hide themselves
 * in the cloud, where those features have no home. Mode is fixed for the
 * lifetime of a deployment, so this is cached indefinitely. */
export function useCloudMode(): { cloud: boolean; loaded: boolean } {
  const { data, isSuccess } = useQuery({
    queryKey: ['deploy-mode'],
    queryFn: () => apiFetch<{ mode?: string }>('/health'),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })
  return { cloud: data?.mode === 'cloud', loaded: isSuccess }
}
