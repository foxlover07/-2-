import type { AgentProfile, ChatMode, ChatResponse, TimetableImageResponse } from '../types/agent'
import { parseStoredProfile, type Profile } from '../utils/profile'
import { apiFetch } from './api'

// AI 챗봇 (BE POST /agent/chat). 추천 점수·순위는 BE 의 통합안 엔진이 정하고, 챗봇은 조건을 모으고 설명한다.
// 폼과 챗봇이 같은 조건을 쓰도록: 저장된 생활 프로필을 매 턴 보내고, 챗봇이 바꾼 조건은 생활 프로필에 저장한다.

/** 저장된 생활 프로필 + 선택한 대학 → 에이전트 프로필. 저장한 적이 없으면 null (챗봇이 처음부터 묻는다). */
export function toAgentProfile(p: Profile, isSaved: boolean, university: string): AgentProfile | null {
  if (!isSaved) return null
  return {
    university,
    budget_manwon: p.budgetManwon,
    deposit_budget_manwon: p.depositBudgetManwon,
    department: p.department.trim() || null,
    department_buildings: p.departmentBuildings,
    grade: p.grade,
    priorities: p.priorities.slice(0, 2),
    desired_commute_min: p.desiredCommuteMin,
    desired_area_m2: p.desiredAreaM2,
    larger_area: p.largerArea,
    timetable: p.timetable,
    exclusions: p.exclusions,
    frequent_places: p.frequentPlaces,
  }
}

/** 에이전트 프로필 → 생활 프로필. 챗봇이 모르는 항목(폼의 예전 places)은 지금 값을 그대로 둔다. */
export function fromAgentProfile(a: AgentProfile, current: Profile): Profile {
  const merged = {
    ...current,
    department: a.department ?? current.department,
    departmentBuildings: a.department_buildings,
    grade: a.grade ?? current.grade,
    priorities: a.priorities.length > 0 ? a.priorities : current.priorities,
    budgetManwon: a.budget_manwon,
    depositBudgetManwon: a.deposit_budget_manwon,
    desiredCommuteMin: a.desired_commute_min ?? current.desiredCommuteMin,
    desiredAreaM2: a.desired_area_m2 ?? current.desiredAreaM2,
    largerArea: a.larger_area ?? current.largerArea,
    timetable: a.timetable,
    exclusions: a.exclusions,
    frequentPlaces: a.frequent_places ?? current.frequentPlaces,
    savedAt: new Date().toISOString(),
  }
  // 저장 형식 검증을 한 번 더 거친다 (범위를 벗어난 값은 기본값)
  return parseStoredProfile(merged) ?? current
}

export function chat(
  sessionId: string,
  message: string,
  profile: AgentProfile | null,
  mode?: ChatMode,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  return apiFetch<ChatResponse>('/agent/chat', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, message, profile, mode }),
    signal,
  })
}

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'] as const
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.onerror = () => reject(new Error('파일을 읽지 못했습니다.'))
    reader.readAsDataURL(file)
  })
}

/** 시간표 캡처 → 수업 칸 미리보기. 형식·크기는 여기서 먼저 막는다. */
export async function readTimetableImage(
  file: File,
  university: string,
  signal?: AbortSignal,
): Promise<TimetableImageResponse> {
  if (!(IMAGE_TYPES as readonly string[]).includes(file.type)) throw new Error('PNG, JPG, WEBP 이미지만 올릴 수 있습니다.')
  if (file.size > MAX_IMAGE_BYTES) throw new Error('5MB 이하 이미지만 올릴 수 있습니다.')
  const image_base64 = await toBase64(file)
  return apiFetch<TimetableImageResponse>('/agent/timetable-image', {
    method: 'POST',
    body: JSON.stringify({ university, image_base64, mime_type: file.type }),
    signal,
  })
}
