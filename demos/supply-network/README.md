# DAS Lab 공급망 입지·네트워크·배송 시뮬레이션 기초

이 파일은 프로필 계약과 합성 기본 모델이다. 완성된 산업 사례나 고객 납품물이 아니다.
PM이 조사한 실제 문제·목적·출처·데이터 상태·완료 기준을 반영해 소스와 이 문서를 수정해야 한다.
문제 선정, 산업 자료의 근거, 비교 대안의 타당성은 이 기본 모델만으로 검증되지 않는다.

## 고정 실행 계약
브라우저 window.DASNetwork.run(params)는 JSON으로 직렬화할 수 있는 결과를 반환한다.
params: seed=42, days=7(1~30), demandMultiplier=1(0~3), capacityMultiplier=1(0~3),
maxHubs=2(1~시설 수), disruptionHub='' 또는 거점 id, disruptionDays=0(0~days).
선택 data: facilities[{id,name,lon,lat,capacityPerDay,fixedCostPerDay}],
customers[{id,name,lon,lat,demandPerDay}], provenance 객체. 시설2~5개·수요지1~8개.
EPSG:4326 위경도. 기본 좌표는 도시권 근처의 예시 위치, 수요·용량·비용은 합성이다.
도로망·지형·고객 실제 자료가 아니다. 지도에 위경도 좌표망과 실제 모델의 연결을 표시한다.

run 반환: profile, parameters, data, baseline, optimized, candidates.
각 대안은 selectedHubs, planning, simulation으로 구성한다.
planning.allocations: hubId,customerId,unitsPerDay,distanceKm,unitCost.
planning.unserved: customerId,unitsPerDay.
planning: fixedCostPerDay,transportCostPerDay,unservedPenaltyPerDay,totalCostPerDay,
servedUnitsPerDay,totalDemandPerDay. 수요·용량은 Math.round(기준값*배율).
거리 Haversine(지구반경6371km), 단위 운송비=거리*0.12, 미충족 비용=200/개/일.
baseline은 데이터 첫·마지막 시설을 maxHubs 범위에서 선택한다.
optimized는 공집합을 포함한 maxHubs 이하 모든 시설 부분집합을 열거하고,
각 부분집합의 정수 용량제약 최소비용 배분을 잔여 그래프 최단경로로 계산한다.
정의한 작은 단일기간 문제에서의 최적화이며 도로·재고·다기간 입지 최적화가 아니다.

simulation.orders: id='d{day}-{customerId}-{ordinal}',day(0부터),customerId,hubId 또는 null,
quantity=1,distanceKm,createdAt,dueAt,exogenousDelayHours,dispatchedAt/deliveredAt 또는 null,
unservedReason. 매일 고정 수요이며 계획 배분을 hubId 순서로 적용한다.
createdAt=day*24+8+ordinal*0.01, dueAt=createdAt+8, dispatchedAt=createdAt+1,
deliveredAt=dispatchedAt+distanceKm/50+exogenousDelayHours.
외생 지연은 시드·날짜·고객·주문번호 기반 [0,3)시간으로 대안에 공통이다.
장애 거점은 처음 disruptionDays일 출고 불가. 긴급 재배정·재고이월은 없다.
daily: day,demand,delivered,onTime,unserved. kpis: demand,delivered,unserved,onTime,
serviceRate,onTimeRate,meanLeadHours,fixedCost,transportCost,penaltyCost,totalCost.
서비스·정시율 분모는 전체 수요. 수요0은 비율0, 평균리드0으로 표시하며 달성 실적으로 해석하지 않는다. 정시=deliveredAt<=dueAt.
고정비는 선택거점 일비용*days, 운송비는 실제 출고 물량, 벌점은 미충족 물량만 계상한다.

## 화면 계약
model-form/run-model/model-results/model-checks/model-scene를 유지한다.
network-map SVG는 위경도 기반이며 data-selected-hubs는 선택id의 쉼표목록이다.
거점/고객은 data-hub-id/data-customer-id 및 data-lon/data-lat,
운송선은 data-route-hub/data-route-customer/data-units를 가진다.
모든 거점·수요 marker는 circle(cx,cy)이다. 좌표 범위는 전체 점의 경도 최소/최대에
-.35/+.35, 위도 최소/최대에 -.3/+.3을 더한 west/east/south/north이다.
투영식 x=55+(lon-west)/(east-west)*790, y=490-(lat-south)/(north-south)*430.
viewBox=0 0 900 540이며 회전·중첩 변환 없이 경로와 점에 같은 식을 쓴다.
top-kpis의 data-metric와 data-value는 비용·비율·주문 수의 원시 수치를 표시 문구와 함께 보존한다.
top-kpis와 model-results의 data-scenario/data-total-cost/data-service-rate/data-on-time-rate/
data-demand/data-delivered는 현재 scenario-select(baseline/optimized)와 일치해야 한다.
수요·용량·거점수·장애·시드·기간 입력을 바꾼 뒤 run-model로 실제 결과를 다시 계산한다.
직원 코드는 이 공개 계약을 보존한다. 모델 계산과 화면 표현 외 주장을 검사 통과로 대신하지 않는다.

## 작업과 검증
6개 파일만 수정. 외부 패키지·네트워크·설치·원격 지도·고객 접촉·공개 배포 금지.
공개 조사 결과는 PM이 제공한 근거를 출처·날짜와 함께 반영한다. 자료 부족은 명시한다.
생성한 model.test.cjs는 허용된 직원 샌드박스 안에서만 실행한다.
서버는 파일·문법 검사 후 격리 브라우저에서 별도의 모델·화면 검사를 수행한다.
기본 seed의 검사는 구현 연결의 검증이며 고객 효과·제품 완성도 검증이 아니다.


## 재작업 결과 · 2026-09-30 · DAS-RD

목표는 지역 배송망 기획 책임자가 시설 선택·배분·배송의 비용과 서비스 상충을 비교하는 내부 데모다. 시작 기준은 지역 화면은 있으나 API 기본 불일치·오류 잔류·README 652,628바이트 초과였다. 목표 수준은 지역 사례 연결, 같은 조건의 계획 목적값 비악화, 수요 보존 잔차 0·용량 초과 0·공통 지연 일치, 원시 재계산 일치다. 고객 절감 목표는 설정하지 않는다. 직원 완료 조건은 구현·자체 수치 검사와 서버 인계이며 실제 브라우저 및 독립 모델 검수는 후속 서버 책임이다.

### 복구 대조와 변경 경계
최신 attempt f0ebf61cdf0446da9dd27129dac2629d; 제공 묶음 식별자 26155c737c2ed733f931063819c666ac8a9cf516b548f772b5a131d785a4ed8c. 묶음 산식 미제공으로 묶음 자체 재계산은 하지 않았다. 시작 Get-FileHash SHA256은 제공 파일별 해시와 6/6 일치:
- app.js: 36c48833ec92e4029c78ec79ea1bfabe7edcbc803867465837415dea21aed98c
- index.html: 34e0067c3c609489cacb2142258f8e0450320ae913f1ced3fea81e78b1b2712f
- model.js: 92bd63c89bbc6e0d2b2ec6efef2d15d0f56ec6fba858c5b1fc1ccc74da268d40
- model.test.cjs: 5f9b69cb4b8bfaada68162b85ffd8ea57a59d57c93a2bc150be8d16d19d70b0c
- README.md: c671e8059e557ef399b5124012ff55e446b34adf2274c9aa8702f42946624cdc
- style.css: fa9c752567940a81ec28174350007f9a7013db873f1540cc286e4b4a8e1b2444
source=서버 repair_artifacts, captured_at=2026-09-30T13:58:07.242634+00:00, original_byte_identity_verified=false. 실패 종료 당시 바이트 동일성·작동 증거로 격상하지 않는다.
변경: model.js의 지역 기본값·모든 fixture kind, app.js 공통 오류 경로와 이전 데이터 삭제, index.html 초기 장애 선택 정리·상태 알림, style.css 오류 문구 줄바꿈·숨김 명시, model.test.cjs 허용 모듈·명시 S2·회귀검사·요약 생성기, README 전체 덤프 제거. 기존 열거·잔여 흐름 알고리즘 유지.
작업 공간에는 허용 6파일만 있다. 외부 접근 금지로 demos/supply-chain-policy/ 및 docs/reports/2026-09-30-policy-demo/ 원본 불변은 확인 불가. 대표/PM에게 두 대상의 기준 버전·전후 해시 보유 여부와 대조 기록을 요청한다. 보존 확인에 필요하며 이번 6파일 재작업은 계속 가능하다.

### 합성 지역 사례와 방법 선택
기본 화면과 data 미지정 API 모두 KR-regional이다. S2/R2 산술 검사는 명시적 data를 전달한다. 모든 fixture는 provenance.kind=synthetic이다. fixtures().regional을 data로 전달하면 같은 지역 결과를 재현한다. 성남·대전·대구·부산 후보 4개와 서울·인천·대전·광주·대구·부산 수요 6개는 한국 도시권의 지리적 분포를 설명하기 위한 예시 좌표다. 실제 시설 주소나 측량·행정경계 자료가 아니다. 정확한 전체 배열은 model.js와 fixtures()에 있다. 수요 합 180개/일, 용량 90/110/100/80개/일, 고정비 160/130/120/150 합성 비용단위/일이다. 기준 H1/H4의 용량 부족과 중간 후보의 공급 범위를 비교하도록 설정했다. 단위: 거리 km, 시간 h, 수량 item, 비용 synthetic_cost_unit. 작성일 2026-09-30, source=DAS Lab bounded scaffold, verification=synthetic_not_field_validated.

최대 5시설에서는 최대 32부분집합을 모두 열거하고 Bellman–Ford 잔여 경로로 정수 흐름을 구한다. 탐욕 최근접은 R2 재배치를 놓치므로 제외, 새 MIP 솔버는 설치 없이 검증 가능한 규모라 보류했다. 처리 대기 DES·ABM·SD는 계약의 자료·상태를 넘어 보류했다. 단일기간 선형 목적에서만 최적성을 주장하며 부동 오차 허용을 둔다. 계획은 장애를 미리 반영하지 않는다.

구현 선택: 동일 비용은 먼저 열거된 마스크를 유지(비교 여유 1e-7), 흐름 완화 여유 1e-9. 기준 maxHubs=1은 첫 시설. 주문 ordinal=0부터, 지연은 seed XOR 2166136261, day:customerId:ordinal의 문자 코드와 16777619 곱, 2246822507 혼합 후 uint32/2^32*3이다. 대안과 장애는 해시 키에 없다. 입력 ID는 ASCII 영숫자/밑줄/하이픈 1~30자, 이름 80자 이하, 수량 정수 0~1000, 시설비 0~100000, seed 0~2147483647, 주문 20000개 상한의 시연 안전 제한을 둔다. 범위 밖은 오류로 반환하며 화면은 이전 결과를 숨긴다. 고정비 배율 UI는 data의 fixedCostPerDay만 변환하며 고정 API에 새 비용 상수를 만들지 않는다. 납기 민감도 4/6/8h는 동일 orders의 사후 판정이며 API 납기 8h는 유지한다.

### 기존 조사 출처와 기억 보존
이번 외부 조회는 0회다. 아래는 제공된 조사 보고의 재사용이며 이번 원문 열람 또는 독립 확인이 아니다.
- USPS OIG Atlanta RPDC: https://www.oversight.gov/reports/audit/network-changes-progress-improvements-atlanta-ga-regional-processing-and-distribution , 발행 2025-07-08, 제공 확인일 2026-09-30. 시설 통합 후 물량·처리·비용·서비스 재평가가 필요하다는 문제 근거.
- USPS OIG DFA Volume 3: https://www.oversight.gov/reports/audit/oigs-oversight-uspss-delivering-america-plan-volume-3 , 발행 2026-01-20. 제공 확인일 2026-09-30, 최신 조사에서 직접 페이지 timeout·PDF 오류, 이전 설명·PDF 텍스트 조회 기록은 ai_unverified 그대로 유지. 통합·투자가 서비스 동시 개선을 보장하지 않는다는 근거.
- https://developers.google.com/optimization/flow/mincostflow , 갱신 2026-03-18 UTC, 제공 확인일 2026-09-30. 최소비용 흐름의 용량·보존 근거. 실제 패키지 사용 아님.
- 제조 부품망은 BOM·생산계획 자료, 재난망은 형평성·복구 우선순위가 필요하여 기존 선정대로 보류. Toyota 생산방식, FEMA 물류 도구, SCIP 시설입지, SimPy, RFC7946(2016-08), OSM 저작권의 이전 열람 기록을 새 확인으로 격상하지 않는다. FEMA PDF 실패 및 검색 표시 April 2019 역시 원문 날짜 확인으로 바꾸지 않는다.

기억 source/date/verification:
- mission:00c559726d204cc3af7ff8b3951cb2a7 / 2026-09-30T13:22:26.210921+00:00 / ai_unverified
- mission:a348128d337b4c54a53f176627ca8f11 / 2026-09-30T13:13:54.224713+00:00 / ai_unverified
- mission:efbb8875e61849509a6a007c50a16c30 / 2026-09-30T13:04:26.583548+00:00 / ai_unverified
- owner / 2026-09-30T10:53:30.964250+00:00 / owner_statement (공통 수행 과정)
- owner / 2026-09-30T10:45:51.291669+00:00 / owner_statement (공급망 기대 수준)
- mission:2247519d85734764b737594a5c734a02 / 2026-09-30T06:55:20.833319+00:00 / ai_unverified (링크 수정, 이번 근거에서 제외)
- knowledge/daslab-team.md / 2026-09-30T06:42:30.606286+00:00 / source_record (역할)

### 오류·검증·재현
관측된 원인: README 기록 생성기가 전체 원시 결과를 덤프; node:crypto 허용 밖; 합성 kind 누락; API 기본 S2; setCase가 try 밖. 수정 후 node model.test.cjs --record로 요약만 생성하고 최종 크기를 검사한다. 원시 데이터는 DASNetwork.run({data:DASNetwork.fixtures().S2}) 또는 regional/R2 및 아래 변경 조건으로 재현·재계산한다. --raw 덤프 옵션은 제거했다.
정상 결과 → 고정비11 → 사례변경 → 오류 사유·결과 숨김·지도/KPI 자식 및 속성 삭제 → 고정비1 → 사례변경 → 재계산을 DOM 모형으로 검사한다. 실제 브라우저 레이아웃·접근성·콘솔 검증은 아니다. 수치/시간 오차 max(1e-9,상대1e-9), 별도 구면코사인 거리 검사는 1e-5km.

### 서버 독립 검사용 조작 인계
1. index.html 로드: 지역 사례, seed42/7일/수요1/용량1/거점2/고정비1/장애없음. 원시 입력은 DASNetwork.run({data:DASNetwork.fixtures().regional}). 4후보·6수요 circle, 선택 H1,H3, 계획선과 run 반환 planning.allocations 일치. 전체 점 범위의 계약 투영식을 사용한다.
2. baseline으로 전환: H1,H4, 기간비용 24801.966399753248, 배송1190/1260. optimized로 전환: H1,H3, 비용11790.69560457667, 배송1260/1260. 두 영역 data 속성·data-metric·선 endpoints를 같은 반환 객체와 대조한다.
3. 거점·고객 클릭 또는 Enter: 배분/용량/관련 실제 주문 상세 확인. H3 장애2일 후 다시 계산: 계획선은 유지, 실제 출고와 장애 손실은 용량표에서 감소/증가. 빨간 점은 장애 지정이며 미선택 후보 장애는 영향이 없을 수 있다.
4. 수요1.2, 용량0.6, 거점1/4, 고정비0/10, seed2026, 기간1/30을 각각 기본값에서 바꾸고 다시 계산. 아래 동일 명칭 실행과 비교. 장애일수는 기간 이하로 설정한다.
5. S2 선택: F1/F3 기준과 F2 개선, 기본 운영비용 각각 28+56k와 14+112k (k=0.12*6371*pi/180*0.01). F2 7일 장애: 개선 비용11214, 배송0, 계획8/일 유지. 수요0: 선택공집합, 비용0, 평가대상없음. R2 선택: A→D2=1, B→D1=1, 1일 비용4k.
6. 정상 결과 후 고정비11 입력→사례변경: 오류 사유, 이전 지도·KPI·결과 숨김; 고정비1→사례변경: 새 사례 재계산. 잘못된 기간0은 HTML validation 또는 모델 오류로 거절; 유효값 복원 후 재계산. 실제 브라우저·독립 모델 검사는 직원 미수행이며 서버 기록을 PM이 검수해야 한다.

### 평가와 한계
정상 지역안에서 개선안 비용·충족률이 좋아져도 평균 리드는 길어진다. 미배송을 평균에서 제외하므로 표본 차이에도 주의한다. 선택하지 않은 거점의 장애는 영향0, 선택 거점 장애는 계획비용 우위를 운영비용 우위로 보장하지 않는다. 고정비 변화가 항상 선택을 바꾸지는 않는다. 비용은 실제 원화가 아니며 고객 절감 실적이 아니다. 외생 지연의 분포는 보정되지 않았고 수요·장애는 결정론적 스트레스다. 도로망·차량·재고·혼잡·다기간 입지·신뢰구간·실고객 효과는 검증하지 않았다. 이번 자체 검사는 독립 검증이 아니다. 대표 결정 요청은 없으며 원본 보존 대조는 PM/서버 기록이 필요하다.

<!-- EXECUTION_RECORD -->
## 실제 Node 자체 검사 요약

실행: 2026-09-30T14:02:18.578Z · v24.19.0 · 독립 검증/실제 브라우저 아님.

명령: node model.test.cjs --record. 아래 모든 실행은 원시 orders·planning·daily에서 비용/수요/용량/납기를 재계산하여 PASS. 수량 정확 일치; 비용·시간 max(1e-9, 상대1e-9), 별도 거리식 1e-5km.

- PASS: 합성 provenance 및 최초 지역 UI와 생략 API 일치
- PASS: S2 전체 7집합과 작은 해
- PASS: R2 역방향 재배치 최종 해
- PASS: 수요 0과 용량 0 공집합
- PASS: 용량 부족과 중복 벌점
- PASS: 전기간 및 2일 장애
- PASS: 반올림 .49/.5/1.5
- PASS: 거점 수 및 용량 단조성
- PASS: 수요·고정비·seed 민감도
- PASS: 재현성·장애 간 공통 지연·JSON
- PASS: 정확 납기 경계 <=
- PASS: 별도 장거리 합성 입력의 실제 납기 지연
- PASS: 범위·기간·최대 후보 32개
- PASS: 화면 핸들러·독립 KPI·투영·선택·재계산 (DOM 모형)
- PASS: R2 첫 증대 이후 실제 역간선 사용
- PASS: 지역 배송망 스트레스·용량·납기 민감도

### 산술 기대값 / 관측값 / 판정
| 검사 | 기대 | 관측 | 판정 |
|---|---:|---:|---|
| S2 기준 일비용 | 5.067471 | 5.067471 | PASS |
| S2 개선 일비용 | 4.134943 | 4.134943 | PASS |
| R2 재배치 비용 | 0.533736 | 0.533736 | PASS |
| S2 F2 전기간 장애 비용 | 11214 | 11214 | PASS |

### 실행별 핵심 비교
기본 조건 seed42/7일/배율1/maxHubs2/장애없음. fixture와 변경 조건으로 재현. 계획비용은 일, 운영비용은 기간, 비율은 0~1, 리드는 h. 모든 행의 기대: 제약 잔차0·용량초과0·개선 계획비용 비악화·원시 재계산 일치; 관측: PASS.
| 사례·변경 조건 | 대안·선택 | 계획비용 | 운영비용 | 배송률 | 정시율 | 리드 | 용량: 거점=계획/한도, 기간출고, 장애손실 |
|---|---|---:|---:|---:|---:|---:|---|
| S2 정상; S2-contract-2026-09-30; {"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| S2 정상; S2-contract-2026-09-30; {"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| R2; R2-contract-2026-09-30; {"days":1,"fixedCosts":[0,0]} | baseline A,B | 0.533736 | 0.533736 | 1 | 1 | 2.91854 | A=1/1,1,0; B=1/1,1,0 |
| R2; R2-contract-2026-09-30; {"days":1,"fixedCosts":[0,0]} | optimized A,B | 0.533736 | 0.533736 | 1 | 1 | 2.91854 | A=1/1,1,0; B=1/1,1,0 |
| 수요0; S2-contract-2026-09-30; {"demandMultiplier":0,"days":1,"fixedCosts":[2,2,2]} | baseline F1,F3 | 4 | 4 | 0 | 0 | 0 | F1=0/8,0,0; F2=0/8,0,0; F3=0/8,0,0 |
| 수요0; S2-contract-2026-09-30; {"demandMultiplier":0,"days":1,"fixedCosts":[2,2,2]} | optimized  | 0 | 0 | 0 | 0 | 0 | F1=0/8,0,0; F2=0/8,0,0; F3=0/8,0,0 |
| 용량0; S2-contract-2026-09-30; {"capacityMultiplier":0,"days":1,"fixedCosts":[2,2,2]} | baseline F1,F3 | 1604 | 1604 | 0 | 0 | 0 | F1=0/0,0,0; F2=0/0,0,0; F3=0/0,0,0 |
| 용량0; S2-contract-2026-09-30; {"capacityMultiplier":0,"days":1,"fixedCosts":[2,2,2]} | optimized  | 1600 | 1600 | 0 | 0 | 0 | F1=0/0,0,0; F2=0/0,0,0; F3=0/0,0,0 |
| R2 부족; R2-contract-2026-09-30; {"days":1,"demandMultiplier":2,"fixedCosts":[0,0]} | baseline A,B | 400.400302 | 400.400302 | 0.5 | 0.5 | 2.461826 | A=1/1,1,0; B=1/1,1,0 |
| R2 부족; R2-contract-2026-09-30; {"days":1,"demandMultiplier":2,"fixedCosts":[0,0]} | optimized A,B | 400.400302 | 400.400302 | 0.5 | 0.5 | 2.461826 | A=1/1,1,0; B=1/1,1,0 |
| R2 부족 장애; R2-contract-2026-09-30; {"days":1,"demandMultiplier":2,"disruptionHub":"A","disruptionDays":1,"fixedCosts":[0,0]} | baseline A,B | 400.400302 | 600.266868 | 0.25 | 0.25 | 1.819392 | A=1/1,0,1; B=1/1,1,0 |
| R2 부족 장애; R2-contract-2026-09-30; {"days":1,"demandMultiplier":2,"disruptionHub":"A","disruptionDays":1,"fixedCosts":[0,0]} | optimized A,B | 400.400302 | 600.266868 | 0.25 | 0.25 | 1.819392 | A=1/1,0,1; B=1/1,1,0 |
| F2 전기간 장애; S2-contract-2026-09-30; {"disruptionHub":"F2","disruptionDays":7,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| F2 전기간 장애; S2-contract-2026-09-30; {"disruptionHub":"F2","disruptionDays":7,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 11214 | 0 | 0 | 0 | F1=0/8,0,0; F2=8/8,0,56; F3=0/8,0,0 |
| F2 2일 장애; S2-contract-2026-09-30; {"disruptionHub":"F2","disruptionDays":2,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| F2 2일 장애; S2-contract-2026-09-30; {"disruptionHub":"F2","disruptionDays":2,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 3224.674713 | 0.714286 | 0.714286 | 2.609975 | F1=0/8,0,0; F2=8/8,40,16; F3=0/8,0,0 |
| 반올림 0.49; R2-contract-2026-09-30; {"days":1,"demandMultiplier":0.49,"capacityMultiplier":0.49,"fixedCosts":[0,0]} | baseline A,B | 0 | 0 | 0 | 0 | 0 | A=0/0,0,0; B=0/0,0,0 |
| 반올림 0.49; R2-contract-2026-09-30; {"days":1,"demandMultiplier":0.49,"capacityMultiplier":0.49,"fixedCosts":[0,0]} | optimized  | 0 | 0 | 0 | 0 | 0 | A=0/0,0,0; B=0/0,0,0 |
| 반올림 0.5; R2-contract-2026-09-30; {"days":1,"demandMultiplier":0.5,"capacityMultiplier":0.5,"fixedCosts":[0,0]} | baseline A,B | 0.533736 | 0.533736 | 1 | 1 | 2.91854 | A=1/1,1,0; B=1/1,1,0 |
| 반올림 0.5; R2-contract-2026-09-30; {"days":1,"demandMultiplier":0.5,"capacityMultiplier":0.5,"fixedCosts":[0,0]} | optimized A,B | 0.533736 | 0.533736 | 1 | 1 | 2.91854 | A=1/1,1,0; B=1/1,1,0 |
| 반올림 1.5; R2-contract-2026-09-30; {"days":1,"demandMultiplier":1.5,"capacityMultiplier":1.5,"fixedCosts":[0,0]} | baseline A,B | 1.067471 | 1.067471 | 1 | 1 | 2.854295 | A=2/2,2,0; B=2/2,2,0 |
| 반올림 1.5; R2-contract-2026-09-30; {"days":1,"demandMultiplier":1.5,"capacityMultiplier":1.5,"fixedCosts":[0,0]} | optimized A,B | 1.067471 | 1.067471 | 1 | 1 | 2.854295 | A=2/2,2,0; B=2/2,2,0 |
| maxHubs1; S2-contract-2026-09-30; {"maxHubs":1,"fixedCosts":[2,2,2]} | baseline F1 | 5.202414 | 36.416897 | 1 | 1 | 2.699547 | F1=8/8,56,0; F2=0/8,0,0; F3=0/8,0,0 |
| maxHubs1; S2-contract-2026-09-30; {"maxHubs":1,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| maxHubs2; S2-contract-2026-09-30; {"maxHubs":2,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| maxHubs2; S2-contract-2026-09-30; {"maxHubs":2,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| maxHubs3; S2-contract-2026-09-30; {"maxHubs":3,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| maxHubs3; S2-contract-2026-09-30; {"maxHubs":3,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| capacityMultiplier0.8; S2-contract-2026-09-30; {"capacityMultiplier":0.8,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/6,28,0; F2=0/6,0,0; F3=4/6,28,0 |
| capacityMultiplier0.8; S2-contract-2026-09-30; {"capacityMultiplier":0.8,"fixedCosts":[2,2,2]} | optimized F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/6,28,0; F2=0/6,0,0; F3=4/6,28,0 |
| capacityMultiplier1; S2-contract-2026-09-30; {"capacityMultiplier":1,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| capacityMultiplier1; S2-contract-2026-09-30; {"capacityMultiplier":1,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| capacityMultiplier1.2; S2-contract-2026-09-30; {"capacityMultiplier":1.2,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/10,28,0; F2=0/10,0,0; F3=4/10,28,0 |
| capacityMultiplier1.2; S2-contract-2026-09-30; {"capacityMultiplier":1.2,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/10,0,0; F2=8/10,56,0; F3=0/10,0,0 |
| 수요0.8; S2-contract-2026-09-30; {"demandMultiplier":0.8,"fixedCosts":[2,2,2]} | baseline F1,F3 | 4.800603 | 33.604224 | 1 | 1 | 2.599619 | F1=3/8,21,0; F2=0/8,0,0; F3=3/8,21,0 |
| 수요0.8; S2-contract-2026-09-30; {"demandMultiplier":0.8,"fixedCosts":[2,2,2]} | optimized F2 | 3.601207 | 25.208449 | 1 | 1 | 2.621858 | F1=0/8,0,0; F2=6/8,42,0; F3=0/8,0,0 |
| 수요1; S2-contract-2026-09-30; {"demandMultiplier":1,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| 수요1; S2-contract-2026-09-30; {"demandMultiplier":1,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| 수요1.2; S2-contract-2026-09-30; {"demandMultiplier":1.2,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.334339 | 37.340374 | 1 | 1 | 2.569923 | F1=5/8,35,0; F2=0/8,0,0; F3=5/8,35,0 |
| 수요1.2; S2-contract-2026-09-30; {"demandMultiplier":1.2,"fixedCosts":[2,2,2]} | optimized F1,F3 | 5.334339 | 37.340374 | 1 | 1 | 2.569923 | F1=5/8,35,0; F2=0/8,0,0; F3=5/8,35,0 |
| 수요3; S2-contract-2026-09-30; {"demandMultiplier":3,"fixedCosts":[2,2,2]} | baseline F1,F3 | 1606.134943 | 11242.944598 | 0.666667 | 0.666667 | 2.55013 | F1=8/8,56,0; F2=0/8,0,0; F3=8/8,56,0 |
| 수요3; S2-contract-2026-09-30; {"demandMultiplier":3,"fixedCosts":[2,2,2]} | optimized F1,F3 | 1606.134943 | 11242.944598 | 0.666667 | 0.666667 | 2.55013 | F1=8/8,56,0; F2=0/8,0,0; F3=8/8,56,0 |
| 고정비0; S2-contract-2026-09-30; {"fixedCosts":[0,0,0]} | baseline F1,F3 | 1.067471 | 7.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| 고정비0; S2-contract-2026-09-30; {"fixedCosts":[0,0,0]} | optimized F1,F3 | 1.067471 | 7.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| 고정비2; S2-contract-2026-09-30; {"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| 고정비2; S2-contract-2026-09-30; {"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| 고정비20; S2-contract-2026-09-30; {"fixedCosts":[20,20,20]} | baseline F1,F3 | 41.067471 | 287.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| 고정비20; S2-contract-2026-09-30; {"fixedCosts":[20,20,20]} | optimized F2 | 22.134943 | 154.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| seed1; S2-contract-2026-09-30; {"seed":1,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.640105 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| seed1; S2-contract-2026-09-30; {"seed":1,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.662344 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| seed42; S2-contract-2026-09-30; {"seed":42,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.655069 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| seed42; S2-contract-2026-09-30; {"seed":42,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| seed2026; S2-contract-2026-09-30; {"seed":2026,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 35.472299 | 1 | 1 | 2.562294 | F1=4/8,28,0; F2=0/8,0,0; F3=4/8,28,0 |
| seed2026; S2-contract-2026-09-30; {"seed":2026,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.584533 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0 |
| L2 장거리; L2-long-distance-synthetic; {"fixedCosts":[0,0]} | baseline A,B | 288.21725 | 2017.520749 | 1 | 0.303571 | 8.637356 | A=8/8,56,0; B=0/8,0,0 |
| L2 장거리; L2-long-distance-synthetic; {"fixedCosts":[0,0]} | optimized A | 288.21725 | 2017.520749 | 1 | 0.303571 | 8.637356 | A=8/8,56,0; B=0/8,0,0 |
| 30일; S2-contract-2026-09-30; {"days":30,"fixedCosts":[2,2,2]} | baseline F1,F3 | 5.067471 | 152.024139 | 1 | 1 | 2.465157 | F1=4/8,120,0; F2=0/8,0,0; F3=4/8,120,0 |
| 30일; S2-contract-2026-09-30; {"days":30,"fixedCosts":[2,2,2]} | optimized F2 | 4.134943 | 124.048278 | 1 | 1 | 2.487396 | F1=0/8,0,0; F2=8/8,240,0; F3=0/8,0,0 |
| 배율상한; S2-contract-2026-09-30; {"demandMultiplier":3,"capacityMultiplier":3,"fixedCosts":[2,2,2]} | baseline F1,F3 | 7.202414 | 50.416897 | 1 | 1 | 2.523996 | F1=12/24,84,0; F2=0/24,0,0; F3=12/24,84,0 |
| 배율상한; S2-contract-2026-09-30; {"demandMultiplier":3,"capacityMultiplier":3,"fixedCosts":[2,2,2]} | optimized F1,F3 | 7.202414 | 50.416897 | 1 | 1 | 2.523996 | F1=12/24,84,0; F2=0/24,0,0; F3=12/24,84,0 |
| 5시설; S2-contract-2026-09-30; {"maxHubs":5,"fixedCosts":[2,2,2,2,2]} | baseline F1,F5 | 7.202414 | 50.416897 | 1 | 1 | 2.699547 | F1=8/8,56,0; F2=0/8,0,0; F3=0/8,0,0; F4=0/8,0,0; F5=0/8,0,0 |
| 5시설; S2-contract-2026-09-30; {"maxHubs":5,"fixedCosts":[2,2,2,2,2]} | optimized F2 | 4.134943 | 28.944598 | 1 | 1 | 2.677308 | F1=0/8,0,0; F2=8/8,56,0; F3=0/8,0,0; F4=0/8,0,0; F5=0/8,0,0 |
| 지역 정상; KR-regional-2026-09-30; {"fixedCosts":[160,130,120,150]} | baseline H1,H4 | 3543.138057 | 24801.9664 | 0.944444 | 0.944444 | 3.68208 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 정상; KR-regional-2026-09-30; {"fixedCosts":[160,130,120,150]} | optimized H1,H3 | 1684.385086 | 11790.695605 | 1 | 1 | 3.779211 | H1=80/90,560,0; H2=0/110,0,0; H3=100/100,700,0; H4=0/80,0,0 |
| 지역 수요증가; KR-regional-2026-09-30; {"demandMultiplier":1.2,"fixedCosts":[160,130,120,150]} | baseline H1,H4 | 10324.596007 | 72272.172049 | 0.787037 | 0.787037 | 3.281935 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 수요증가; KR-regional-2026-09-30; {"demandMultiplier":1.2,"fixedCosts":[160,130,120,150]} | optimized H2,H3 | 3625.146517 | 25376.025619 | 0.972222 | 0.972222 | 4.206306 | H1=0/90,0,0; H2=110/110,770,0; H3=100/100,700,0; H4=0/80,0,0 |
| 지역 용량축소; KR-regional-2026-09-30; {"capacityMultiplier":0.6,"fixedCosts":[160,130,120,150]} | baseline H1,H4 | 16174.01054 | 113218.073777 | 0.566667 | 0.566667 | 2.92113 | H1=54/54,378,0; H2=0/66,0,0; H3=0/60,0,0; H4=48/48,336,0 |
| 지역 용량축소; KR-regional-2026-09-30; {"capacityMultiplier":0.6,"fixedCosts":[160,130,120,150]} | optimized H2,H3 | 11989.687637 | 83927.81346 | 0.7 | 0.7 | 3.714415 | H1=0/54,0,0; H2=66/66,462,0; H3=60/60,420,0; H4=0/48,0,0 |
| 지역 거점수1; KR-regional-2026-09-30; {"maxHubs":1,"fixedCosts":[160,130,120,150]} | baseline H1 | 18706.212287 | 130943.486009 | 0.5 | 0.5 | 3.519292 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=0/80,0,0 |
| 지역 거점수1; KR-regional-2026-09-30; {"maxHubs":1,"fixedCosts":[160,130,120,150]} | optimized H2 | 15411.958143 | 107883.706999 | 0.611111 | 0.611111 | 4.422921 | H1=0/90,0,0; H2=110/110,770,0; H3=0/100,0,0; H4=0/80,0,0 |
| 지역 거점수4; KR-regional-2026-09-30; {"maxHubs":4,"fixedCosts":[160,130,120,150]} | baseline H1,H4 | 3543.138057 | 24801.9664 | 0.944444 | 0.944444 | 3.68208 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 거점수4; KR-regional-2026-09-30; {"maxHubs":4,"fixedCosts":[160,130,120,150]} | optimized H1,H2,H3,H4 | 1085.702797 | 7599.91958 | 1 | 1 | 2.965616 | H1=65/90,455,0; H2=50/110,350,0; H3=30/100,210,0; H4=35/80,245,0 |
| 지역 장애H2; KR-regional-2026-09-30; {"disruptionHub":"H2","disruptionDays":2,"fixedCosts":[160,130,120,150]} | baseline H1,H4 | 3543.138057 | 24801.9664 | 0.944444 | 0.944444 | 3.68208 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 장애H2; KR-regional-2026-09-30; {"disruptionHub":"H2","disruptionDays":2,"fixedCosts":[160,130,120,150]} | optimized H1,H3 | 1684.385086 | 11790.695605 | 1 | 1 | 3.779211 | H1=80/90,560,0; H2=0/110,0,0; H3=100/100,700,0; H4=0/80,0,0 |
| 지역 장애H3; KR-regional-2026-09-30; {"disruptionHub":"H3","disruptionDays":2,"fixedCosts":[160,130,120,150]} | baseline H1,H4 | 3543.138057 | 24801.9664 | 0.944444 | 0.944444 | 3.68208 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 장애H3; KR-regional-2026-09-30; {"disruptionHub":"H3","disruptionDays":2,"fixedCosts":[160,130,120,150]} | optimized H1,H3 | 1684.385086 | 49762.072311 | 0.84127 | 0.84127 | 3.714835 | H1=80/90,560,0; H2=0/110,0,0; H3=100/100,500,200; H4=0/80,0,0 |
| 지역 장애H1; KR-regional-2026-09-30; {"disruptionHub":"H1","disruptionDays":2,"fixedCosts":[160,130,120,150]} | baseline H1,H4 | 3543.138057 | 59709.541826 | 0.801587 | 0.801587 | 3.708973 | H1=90/90,450,180; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 장애H1; KR-regional-2026-09-30; {"disruptionHub":"H1","disruptionDays":2,"fixedCosts":[160,130,120,150]} | optimized H1,H3 | 1684.385086 | 43010.548725 | 0.873016 | 0.873016 | 3.842908 | H1=80/90,400,160; H2=0/110,0,0; H3=100/100,700,0; H4=0/80,0,0 |
| 지역 seed2026; KR-regional-2026-09-30; {"seed":2026,"fixedCosts":[160,130,120,150]} | baseline H1,H4 | 3543.138057 | 24801.9664 | 0.944444 | 0.943651 | 3.735512 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 seed2026; KR-regional-2026-09-30; {"seed":2026,"fixedCosts":[160,130,120,150]} | optimized H1,H3 | 1684.385086 | 11790.695605 | 1 | 1 | 3.830438 | H1=80/90,560,0; H2=0/110,0,0; H3=100/100,700,0; H4=0/80,0,0 |
| 지역 고정비0; KR-regional-2026-09-30; {"fixedCosts":[0,0,0,0]} | baseline H1,H4 | 3233.138057 | 22631.9664 | 0.944444 | 0.944444 | 3.68208 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 고정비0; KR-regional-2026-09-30; {"fixedCosts":[0,0,0,0]} | optimized H1,H3 | 1404.385086 | 9830.695605 | 1 | 1 | 3.779211 | H1=80/90,560,0; H2=0/110,0,0; H3=100/100,700,0; H4=0/80,0,0 |
| 지역 고정비10배; KR-regional-2026-09-30; {"fixedCosts":[1600,1300,1200,1500]} | baseline H1,H4 | 6333.138057 | 44331.9664 | 0.944444 | 0.944444 | 3.68208 | H1=90/90,630,0; H2=0/110,0,0; H3=0/100,0,0; H4=80/80,560,0 |
| 지역 고정비10배; KR-regional-2026-09-30; {"fixedCosts":[1600,1300,1200,1500]} | optimized H1,H3 | 4204.385086 | 29430.695605 | 1 | 1 | 3.779211 | H1=80/90,560,0; H2=0/110,0,0; H3=100/100,700,0; H4=0/80,0,0 |

납기 사후 평가: [{"hours":4,"baseline":0.596031746031746,"optimized":0.5817460317460318},{"hours":6,"baseline":0.8642857142857143,"optimized":0.919047619047619},{"hours":8,"baseline":0.9444444444444444,"optimized":1}]
