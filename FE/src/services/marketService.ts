import type { Amenities, BuildingInfo, ContractCheck, MarketCompare, RelatedPolicies } from '../types/market'
import { API_URL, apiFetch } from './api'

// 서버(VITE_API_URL)가 없으면 null — 실거래·편의시설은 로컬 데이터에 없다.

export async function getMarketCompare(id: string): Promise<MarketCompare | null> {
  if (API_URL === null) return null
  return apiFetch<MarketCompare>(`/market/compare/${encodeURIComponent(id)}`)
}

export async function getAmenities(id: string): Promise<Amenities | null> {
  if (API_URL === null) return null
  return apiFetch<Amenities>(`/market/amenities/${encodeURIComponent(id)}`)
}

export async function getBuilding(id: string): Promise<BuildingInfo | null> {
  if (API_URL === null) return null
  return apiFetch<BuildingInfo>(`/market/building/${encodeURIComponent(id)}`)
}

export async function getContractCheck(id: string): Promise<ContractCheck | null> {
  if (API_URL === null) return null
  return apiFetch<ContractCheck>(`/market/contract-check/${encodeURIComponent(id)}`)
}

export async function getRelatedPolicies(id: string): Promise<RelatedPolicies | null> {
  if (API_URL === null) return null
  return apiFetch<RelatedPolicies>(`/market/related-policies/${encodeURIComponent(id)}`)
}
