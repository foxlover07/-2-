# AI 주거 추천 서비스 — FE

대학생을 위한 주거 매물 탐색·비교 서비스의 프론트엔드 PoC입니다. 현재 데이터는 이화여자대학교 인근 매물 10개(`DB/poc_ewha.csv`)입니다.

## 기술 스택

React · TypeScript · Vite · Tailwind CSS v4 · React Router · lucide-react · 카카오맵 JS SDK

## 실행

```bash
npm install
cp .env.example .env.local   # VITE_KAKAO_MAP_KEY에 카카오 JavaScript 키 입력
npm run dev                  # http://localhost:5173
```

카카오 개발자 콘솔에서 다음을 설정해야 지도가 표시됩니다. 키가 없거나 로드에 실패하면 지도 자리에 안내가 나오고 나머지 기능은 그대로 동작합니다.

- 앱의 카카오맵 사용 설정 ON
- 플랫폼(Web) 도메인에 `http://localhost:5173` 등록 (배포 시 배포 주소도 추가)

## 스크립트

| 명령 | 설명 |
|---|---|
| `npm run dev` | 개발 서버 |
| `npm run build` | 타입 검사 + 프로덕션 빌드 |
| `npm run preview` | 빌드 결과 미리보기 |
| `npm run lint` | oxlint |

## 화면

| 경로 | 화면 |
|---|---|
| `/` | 매물 탐색 — 필터, 카카오맵, 매물 목록·정렬, 비교 담기 |
| `/properties/:id` | 매물 상세 — 비용, 예산 비교, 강의동 도보시간, 네이버 부동산 원본 링크 |
| `/profile` | 생활 프로필 — 대학, 학과·학년·학과 건물, 우선순위(예산·이동 시간·면적 중 1·2순위), 조건(유지비·보증금 상한, 희망 통학 시간·면적), 시간표, 자주 가는 곳 |
| `/compare` | 매물 비교 — 추천 결과(BE POST /recommend, 기준 대비 값·비추천 사유·제외 사유), 2~3개 비교, 최저 비용·최단 이동 강조 |

## 구조

```
src/
  data/properties.ts      CSV 원본 값 (빈 값은 null)
  types/                  Property, 카카오 SDK 타입
  utils/cost.ts           월 고정 주거비 = 월세 + 관리비, 예산 비교, 최저 비용
  utils/walk.ts           가중 평균 도보시간 = Σ(도보시간 × 주간 방문) ÷ Σ(방문), 최단 이동
  services/               propertyService(데이터 접근), 카카오 SDK 로더·지오코딩
  contexts/               대학 선택, 생활 프로필, 비교 목록 (localStorage 저장)
  components/             common(공통 UI), map, explore, detail, profile, compare
  pages/                  라우트별 화면
```

- 데이터 접근은 `services/propertyService.ts`만 거칩니다. FastAPI 연동 시 이 파일의 구현만 교체하면 됩니다.
- 매물 좌표는 주소를 카카오 지오코딩으로 변환하고 브라우저에 캐시합니다. 학교 위치는 장소 검색으로 찾습니다.
- 데이터에 없는 값은 "정보 없음"으로 표시합니다.
