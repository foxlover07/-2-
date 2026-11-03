import { properties } from '../data/properties'
import type { Property } from '../types/property'

// 지금은 로컬 데이터를 반환한다. FastAPI 연동 시 이 파일의 구현만 fetch로 바꾼다.

export async function getProperties(): Promise<Property[]> {
  return properties
}

export async function getPropertyById(id: string): Promise<Property | null> {
  return properties.find((p) => p.property_id === id) ?? null
}
