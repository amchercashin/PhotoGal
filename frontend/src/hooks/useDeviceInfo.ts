import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export type DeviceInfo = Awaited<ReturnType<typeof api.getDeviceInfo>>

export function useDeviceInfo() {
  return useQuery<DeviceInfo>({
    queryKey: ['device-info'],
    queryFn: () => api.getDeviceInfo(),
    staleTime: 60_000,
  })
}
