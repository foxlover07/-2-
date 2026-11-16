import { properties } from '../data/properties'
import type { Property } from '../types/property'
import { API_URL, ApiError, apiFetch } from './api'

// VITE_API_URL 이 있으면 BE(GET /properties)에서 받고, 없으면 로컬 데이터(src/data/properties.ts)를 쓴다.

export async function getProperties(): Promise<Property[]> {
  if (API_URL === null) return properties
  return apiFetch<Property[]>('/properties')
}

export async function getPropertyById(id: string): Promise<Property | null> {
  if (API_URL === null) return properties.find((p) => p.property_id === id) ?? null
  try {
    return await apiFetch<Property>(`/properties/${encodeURIComponent(id)}`)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null
    throw e
  }
}
